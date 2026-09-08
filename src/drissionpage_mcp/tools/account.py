"""多账号（BrowserContext）与 cookies 工具。

BrowserContext 提供独立的 cookies 存储，可在同一浏览器中登录多个账号。
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from ..manager import manager
from ..models import ContextInfo, CookieList, MessageResult
from ..server import mcp


@mcp.tool(
    tags={"account"},
    annotations={"title": "新建多账号上下文", "readOnlyHint": False},
)
def context_new(browser_id: str | None = None) -> ContextInfo:
    """创建一个独立的浏览器上下文（BrowserContext），cookies 与主上下文完全隔离，
    用于同一浏览器内登录多个账号。

    Args:
        browser_id: 浏览器会话 id，省略时用当前唯一会话
    """
    return manager.new_context(browser_id)


@mcp.tool(
    tags={"account"},
    annotations={"title": "关闭上下文", "destructiveHint": True},
)
def context_close(context_id: str) -> MessageResult:
    """关闭并销毁一个多账号上下文（其标签页与 cookies 一并清除）。"""
    closed = manager.close_context(context_id)
    return MessageResult(ok=True, message=f"已关闭上下文 {closed}")


@mcp.tool(
    tags={"account"},
    annotations={"title": "列出上下文", "readOnlyHint": True},
)
def context_list(browser_id: str | None = None) -> list[ContextInfo]:
    """列出浏览器会话中的所有多账号上下文。"""
    return manager.list_contexts(browser_id)


@mcp.tool(
    tags={"account", "cookies"},
    annotations={"title": "读取 cookies", "readOnlyHint": True},
)
def cookies_get(tab_id: str | None = None) -> CookieList:
    """读取标签页当前域名相关的 cookies。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    cookies = tab.cookies(all_info=True)
    return CookieList(count=len(cookies), cookies=[dict(c) for c in cookies])


@mcp.tool(
    tags={"account", "cookies"},
    annotations={"title": "写入 cookies", "readOnlyHint": False},
)
def cookies_set(tab_id: str | None = None, cookies: list[dict] | None = None) -> MessageResult:
    """向标签页写入 cookies（复用登录态时使用）。每条 cookie 需含 name/value/domain。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        cookies: cookie 字典列表，如 [{"name": "token", "value": "xxx", "domain": ".example.com"}]
    """
    if not cookies:
        raise ToolError("cookies 不能为空")
    tab, _ = manager.get_tab(tab_id)
    tab.set.cookies(cookies)
    return MessageResult(ok=True, message=f"已写入 {len(cookies)} 条 cookies")


@mcp.tool(
    tags={"account", "cookies"},
    annotations={"title": "清除 cookies", "destructiveHint": True},
)
def cookies_clear(tab_id: str | None = None) -> MessageResult:
    """清除标签页所属域的 cookies。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    tab.set.cookies.clear()
    return MessageResult(ok=True, message="已清除 cookies")
