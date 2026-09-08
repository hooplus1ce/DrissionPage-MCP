"""浏览器与标签页管理工具。"""

from __future__ import annotations

from ..manager import manager
from ..models import BrowserInfo, MessageResult, TabInfo
from ..server import mcp


@mcp.tool(
    tags={"browser"},
    annotations={"title": "启动浏览器", "readOnlyHint": False},
)
def browser_launch(
    browser_path: str | None = None,
    headless: bool = False,
    arguments: list[str] | None = None,
    user_data_path: str | None = None,
    incognito: bool = False,
) -> BrowserInfo:
    """启动一个新的浏览器实例（自动分配调试端口）。

    Args:
        browser_path: 浏览器可执行文件路径，默认使用系统 Chrome
        headless: 是否无头模式
        arguments: 附加的 Chromium 启动参数，如 ["--lang=zh-CN"]
        user_data_path: 用户数据目录（持久化登录态时指定）
        incognito: 是否无痕模式
    """
    session = manager.launch(
        browser_path=browser_path,
        headless=headless,
        arguments=arguments,
        user_data_path=user_data_path,
        incognito=incognito,
    )
    return session.info()


@mcp.tool(
    tags={"browser"},
    annotations={"title": "接管浏览器", "readOnlyHint": False},
)
def browser_connect(address: str = "127.0.0.1:9222") -> BrowserInfo:
    """接管一个已在运行的浏览器（需开启远程调试端口，默认 127.0.0.1:9222）。

    接管后的浏览器在本服务关闭时不会被退出。
    """
    session = manager.connect(address)
    return session.info()


@mcp.tool(
    tags={"browser"},
    annotations={"title": "关闭浏览器", "destructiveHint": True},
)
def browser_close(browser_id: str, force: bool = False) -> MessageResult:
    """关闭并移除一个浏览器会话（browser_connect 接管的浏览器只断开连接不退出）。

    Args:
        browser_id: 浏览器会话 id，可用 browser_status 查询
        force: 是否强制结束浏览器进程
    """
    closed = manager.close_browser(browser_id, force=force)
    return MessageResult(ok=True, message=f"已关闭浏览器会话 {closed}")


@mcp.tool(
    tags={"browser"},
    annotations={"title": "查看会话状态", "readOnlyHint": True},
)
def browser_status() -> list[BrowserInfo]:
    """列出所有浏览器会话及其标签页、上下文信息。"""
    return manager.status()


@mcp.tool(
    tags={"browser", "tab"},
    annotations={"title": "新建标签页", "readOnlyHint": False},
)
def tab_new(
    url: str | None = None,
    browser_id: str | None = None,
    context_id: str | None = None,
    background: bool = False,
) -> TabInfo:
    """新建标签页。指定 context_id 时在对应多账号上下文中创建（cookies 隔离）。

    Args:
        url: 初始网址，可为空
        browser_id: 浏览器会话 id，省略时用当前唯一会话
        context_id: 多账号上下文 id（由 context_new 创建），省略时在主上下文创建
        background: 是否在后台打开
    """
    if context_id is not None:
        ctx, session = manager.get_context(context_id, browser_id)
        tab = ctx.new_tab(url=url, background=background)
    else:
        session = manager.get_session(browser_id)
        tab = session.chromium.new_tab(url=url, background=background)
    return TabInfo(
        tab_id=tab.tab_id,
        browser_id=session.browser_id,
        context_id=context_id,
        url=tab.url,
        title=tab.title,
    )


@mcp.tool(
    tags={"browser", "tab"},
    annotations={"title": "列出标签页", "readOnlyHint": True},
)
def tab_list(browser_id: str | None = None) -> list[TabInfo]:
    """列出浏览器会话中的所有标签页。"""
    return manager.list_tabs(browser_id)


@mcp.tool(
    tags={"browser", "tab"},
    annotations={"title": "关闭标签页", "destructiveHint": True},
)
def tab_close(tab_id: str | None = None, others: bool = False) -> MessageResult:
    """关闭标签页，省略 tab_id 时关闭最新标签页；others=True 时保留该标签页关闭其余。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        others: 关闭除目标之外的所有标签页
    """
    tab, _ = manager.get_tab(tab_id)
    tab.close(others=others)
    return MessageResult(ok=True, message=f"已关闭标签页 {tab.tab_id}")


@mcp.tool(
    tags={"browser", "tab"},
    annotations={"title": "标签页详情", "readOnlyHint": True},
)
def tab_info(tab_id: str | None = None, browser_id: str | None = None) -> TabInfo:
    """获取标签页的当前地址、标题与加载状态。"""
    return manager.tab_info(tab_id, browser_id)


@mcp.tool(
    tags={"browser", "tab"},
    annotations={"title": "执行 JS", "readOnlyHint": False},
)
def run_js(script: str, tab_id: str | None = None, as_expr: bool = False) -> object:
    """在标签页中执行 JavaScript 并返回结果（可 JSON 序列化的部分）。

    Args:
        script: JS 代码；as_expr=False 时作为函数体执行（可用 return），True 时作为表达式求值
        tab_id: 标签页 id，省略时用最新标签页
        as_expr: 是否按表达式求值
    """
    tab, _ = manager.get_tab(tab_id)
    result = tab.run_js(script, as_expr=as_expr)
    if isinstance(result, (str, int, float, bool)) or result is None:
        return result
    return str(result)
