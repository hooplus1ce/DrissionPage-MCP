"""导航与页面信息工具。"""

from __future__ import annotations

from ..manager import manager, normalize_locator, prepare_locator
from ..models import HtmlResult, MessageResult, NavInfo, PageInfo, WaitResult
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Navigation")


def _nav_info(nav, url: str | None = None) -> NavInfo:
    ok = bool(getattr(nav, "ok", False))
    status = getattr(nav, "status", None)
    return NavInfo(
        url=url if url is not None else getattr(nav, "url", None),
        status=status,
        ok=ok,
        message=None if ok else f"导航未成功，status={status}",
    )


@mcp.tool(
    tags={"navigate"},
    annotations={"title": "打开网址", "readOnlyHint": False},
)
def navigate(url: str, tab_id: str | None = None, timeout: float | None = None) -> NavInfo:
    """在标签页中打开网址，返回导航结果（url/status/ok）。

    Args:
        url: 目标网址
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 页面加载超时秒数，省略时用浏览器默认（30 秒）
    """
    tab, _ = manager.get_tab(tab_id)
    nav = tab.get(url, timeout=timeout)
    return _nav_info(nav)


@mcp.tool(
    tags={"navigate"},
    annotations={"title": "后退", "readOnlyHint": False},
)
def navigate_back(tab_id: str | None = None, steps: int = 1) -> MessageResult:
    """在标签页中执行浏览器后退。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        steps: 后退步数
    """
    tab, _ = manager.get_tab(tab_id)
    tab.back(steps)
    return MessageResult(ok=True, message=f"已后退 {steps} 步，当前页面: {tab.url}")


@mcp.tool(
    tags={"navigate"},
    annotations={"title": "前进", "readOnlyHint": False},
)
def navigate_forward(tab_id: str | None = None, steps: int = 1) -> MessageResult:
    """在标签页中执行浏览器前进。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        steps: 前进步数
    """
    tab, _ = manager.get_tab(tab_id)
    tab.forward(steps)
    return MessageResult(ok=True, message=f"已前进 {steps} 步，当前页面: {tab.url}")


@mcp.tool(
    tags={"navigate"},
    annotations={"title": "刷新页面", "readOnlyHint": False},
)
def refresh(tab_id: str | None = None, ignore_cache: bool = False) -> MessageResult:
    """刷新标签页当前页面。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        ignore_cache: 是否忽略缓存强制刷新
    """
    tab, _ = manager.get_tab(tab_id)
    tab.refresh(ignore_cache=ignore_cache)
    return MessageResult(ok=True, message=f"已刷新页面: {tab.url}")


@mcp.tool(
    tags={"element", "wait"},
    annotations={"title": "等待元素", "readOnlyHint": True},
)
def wait_element(
    locator: str, tab_id: str | None = None, timeout: float = 10, frame: str | None = None
) -> WaitResult:
    """等待元素出现（页面加载、AJAX 渲染后返回）。

    Args:
        locator: 定位符，如 '#submit'、'text:登录'、'css:.item>button'、'xpath://a'
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 最长等待秒数
        frame: 搜索范围，同 find_element 的 frame 参数（'active'=激活态 iframe）
    """
    tab, _ = manager.get_tab(tab_id)
    prepare_locator(tab, locator)
    found, container = manager.search(tab, locator, timeout=timeout, frame=frame)
    return WaitResult(
        found=bool(found),
        tab_id=tab.tab_id,
        locator=locator,
        elapsed_hint=None if found else f"等待 {timeout} 秒内未出现",
    )


@mcp.tool(
    tags={"page"},
    annotations={"title": "页面信息", "readOnlyHint": True},
)
def get_page_info(tab_id: str | None = None) -> PageInfo:
    """获取标签页当前网址、标题、加载状态与 User-Agent。"""
    tab, _ = manager.get_tab(tab_id)
    return PageInfo(
        tab_id=tab.tab_id,
        url=tab.url,
        title=tab.title,
        ready_state=str(tab.states.ready_state),
        user_agent=tab.user_agent,
    )


@mcp.tool(
    tags={"page"},
    annotations={"title": "页面 HTML", "readOnlyHint": True},
)
def get_page_html(tab_id: str | None = None, max_chars: int = 100_000) -> HtmlResult:
    """获取标签页当前页面的完整 HTML（不含 iframe 内部内容），超长时截断。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        max_chars: 返回 HTML 的最大字符数，超出部分截断
    """
    tab, _ = manager.get_tab(tab_id)
    html = tab.html or ""
    truncated = len(html) > max_chars
    if truncated:
        html = html[:max_chars]
    return HtmlResult(tab_id=tab.tab_id, url=tab.url, html=html, truncated=truncated)
