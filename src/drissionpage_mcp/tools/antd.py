"""Ant Design portal 弹层专用工具。

AntD 的下拉浮层、日期选择器、弹窗、message/notification 气泡均通过
portal 渲染到所在文档的 body 末尾。交互一律使用 Actions 真实鼠标事件，
保证 onChange 等事件链真实触发。
"""

from __future__ import annotations

import json
import re
import time

from fastmcp.exceptions import ToolError

from ..manager import has_box, manager, normalize_locator, real_click
from ..models import MessageMatchResult, MessageResult, ToastResult
from ..overlays import arm_overlays, drain_overlays
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("AntD Portal")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_search_root(tab, frame: str | None = None, element_id: str | None = None):
    """确定 portal 浮层的搜索容器：优先用元素所在文档，其次 frame 参数。"""
    if element_id:
        record = manager.get_record(element_id)
        if record is not None and record.container is not None:
            return record.container
    return manager.resolve_frame(tab, frame) if frame else tab


# 消息浮层家族：(选择器, source, 是否按类名解析 level)
_MESSAGE_SELECTORS = (
    ("css:.ant-message-notice", "message", True),
    ("css:.ant-notification-notice", "notification", True),
    ("css:.layui-layer-msg .layui-layer-content", "layer", False),
)


def _message_containers(tab, frame: str | None = None) -> list:
    """确定消息浮层的探测文档（即时解析，不做 frame 重建重试）。

    frame 指定时=该文档+主文档兜底；未指定时=激活功能模块 iframe+主文档
    （portal 气泡渲染在触发它的那个文档里）。
    """
    if frame:
        try:
            root = manager.resolve_frame(tab, frame)
        except Exception:
            return [tab]
        return [root, tab] if root is not tab else [root]
    containers = []
    try:
        containers.append(manager.resolve_frame(tab, "active"))
    except Exception:
        pass
    containers.append(tab)
    return containers


def _level_of(node) -> str:
    cls = (node.attr("class") or "").lower()
    if "success" in cls:
        return "success"
    if "error" in cls:
        return "error"
    if "warn" in cls:
        return "warning"
    return "info"


# 页面内一次采集全部消息浮层（替代逐选择器检索 + 逐节点状态查询的多轮 CDP）
_COLLECT_JS = r"""
var nodes = document.querySelectorAll('.ant-message-notice, .ant-notification-notice, .layui-layer-msg .layui-layer-content');
var out = [];
for (var i = 0; i < nodes.length; i++) {
  var n = nodes[i];
  var r = n.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) continue;
  var txt = String(n.textContent || '').replace(/\s+/g, ' ').trim();
  if (!txt) continue;
  var cls = String(n.className || '').toLowerCase();
  var source = cls.indexOf('ant-notification-notice') >= 0 ? 'notification'
             : cls.indexOf('layui-layer-content') >= 0 ? 'layer'
             : 'message';
  var level = null;
  if (source !== 'layer') {
    var host = n.closest('.ant-message-notice, .ant-notification-notice') || n;
    var hcls = String(host.className || '').toLowerCase();
    level = hcls.indexOf('success') >= 0 ? 'success'
          : hcls.indexOf('error') >= 0 ? 'error'
          : hcls.indexOf('warn') >= 0 ? 'warning' : 'info';
  }
  out.push({ text: txt.slice(0, 200), source: source, level: level });
}
return JSON.stringify(out);
"""


def _collect_in(container) -> list[tuple[str, str, str | None]] | None:
    """一次 run_js 采集容器内消息；脚本不可用/不可解析时返回 None 以回退。"""
    try:
        raw = container.run_js(_COLLECT_JS)
        items = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(items, list):
        return None
    out: list[tuple[str, str, str | None]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        out.append((text[:200], str(it.get("source") or "message"), it.get("level")))
    return out


def _collect_by_dp(container) -> list[tuple[str, str, str | None]]:
    """回退路径：逐选择器 DP 检索（脚本采集不可用的环境）。"""
    results: list[tuple[str, str, str | None]] = []
    for locator, source, has_level in _MESSAGE_SELECTORS:
        try:
            nodes = container.eles(locator, timeout=0)
        except Exception:
            continue
        for n in nodes or []:
            try:
                if not getattr(n.states, "is_displayed", True):
                    continue
                txt = (n.text or "").strip()
                if not txt:
                    continue
                results.append((txt[:200], source, _level_of(n) if has_level else None))
            except Exception:
                continue
    return results


def _collect_containers(containers: list) -> list[tuple[str, str, str | None]]:
    results: list[tuple[str, str, str | None]] = []
    for container in containers:
        items = _collect_in(container)
        if items is None:
            items = _collect_by_dp(container)
        results.extend(items)
    return results


def _collect_messages(tab, frame: str | None = None) -> list[tuple[str, str, str | None]]:
    """收集当前可见的全局消息项：[(text, source, level), ...]

    只做即时 DOM 探测：浮层不存在是合法结果，绝不空等。
    优先在页面内一次 run_js 采集（每轮 1 次 CDP），不支持时回退逐选择器检索。
    """
    return _collect_containers(_message_containers(tab, frame))


@mcp.tool(
    tags={"antd", "assert"},
    annotations={"title": "读取操作提示气泡", "readOnlyHint": True},
)
def get_toasts(tab_id: str | None = None, frame: str | None = None) -> ToastResult:
    """读取当前正在显示的 AntD message 全局提示与 notification 通知内容。

    纯即时快照：没有气泡时立刻返回空列表，不做任何等待。需要等待/断言某条消息
    出现（如保存后等 "保存成功"）请用 wait_message（可传 pattern 与 timeout）。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        frame: 搜索范围（'active'=激活态 iframe，见 frame_list）；省略时=激活模块+主文档
    """
    tab, _ = manager.get_tab(tab_id)
    entries = _collect_messages(tab, frame)
    return ToastResult(
        message_texts=[t for t, src, _ in entries if src in ("message", "layer")],
        notification_texts=[t for t, src, _ in entries if src == "notification"],
    )


@mcp.tool(
    tags={"antd", "assert"},
    annotations={"title": "等待操作提示气泡或通知", "readOnlyHint": True},
)
def wait_message(
    pattern: str = r".+",
    timeout: float = 5.0,
    tab_id: str | None = None,
    frame: str | None = None,
    raise_if_not_found: bool = True,
) -> MessageMatchResult:
    """轮询等待全局操作结果气泡或通知出现并断言内容（如 '保存成功'、'操作失败'）。

    支持 AntD Message 全局提示气泡、Notification 通知卡片、Modal 提示及旧版
    layui-layer 弹窗消息。捕获到符合正则的内容后立即返回（无须多余等待）。

    Args:
        pattern: 正则表达式或关键字，如 "保存成功"、"成功|完成"、"失败|错误"（默认 r".+" 捕获任意消息）
        timeout: 等待的最长秒数（默认 5.0 秒）
        tab_id: 标签页 id，省略时用最新标签页
        frame: 搜索范围（'active'=激活态 iframe，默认优先当前激活模块并穿透）
        raise_if_not_found: 超时未匹配时是否抛出 ToolError（默认 True；False 则返回 found=False 的结果模型）
    """
    clean_pat = (pattern or "").strip() or r".+"
    try:
        regex = re.compile(clean_pat, re.IGNORECASE)
    except re.error as exc:
        raise ToolError(f"非法的正则表达式: {pattern!r} ({exc})") from exc

    tab, _ = manager.get_tab(tab_id)
    # 轮询期间容器固定，只解析一次激活 iframe（原实现每轮都重解析，约 1+N 次 CDP）
    containers = _message_containers(tab, frame)
    deadline = time.time() + max(timeout, 0.5)
    start_time = time.time()
    seen_messages: list[str] = []

    while time.time() < deadline:
        entries = _collect_containers(containers)
        for msg_text, source, level in entries:
            if msg_text not in seen_messages:
                seen_messages.append(msg_text)
            if regex.search(msg_text):
                return MessageMatchResult(
                    found=True,
                    pattern=pattern,
                    matched_text=msg_text,
                    source=source,
                    level=level,
                    elapsed_seconds=round(time.time() - start_time, 2),
                    all_messages=seen_messages,
                )
        time.sleep(0.2)

    elapsed = round(time.time() - start_time, 2)
    if raise_if_not_found:
        captured_str = "、".join(repr(m) for m in seen_messages) or "（未捕捉到任何消息气泡）"
        raise ToolError(
            f"在 {elapsed}s 内未等到匹配 [{pattern}] 的操作结果气泡。当前捕获到的消息: {captured_str}"
        )

    return MessageMatchResult(
        found=False,
        pattern=pattern,
        matched_text=None,
        source=None,
        level=None,
        elapsed_seconds=elapsed,
        all_messages=seen_messages,
    )


def _open_dropdown(tab, ele, root, dd_loc: str, timeout: float):
    """确保 Select 浮层展开并返回（可见的）浮层元素及其容器。

    页面里可能残留无 hidden 类但视觉隐藏的浮层（如列选择器），
    用盒模型可见性过滤，找不到可见浮层时先真实点击再检索。
    """
    found, container = manager.search(tab, dd_loc, many=True, timeout=1, frame_obj=root)
    visible = [d for d in (found or []) if has_box(d)]
    if not visible:
        real_click(tab, ele, root)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found, container = manager.search(tab, dd_loc, many=True, timeout=1, frame_obj=root)
            visible = [d for d in (found or []) if has_box(d)]
            if visible:
                break
            time.sleep(0.2)
    return (visible[0], container) if visible else (None, container)


@mcp.tool(
    tags={"antd", "interaction"},
    annotations={"title": "选择下拉选项", "readOnlyHint": False},
)
def antd_select(
    element_id: str,
    option_text: str,
    tab_id: str | None = None,
    timeout: float = 5,
    exact: bool = False,
    close_multi: bool = True,
) -> dict:
    """操作 AntD 下拉选择框（Select）：真实点击展开，在 portal 浮层中点击匹配选项。

    若为多选下拉框（Select[multiple]），选中后自动派发 ESC 键收起浮层，防止遮挡后续按钮或表单。

    Args:
        element_id: Select 输入框元素的 element_id（find_element 定位 '.ant-select' 等）
        option_text: 选项文本（匹配 .ant-select-item-option，默认包含匹配，exact=True 精确匹配）
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 等待下拉浮层出现的秒数
        exact: 是否精确匹配选项文本
        close_multi: 多选下拉框选中后是否自动按 ESC 键收起浮层（默认 True，彻底避免遮挡后续操作按钮）
    """
    ele = manager.get_element(element_id)
    tab, _ = manager.get_tab(tab_id)
    root = _resolve_search_root(tab, None, element_id)

    dd_loc = ".ant-select-dropdown:not(.ant-select-dropdown-hidden)"
    dropdown, dd_root = _open_dropdown(tab, ele, root, dd_loc, timeout)
    if dropdown is None:
        raise ToolError("下拉浮层未出现，请确认元素是 AntD Select 且可展开")

    # 兼容两代 AntD/rc-select 选项类名
    options = []
    for opt_loc in ("css:.ant-select-item-option",
                    "css:.ant-select-dropdown-menu-item"):
        try:
            found = dropdown.eles(normalize_locator(opt_loc), timeout=2)
            options.extend(found or [])
        except Exception:
            continue
    options = [o for o in options if (o.text or "").strip()]
    target = None
    for opt in options:
        t = (opt.text or "").strip()
        if (exact and t == option_text) or (not exact and option_text in t):
            target = opt
            break
    if target is None:
        shown = [((o.text or "").strip()[:30]) for o in (options or [])][:10]
        raise ToolError(f"未找到选项 {option_text!r}，当前可见选项: {shown}")

    arm_overlays(root)
    real_click(tab, target, root)
    resp = {"ok": True, "message": f"已选择下拉选项: {option_text!r}"}
    time.sleep(0.35)

    # 核心保障：多选下拉框（或选项点击后浮层仍未关闭），自动派发 ESC 键收起浮层，防止遮挡下一步按钮或交互
    if close_multi:
        try:
            ele_cls = ele.attr("class") or ""
            dd_cls = dropdown.attr("class") or ""
            is_multi = "multiple" in ele_cls or "multiple" in dd_cls
            still_open = dropdown.states.is_displayed if hasattr(dropdown, "states") else False
            if is_multi or still_open:
                actions = root.actions if hasattr(root, "actions") else tab.actions
                actions.key_down("ESCAPE")
                time.sleep(0.05)
                actions.key_up("ESCAPE")
                time.sleep(0.15)
        except Exception:
            pass

    overlays = drain_overlays(root)
    if overlays:
        resp["overlays"] = overlays
    return resp


@mcp.tool(
    tags={"antd", "interaction"},
    annotations={"title": "选择日期", "readOnlyHint": False},
)
def antd_date_pick(
    element_id: str,
    date: str,
    tab_id: str | None = None,
    timeout: float = 5,
) -> MessageResult:
    """操作 AntD 日期选择器（DatePicker）：真实点击输入框，在 portal 日历中点击目标日期。

    Args:
        element_id: 日期输入框元素的 element_id（find_element 定位 '.ant-picker' 等）
        date: 目标日期，格式 YYYY-MM-DD
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 等待日历浮层出现的秒数
    """
    if not DATE_RE.match(date):
        raise ToolError(f"日期格式应为 YYYY-MM-DD，收到: {date!r}")
    ele = manager.get_element(element_id)
    tab, _ = manager.get_tab(tab_id)
    root = _resolve_search_root(tab, None, element_id)

    picker, pk_root, cell_sel, ok_sel = None, None, None, None
    for dd_loc, cell_fmt, ok_loc in (
        (".ant-picker-dropdown:not(.ant-picker-dropdown-hidden)",
         'css:.ant-picker-cell[title="{d}"]', "css:.ant-picker-ok button"),
        (".ant-calendar-dropdown:not(.ant-calendar-dropdown-hidden)",
         'css:.ant-calendar-cell[title="{d}"]', "css:.ant-calendar-ok-btn"),
        (".ant-calendar-picker-container:not(.ant-calendar-picker-container-hidden)",
         'css:.ant-calendar-cell[title="{d}"]', "css:.ant-calendar-ok-btn"),
    ):
        open_pk, _ = manager.search(tab, dd_loc, timeout=1, frame_obj=root)
        if not open_pk:
            real_click(tab, ele, root)
        picker, pk_root = manager.search(tab, dd_loc, timeout=timeout, frame_obj=root)
        if picker:
            cell_sel, ok_sel = cell_fmt, ok_loc
            break
    if not picker:
        raise ToolError("日历浮层未出现，请确认元素是 AntD DatePicker 且当前面板包含目标月份")

    cell = picker.ele(cell_sel.format(d=date), timeout=3)
    if not cell:
        raise ToolError(
            f"当前日历面板中无 {date}，可能需先切换月份（点击日历头部的上/下月按钮）"
        )
    real_click(tab, cell, pk_root)

    # 带时间选择的面板需要点确定
    ok_btn = picker.ele(ok_sel, timeout=1)
    if ok_btn:
        real_click(tab, ok_btn, pk_root)
    return MessageResult(ok=True, message=f"已选择日期: {date}")


@mcp.tool(
    tags={"antd", "interaction"},
    annotations={"title": "点击弹窗按钮", "readOnlyHint": False},
)
def antd_modal_click(
    button_text: str,
    tab_id: str | None = None,
    frame: str | None = None,
    timeout: float = 5,
    exact: bool = False,
) -> MessageResult:
    """点击当前可见 AntD 弹窗（Modal.confirm / Modal）中的按钮，如"确 定""取 消""确 定删除"。

    自动寻找可见的 .ant-modal，在其 footer 或 confirm 区域真实点击匹配按钮。

    Args:
        button_text: 按钮文本（默认包含匹配，exact=True 精确匹配）
        tab_id: 标签页 id，省略时用最新标签页
        frame: 搜索范围（'active'=激活态 iframe）
        timeout: 等待弹窗出现的秒数
        exact: 是否精确匹配按钮文本
    """
    tab, _ = manager.get_tab(tab_id)

    modal = None
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        found, root = manager.search(
            tab, ".ant-modal", many=True, timeout=1, frame=frame
        )
        visible = []
        for m in found or []:
            try:
                if m.states.is_displayed and has_box(m):
                    visible.append(m)
            except Exception:
                continue
        if visible:
            # 多层弹窗取最后挂载（最顶层）的那个
            modal = visible[-1]
            break
        time.sleep(0.2)
    if modal is None:
        raise ToolError(f"等待 {timeout} 秒内未出现可见的 AntD 弹窗")

    buttons = modal.eles("css:.ant-modal-footer button", timeout=1) or modal.eles(
        "css:.ant-modal-confirm-btns button", timeout=1
    )
    if not buttons:
        # 定制弹窗可能没有标准 footer，退回弹窗内全部按钮
        buttons = modal.eles("css:button", timeout=2)
    target = None
    for b in buttons or []:
        t = (b.text or "").strip().replace(" ", "")
        want = button_text.replace(" ", "")
        if (exact and t == want) or (not exact and want in t):
            target = b
            break
    if target is None:
        shown = [((b.text or "").strip()[:20]) for b in (buttons or [])]
        raise ToolError(f"弹窗中未找到按钮 {button_text!r}，当前按钮: {shown}")

    real_click(tab, target, root)
    return MessageResult(ok=True, message=f"已点击弹窗按钮: {button_text!r}")
