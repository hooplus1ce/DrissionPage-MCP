"""导航与页面信息工具。"""

from __future__ import annotations

import time

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from ..manager import manager, normalize_locator, prepare_locator
from ..models import (
    HtmlResult,
    MessageResult,
    NavInfo,
    NavMenuResult,
    PageInfo,
    WaitResult,
)

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
    """获取标签页当前网址、标题、加载状态、面包屑导航（权威模块路径）与 User-Agent。

    模块路径规范：以返回的 breadcrumb（解析自主框架 .ant-breadcrumb）为准，
    严禁根据 URL / iframe src 猜测模块路径。
    """
    tab, _ = manager.get_tab(tab_id)
    breadcrumb_text = None
    breadcrumb_items: list[str] = []
    try:
        bc_data = tab.run_js(
            r"""
            var container = document.querySelector('.ant-breadcrumb, [class*="breadcrumb"]');
            if (!container) return null;
            var links = container.querySelectorAll('.ant-breadcrumb-link');
            var items = [];
            if (links.length > 0) {
                for (var i = 0; i < links.length; i++) {
                    var t = links[i].innerText ? links[i].innerText.trim() : '';
                    if (t) items.push(t);
                }
            } else {
                var spans = container.querySelectorAll('span');
                for (var j = 0; j < spans.length; j++) {
                    var st = spans[j].innerText ? spans[j].innerText.trim() : '';
                    if (st && st !== '>' && st !== '/') items.push(st);
                }
            }
            return items.length > 0 ? items : null;
            """
        )
        if isinstance(bc_data, list):
            breadcrumb_items = [str(x) for x in bc_data if str(x).strip()]
            if breadcrumb_items:
                breadcrumb_text = " > ".join(breadcrumb_items)
    except Exception:
        pass

    active_frame_info = None
    try:
        active_f = manager.resolve_frame(tab, "active")
        if active_f is not tab:
            active_frame_info = {
                "iframe_id": active_f.attr("id"),
                "name": active_f.attr("name"),
                "src": active_f.attr("src"),
            }
    except Exception:
        pass

    return PageInfo(
        tab_id=tab.tab_id,
        url=tab.url,
        title=tab.title,
        ready_state=str(tab.states.ready_state),
        user_agent=tab.user_agent,
        breadcrumb=breadcrumb_text,
        breadcrumb_items=breadcrumb_items,
        active_frame=active_frame_info,
    )

@mcp.tool(
    tags={"page"},
    annotations={"title": "页面 HTML", "readOnlyHint": True},
)
def get_page_html(tab_id: str | None = None, max_chars: int = 20_000) -> HtmlResult:
    """获取标签页当前页面的 HTML（不含 iframe 内部内容），超长时截断。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        max_chars: 返回 HTML 的最大字符数（默认 20000，硬上限 50000），超出部分截断
    """
    tab, _ = manager.get_tab(tab_id)
    max_chars = min(max_chars, 50_000)
    html = tab.html or ""
    truncated = len(html) > max_chars
    if truncated:
        html = html[:max_chars]
    return HtmlResult(tab_id=tab.tab_id, url=tab.url, html=html, truncated=truncated)


@mcp.tool(
    tags={"navigate", "menu"},
    annotations={"title": "导航功能模块", "readOnlyHint": False},
)
async def nav_menu(
    menu_name: str,
    tab_id: str | None = None,
    force_reload: bool = False,
    timeout: float = 15.0,
    ctx: Context | None = None,
) -> NavMenuResult:
    """在 APS 管理后台中按菜单名一键导航直达功能模块。

    内部自动处理：
    1. 检查顶部标签栏（.ant-tabs-tab）是否已开该模块：
       - 若已开且 force_reload=False：直接点击激活该标签；
       - 若指定 force_reload=True：先点击关闭按钮（.anticon-close）关闭旧标签再重新开；
    2. 展开顶部「到达菜单」下拉选择框，输入模块名模糊匹配并点击；
    3. 轮询等待对应的激活模块 iframe 加载完毕；
    4. 解析最新面包屑（.ant-breadcrumb），返回模块路由与激活 frame 信息。

    Args:
        menu_name: 菜单/功能模块名称，如 "采购订单"、"产线管理"、"审批流配置"
        tab_id: 标签页 id，省略时用当前最新标签页
        force_reload: 是否先关闭已开标签再重新进入（默认 False）
        timeout: 等待目标模块 iframe 激活的最长秒数（默认 15 秒）
    """
    clean_name = (menu_name or "").strip()
    if not clean_name:
        raise ToolError("菜单名称不能为空")

    tab, _ = manager.get_tab(tab_id)
    reused = False

    # 1. 检查顶部标签栏中是否已有该模块标签
    try:
        tabs = tab.eles("css:.ant-tabs-tab")
    except Exception:
        tabs = []

    existing_tab = None
    for t in tabs:
        try:
            if clean_name in (t.text or ""):
                existing_tab = t
                break
        except Exception:
            continue

    if existing_tab:
        if force_reload:
            try:
                close_btn = existing_tab.ele("css:.anticon-close")
                if close_btn:
                    close_btn.click()
                    time.sleep(0.3)
            except Exception:
                pass
        else:
            try:
                is_active = "active" in (existing_tab.attr("class") or "")
                if not is_active:
                    existing_tab.click()
                    time.sleep(0.3)
                reused = True
            except Exception:
                pass

    # 2. 若未复用已有标签（或 force_reload 关闭后），通过顶部「到达菜单」搜索打开
    if not reused:
        select_ele = None
        for loc in (
            "css:.right-header .ant-select",
            "css:.ant-layout-header .ant-select",
            "css:.ant-select:has(.ant-select-selection__placeholder)",
        ):
            try:
                found = tab.ele(loc)
                if found and getattr(found.states, "is_displayed", True):
                    select_ele = found
                    break
            except Exception:
                continue

        if not select_ele:
            raise ToolError(
                f"在当前页面未找到顶部菜单导航框（.ant-select），请确认当前页面为 APS 管理平台主框架（{getattr(tab, 'url', '')}）"
            )

        try:
            select_ele.click()
            time.sleep(0.3)
        except Exception as exc:
            raise ToolError(f"点击菜单选择框失败: {exc}") from exc

        # 定位输入框并输入菜单名
        search_input = None
        for inp_loc in ("css:.ant-select-search__field", "css:input.ant-select-search__field"):
            try:
                found_inp = select_ele.ele(inp_loc) or tab.ele(inp_loc)
                if found_inp and getattr(found_inp.states, "is_displayed", True):
                    search_input = found_inp
                    break
            except Exception:
                continue

        if not search_input:
            try:
                tab.actions.type(clean_name)
            except Exception:
                pass
        else:
            try:
                search_input.input(clean_name, clear=True)
                time.sleep(0.4)
            except Exception as exc:
                raise ToolError(f"在菜单输入框输入失败: {exc}") from exc

        # 在下拉列表寻找匹配项
        target_item = None
        try:
            dropdown = tab.ele("css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)") or tab.ele("css:.ant-select-dropdown")
            if dropdown:
                for it in dropdown.eles("css:.ant-select-dropdown-menu-item"):
                    try:
                        if getattr(it.states, "is_displayed", True) and clean_name in (it.text or ""):
                            target_item = it
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        if not target_item:
            try:
                tab.actions.key_down("ESCAPE").key_up("ESCAPE")
            except Exception:
                pass
            raise ToolError(f"在菜单下拉列表中未找到名称包含 [{clean_name}] 的项，请核对菜单名称")

        try:
            target_item.click()
        except Exception as exc:
            raise ToolError(f"点击菜单项 [{clean_name}] 失败: {exc}") from exc

    # 3. 轮询等待激活的 iframe 挂载就绪
    deadline = time.time() + max(timeout, 3.0)
    active_frame_info = None
    while time.time() < deadline:
        try:
            active_f = manager.resolve_frame(tab, "active")
            if active_f is not tab:
                f_src = active_f.attr("src")
                if f_src and str(f_src).strip():
                    active_frame_info = {
                        "iframe_id": active_f.attr("id"),
                        "name": active_f.attr("name"),
                        "src": f_src,
                    }
                    break
        except Exception:
            pass
        time.sleep(0.3)

    # 4. 获取最新页面信息（包括面包屑）
    info = get_page_info(tab.tab_id)

    # 自动按业务场景激活匹配的特性套件（优先当前会话上下文激活 x6）
    if any(k in clean_name for k in ("审批流", "流程", "设计", "flow", "x6")):
        if ctx:
            try:
                await ctx.enable_components(tags={"x6"})
            except Exception:
                pass
        try:
            from ..server import enable_feature_internal
            enable_feature_internal("x6")
        except Exception:
            pass
    return NavMenuResult(
        ok=True,
        menu_name=clean_name,
        tab_id=tab.tab_id,
        breadcrumb=info.breadcrumb,
        breadcrumb_items=info.breadcrumb_items,
        active_frame=active_frame_info or info.active_frame,
        reused_tab=reused,
        message=f"已成功进入功能模块 [{clean_name}]",
    )
