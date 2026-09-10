"""浏览器网络数据包监控工具（DrissionPage 5.0 listen 数据监听）。

对齐官方 5.0 监听语义：
- `listen.start()` / `listen.set_targets()` 只负责 url 特征；
  `method` 与 `resourceType` 改用 `listen.set_method` / `listen.set_res_type` 独立设置
  （5.0 起从 start 参数中移除），支持 `all()` / `GET(only=True)` / `remove_X()` 链式写法。
- 命中数据包进入**队列**：`wait()` 是逐条出队，同一数据包不会被重复读到；
  因此必须「先 start，再做动作」，动作之后再用 wait / snapshot 取包。
- `snapshot()` 是纯即时快照（出队，不等待）：队列为空立刻返回空列表，绝不空等；
  要等包到达用 `net_listen_wait`，要等网络整体安静用 `net_listen_wait_silent`。
- 无超时的等待会让 MCP 调用永久挂起，故本模块一律要求有限超时（timeout>0）。

典型用法（断言接口载荷，实测用于 APS 审批人配置 type 错位类缺陷）：

    net_listen_start(urls="approverOptions")        # 1. 先开监听（clear 队列）
    antd_select(element_id=..., option_text="按部门审批")   # 2. 触发前端请求
    net_listen_wait(timeout=10)                      # 3. 取包：url 自带 ?type=dept
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..manager import manager
from ..models import NetIdleResult, NetListenState, NetPackets, PacketInfo

mcp = FastMCP("Network Listen")

METHODS: tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "TRACE",
    "CONNECT",
)

RES_TYPES: tuple[str, ...] = (
    "Document",
    "Stylesheet",
    "Image",
    "Media",
    "Font",
    "Script",
    "TextTrack",
    "XHR",
    "Fetch",
    "Prefetch",
    "EventSource",
    "WebSocket",
    "Manifest",
    "SignedExchange",
    "Ping",
    "CSPViolationReport",
    "Preflight",
    "Other",
)

BODY_LIMIT = 4000
HEADER_LIMIT = 40
TRUNCATED = "…(已截断)"

# ---------- DrissionPage 5.0 监听器私有属性适配层 ----------
# 5.0.0b1 的 listen 只公开 start/set_targets/wait/pause 等方法；队列与「当前生效的
# 监听目标」只能从私有属性读取。把这类访问集中到 _LISTEN_ATTRS/_listen_attr，
# 上游一旦提供公开 API 只需改这一处（beta 升级时最先断的通常就是这些私有属性）。


def _listener(tab):
    listen = getattr(tab, "listen", None)
    if listen is None:
        raise ToolError("当前 DrissionPage 版本无 listen 监听器（需 5.0+）")
    return listen


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _is_all(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "all"


def _canonical(names: Any, allowed: tuple[str, ...], kind: str) -> list[str]:
    table = {name.lower(): name for name in allowed}
    result: list[str] = []
    for raw in _as_list(names):
        key = raw.strip().lower()
        if key not in table:
            raise ToolError(f"不支持的 {kind}: '{raw}'，可选值: {list(allowed)}")
        result.append(table[key])
    return result


def _apply_targets(listen, urls: Any, is_regex: bool) -> None:
    """设置 url 目标；urls 为 None/True/'all' 时监听全部。"""
    if urls is None or _is_all(urls):
        listen.set_targets(True, is_regex)
    else:
        listen.set_targets(_as_list(urls), is_regex)


def _apply_method(listen, method: Any) -> None:
    setter = listen.set_method
    if _is_all(method):
        setter.all()
        return
    wanted = set(_canonical(method, METHODS, "请求方法"))
    setter.all()
    for name in METHODS:
        if name not in wanted:
            getattr(setter, f"remove_{name}")()


def _apply_res_type(listen, res_type: Any) -> None:
    setter = listen.set_res_type
    if _is_all(res_type):
        setter.all()
        return
    wanted = set(_canonical(res_type, RES_TYPES, "ResourceType"))
    setter.all()
    for name in RES_TYPES:
        if name not in wanted:
            getattr(setter, f"remove_{name}")()


_LISTEN_ATTRS: dict[str, str] = {
    "targets": "_urls",
    "methods": "_method",
    "res_types": "_res_type",
    "queue": "_caught",
}


def _listen_attr(listen: Any, key: str, default: Any = None) -> Any:
    return getattr(listen, _LISTEN_ATTRS[key], default)


def _echo(listen: Any, key: str) -> list[str] | None:
    """回显监听目标；None 表示监听全部（DrissionPage 内部用 True 表示 all）。"""
    value = _listen_attr(listen, key, True)
    if value is True or value is None:
        return None
    try:
        return sorted(str(v) for v in value)
    except TypeError:
        return None


def _pending(listen) -> int | None:
    queue = _listen_attr(listen, "queue")
    if queue is None:
        return None
    try:
        return queue.qsize()
    except Exception:
        return None


def _state(tab, listen, note: str | None = None) -> NetListenState:
    return NetListenState(
        tab_id=str(getattr(tab, "tab_id", "") or ""),
        listening=bool(getattr(listen, "listening", False)),
        targets=_echo(listen, "targets"),
        methods=_echo(listen, "methods"),
        res_types=_echo(listen, "res_types"),
        pending=_pending(listen),
        note=note,
    )


def _safe(getter, default: Any = None) -> Any:
    try:
        return getter()
    except Exception:
        return default


def _clip_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value[:BODY_LIMIT] + TRUNCATED if len(value) > BODY_LIMIT else value


def _clip_body(value: Any) -> Any:
    """把响应体整理成可 JSON 传输的形状，超长截断。"""
    if value is None or value is False:
        return None
    if isinstance(value, bytes):
        return f"<bytes {len(value)}>"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > BODY_LIMIT:
            return text[:BODY_LIMIT] + TRUNCATED
        return value
    return _clip_text(value)


def _clip_headers(headers: Any) -> dict | None:
    if not headers:
        return None
    try:
        items = list(dict(headers).items())[:HEADER_LIMIT]
    except Exception:
        return None
    return {str(k): _clip_text(str(v)) for k, v in items}


def _packet_info(packet, index: int, include_body: bool, include_headers: bool) -> PacketInfo:
    kind = {
        "WebSocketPacket": "ws",
        "SSEPacket": "sse",
    }.get(getattr(packet, "type", ""), "request")

    info = PacketInfo(
        index=index,
        kind=kind,
        tab_id=_safe(lambda: packet.tab_id),
        method=_safe(lambda: packet.method),
        url=_safe(lambda: packet.url),
        failed=bool(_safe(lambda: packet.is_failed, False)),
        resource_type=_safe(lambda: packet.resourceType),
    )

    if kind == "ws":
        info.direction = "sent" if _safe(lambda: packet.is_sent, False) else "received"
        if include_body:
            info.body = _clip_body(_safe(lambda: packet.data))
        return info

    if kind == "sse":
        if include_body:
            info.body = _clip_body(_safe(lambda: packet.data))
        return info

    info.status = _safe(lambda: packet.response.status)
    info.fail_reason = _safe(lambda: packet.fail_info.errorText)
    info.params = _safe(lambda: packet.request.params)
    info.post_data = _clip_body(_safe(lambda: packet.request.postData))
    if include_body:
        info.body = _clip_body(_safe(lambda: packet.response.body))
    if include_headers:
        info.headers = _clip_headers(_safe(lambda: packet.response.headers))
    return info


def _to_packets(result: Any, include_body: bool, include_headers: bool) -> list[PacketInfo]:
    if not result:
        return []
    packets = result if isinstance(result, list) else [result]
    return [
        _packet_info(p, i, include_body, include_headers)
        for i, p in enumerate(packets, start=1)
    ]


def _drain(listen) -> list:
    """即时出队：不等待、不重试；没有排队数据包就返回空列表。"""
    queue = _listen_attr(listen, "queue")
    if queue is None:
        return []
    try:
        size = queue.qsize()
    except Exception:
        return []
    packets = []
    for _ in range(size):
        try:
            packets.append(queue.get_nowait())
        except Exception:
            break
    return packets


def _require_timeout(timeout: float | None) -> float:
    if timeout is None:
        raise ToolError("必须提供有限超时（DrissionPage 的无限等待会挂死 MCP 调用）")
    if timeout <= 0:
        raise ToolError("timeout 必须大于 0")
    return float(timeout)


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "开始监听网络请求", "readOnlyHint": False},
)
def net_listen_start(
    urls: str | list[str] | None = None,
    is_regex: bool = False,
    method: str | list[str] | None = None,
    res_type: str | list[str] | None = None,
    tab_id: str | None = None,
) -> NetListenState:
    """开始监听标签页的网络数据包（含同页 iframe 内的跨域请求），并清空历史队列。

    【顺序规范】必须先启动监听，再执行触发请求的 UI 动作；start 之前产生的数据包取不到。

    Args:
        urls: url 特征，可传字符串或列表（如 "approverOptions"、["api/order", "api/stock"]）；
              None / "all" / True 表示监听全部 url
        is_regex: urls 是否按正则解释（默认 False，按包含匹配）
        method: 只监听这些请求方法（如 "POST"、["GET","POST"]）；None=默认 GET/POST，"all"=全部方法
        res_type: 只监听这些 ResourceType（如 "XHR"、"Fetch"、"WebSocket"）；None=全部类型
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    if method is not None:
        _apply_method(listen, method)
    if res_type is not None:
        _apply_res_type(listen, res_type)
    targets: Any = True if (urls is None or _is_all(urls)) else _as_list(urls)
    listen.start(urls=targets, is_regex=is_regex)
    return _state(tab, listen)


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "等待网络数据包", "readOnlyHint": True},
)
def net_listen_wait(
    count: int = 1,
    timeout: float = 10.0,
    fit_count: bool = True,
    include_body: bool = False,
    include_headers: bool = False,
    tab_id: str | None = None,
) -> NetPackets:
    """等待符合监听条件的网络数据包到达（逐条出队，取过的包不会再次返回）。

    Args:
        count: 需要等待的数据包数量
        timeout: 最长等待秒数（必须 > 0；不支持无限等待）
        fit_count: 超时未凑满 count 时，True=视为失败返回 found=False，
                   False=返回已捕捉到的部分数据包（found=False 但 packets 非空）
        include_body: 是否附带响应体（json 自动转 dict，超长截断）
        include_headers: 是否附带响应头
        tab_id: 标签页 id，省略时用最新标签页
    """
    if count < 1:
        raise ToolError("count 必须大于等于 1")
    timeout = _require_timeout(timeout)
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)

    started = time.time()
    result = listen.wait(count=count, timeout=timeout, fit_count=fit_count, raise_err=False)
    elapsed = round(time.time() - started, 3)

    if result is False:
        return NetPackets(
            found=False,
            elapsed=elapsed,
            pending=_pending(listen),
            note=f"{timeout}s 内未凑满 {count} 个数据包",
        )

    packets = _to_packets(result, include_body, include_headers)
    if isinstance(result, list) and len(result) < count:
        return NetPackets(
            found=False,
            count=len(packets),
            elapsed=elapsed,
            packets=packets,
            pending=_pending(listen),
            note=f"超时前仅捕捉到 {len(packets)}/{count} 个数据包（fit_count=False）",
        )
    return NetPackets(
        found=True,
        count=len(packets),
        elapsed=elapsed,
        packets=packets,
        pending=_pending(listen),
    )


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "读取已排队数据包", "readOnlyHint": True},
)
def net_listen_snapshot(
    include_body: bool = False,
    include_headers: bool = False,
    tab_id: str | None = None,
) -> NetPackets:
    """即时读取当前已排队的网络数据包（纯快照，绝不空等）。

    队列为空时立刻返回空列表；需要等待数据包到达请用 net_listen_wait。
    读取即出队：同一数据包不会被重复返回。

    Args:
        include_body: 是否附带响应体（json 自动转 dict，超长截断）
        include_headers: 是否附带响应头
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    started = time.time()
    packets = _to_packets(_drain(listen), include_body, include_headers)
    note = None
    if not getattr(listen, "listening", False):
        note = "监听未启动（先调用 net_listen_start 再做触发动作）"
    return NetPackets(
        found=bool(packets),
        count=len(packets),
        elapsed=round(time.time() - started, 3),
        packets=packets,
        pending=_pending(listen),
        note=note,
    )


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "等待网络静默", "readOnlyHint": True},
)
def net_listen_wait_silent(
    timeout: float = 10.0,
    targets_only: bool = False,
    limit: int = 0,
    tab_id: str | None = None,
) -> NetIdleResult:
    """等待在途请求全部结束（网络静默），适合「点击后等页面加载完再断言」。

    Args:
        timeout: 最长等待秒数（必须 > 0）
        targets_only: True=只等待符合监听目标的请求结束，False=所有请求
        limit: 允许剩余的在途连接数（0=完全静默）
        tab_id: 标签页 id，省略时用最新标签页
    """
    timeout = _require_timeout(timeout)
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    if not getattr(listen, "listening", False):
        raise ToolError("监听未启动：先 net_listen_start 才能等待网络静默")

    started = time.time()
    quiet = bool(
        listen.wait_silent(timeout=timeout, targets_only=targets_only, limit=limit)
    )
    elapsed = round(time.time() - started, 3)
    return NetIdleResult(
        quiet=quiet,
        elapsed=elapsed,
        pending=_pending(listen),
        note=None if quiet else f"{timeout}s 内仍有在途请求未结束",
    )


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "暂停监听", "readOnlyHint": False},
)
def net_listen_pause(clear: bool = True, tab_id: str | None = None) -> NetListenState:
    """暂停监听（保留 url/method/res_type 设置，可随时 resume）。

    Args:
        clear: 是否同时清空已排队未取走的数据包（默认 True）
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    listen.pause(clear=clear)
    return _state(tab, listen)


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "恢复监听", "readOnlyHint": False},
)
def net_listen_resume(tab_id: str | None = None) -> NetListenState:
    """恢复被 net_listen_pause 暂停的监听（沿用原监听目标）。"""
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    listen.resume()
    return _state(tab, listen)


@mcp.tool(
    tags={"net", "listener"},
    annotations={"title": "停止监听", "readOnlyHint": False},
)
def net_listen_stop(tab_id: str | None = None) -> NetListenState:
    """停止监听并关闭 Network 域（清空队列，保留 url/method/res_type 设置）。

    长期挂着 Network 监听会持续累积内存，用例结束应及时停止。
    """
    tab, _ = manager.get_tab(tab_id)
    listen = _listener(tab)
    listen.stop()
    return _state(tab, listen)
