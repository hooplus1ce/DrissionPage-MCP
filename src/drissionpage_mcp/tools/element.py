"""元素定位、信息与交互工具。

元素先通过 find_element / find_elements 定位并登记 element_id，
后续交互工具按 element_id 引用。页面刷新后元素会失效，需重新定位。
"""

from __future__ import annotations

import time

from fastmcp.exceptions import ToolError

from ..cursor import act_cursor, glide_cursor
from ..manager import manager, normalize_locator, prefer_visible, prepare_locator, real_click, _rect_center_in_page
from ..overlays import arm_overlays, drain_overlays
from ..models import ElementDetail, ElementListResult, ElementSummary, MessageResult
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Elements")

TEXT_LIMIT = 500

# element_info 省 token 截断上限（full=True 时 inner_html 放宽、其余不截断）
INNER_HTML_COMPACT = 1000
INNER_HTML_FULL = 5000
ATTR_VALUE_LIMIT = 200
VALUE_LIMIT = 500


def _rect_dict(ele) -> dict[str, float] | None:
    try:
        r = ele.rect.location
        s = ele.rect.size
        return {
            "x": r[0],
            "y": r[1],
            "width": s[0],
            "height": s[1],
            "midpoint_x": ele.rect.midpoint[0],
            "midpoint_y": ele.rect.midpoint[1],
        }
    except Exception:
        return None


def _summary(ele, element_id: str) -> ElementSummary:
    try:
        text = (ele.text or "")[:TEXT_LIMIT]
    except Exception:
        text = None
    try:
        css = ele.css_selector
    except Exception:
        css = None
    try:
        xpath = ele.xpath
    except Exception:
        xpath = None
    return ElementSummary(
        element_id=element_id,
        tag=ele.tag,
        text=text,
        css_selector=css,
        xpath=xpath,
    )


def _require_ele(element_id: str):
    return manager.get_element(element_id)


def _check_found(ele, locator: str):
    # 5.0 中 ele() 未找到时返回 NoneElement（假值）
    if not ele:
        raise ToolError(
            f"未找到元素: {locator}。可先调整定位符，或用 wait_element 等待元素出现"
        )


@mcp.tool(
    tags={"element"},
    annotations={"title": "查找元素", "readOnlyHint": True},
)
def find_element(
    locator: str,
    tab_id: str | None = None,
    index: int = 1,
    timeout: float = 10,
    frame: str | None = None,
) -> ElementSummary:
    """在标签页中定位一个元素，返回摘要与 element_id（供交互工具使用）。

    Args:
        locator: 定位符，如 '#kw'、'.btn'、'tag:input'、'@name=q'、'text:搜索'、
            'css:.list>li'、'xpath://a[@href]'、'ax:@role=button'；不带前缀时自动匹配
        tab_id: 标签页 id，省略时用最新标签页
        index: 第几个匹配元素（从 1 开始，负数表示从末尾倒数）
        timeout: 未找到时的最长等待秒数
        frame: 搜索范围：省略=主文档+自动穿透 iframe；'main'=仅主文档；
            'active'=激活态 iframe；也可用序号或 iframe 的 id/name（见 frame_list）
    """
    tab, session = manager.get_tab(tab_id)
    prepare_locator(tab, locator)
    # 先批量检索再按可见性过滤：避免命中已关闭弹窗残留 DOM 里的隐藏元素
    eles, container = manager.search(tab, locator, many=True, timeout=timeout, frame=frame)
    _check_found(eles, locator)
    picked = prefer_visible(list(eles))
    try:
        ele = picked[index - 1] if index > 0 else picked[index]
    except IndexError:
        raise ToolError(
            f"可见匹配元素不足: 定位符 {locator} 共 {len(picked)} 个可见元素，"
            f"index={index} 超出范围"
        ) from None
    element_id = manager.register_element(ele, tab, session.browser_id, container)
    return _summary(ele, element_id)


@mcp.tool(
    tags={"element"},
    annotations={"title": "查找多个元素", "readOnlyHint": True},
)
def find_elements(
    locator: str,
    tab_id: str | None = None,
    timeout: float = 10,
    limit: int = 20,
    frame: str | None = None,
) -> ElementListResult:
    """在标签页中定位所有匹配元素，返回摘要列表（每个元素含 element_id）。

    Args:
        locator: 定位符，同 find_element
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 未找到时的最长等待秒数
        limit: 最多返回与登记的元素数量（硬上限 200；超出时响应含 total/truncated）
        frame: 搜索范围，同 find_element 的 frame 参数
    """
    tab, session = manager.get_tab(tab_id)
    prepare_locator(tab, locator)
    eles, container = manager.search(tab, locator, many=True, timeout=timeout, frame=frame)
    if not eles:
        raise ToolError(f"未找到元素: {locator}。可先调整定位符，或用 wait_element 等待元素出现")
    limit = min(max(limit, 1), 200)
    ordered = prefer_visible(list(eles))
    total = len(ordered)
    result = []
    for ele in ordered[:limit]:
        element_id = manager.register_element(ele, tab, session.browser_id, container)
        result.append(_summary(ele, element_id))
    return ElementListResult(
        count=len(result),
        elements=result,
        total=total,
        truncated=(total > limit) or None,
    )


@mcp.tool(
    tags={"element"},
    annotations={"title": "元素详情", "readOnlyHint": True},
)
def element_info(element_id: str, full: bool = False) -> ElementDetail:
    """获取元素完整信息：文本、HTML、属性、值、链接、位置尺寸与状态。

    省 token 默认截断：inner_html≤1000、每个属性值≤200、value≤500 字符，
    被截断的字段列在 truncated_fields 中；对长内容做精确断言时传 full=True。

    Args:
        element_id: find_element 返回的元素 id
        full: True=放宽截断（inner_html≤5000、属性值/value 不截断）
    """
    ele = _require_ele(element_id)
    truncated: list[str] = []

    def _cut(s: str, limit: int | None, name: str) -> str:
        # limit=None 表示该字段在 full 模式下不设限
        if limit is None or len(s) <= limit:
            return s
        if name not in truncated:
            truncated.append(name)
        return s[:limit]

    try:
        attrs_raw = dict(ele.attrs)
    except Exception:
        attrs_raw = {}
    attrs = {
        k: _cut(str(v), None if full else ATTR_VALUE_LIMIT, "attrs")
        for k, v in attrs_raw.items()
    }
    try:
        value_raw = ele.value
    except Exception:
        value_raw = None
    value = (
        _cut(str(value_raw), None if full else VALUE_LIMIT, "value")
        if value_raw is not None
        else None
    )
    try:
        link = ele.link
    except Exception:
        link = None
    try:
        states = {
            "is_displayed": ele.states.is_displayed,
            "is_enabled": ele.states.is_enabled,
            "is_checked": ele.states.is_checked,
            "is_in_viewport": ele.states.is_in_viewport,
        }
    except Exception:
        states = None
    try:
        inner_raw = ele.inner_html or ""
    except Exception:
        inner_raw = None
    inner = (
        _cut(inner_raw, INNER_HTML_FULL if full else INNER_HTML_COMPACT, "inner_html")
        if inner_raw is not None
        else None
    )
    s = _summary(ele, element_id)
    return ElementDetail(
        element_id=s.element_id,
        tag=s.tag,
        text=s.text,
        css_selector=s.css_selector,
        xpath=s.xpath,
        inner_html=inner,
        attrs=attrs,
        value=value,
        link=link,
        rect=_rect_dict(ele),
        states=states,
        truncated_fields=truncated or None,
    )


@mcp.tool(
    tags={"element", "action", "interaction"},
    annotations={"title": "通用点击(全能交互)", "readOnlyHint": False},
)
def click(
    target: str | None = None,
    point: dict[str, float] | None = None,
    x: float | None = None,
    y: float | None = None,
    frame: str | None = None,
    tab_id: str | None = None,
    double: bool = False,
    button: str = "left",
    by_js: bool = False,
    timeout: float = 5,
    observe: bool = True,
) -> dict:
    """通用全能点击：支持 element_id、选择器（CSS/XPath/文本/AX）、或视口绝对坐标。

    统一驱动 Win11 虚拟光标 60FPS 滑行 + 真实鼠标事件，支持单双击/右键，并自动感知
    点击后弹出的新浮层（Modal/Toast，封顶 4 条）。

    Args:
        target: 目标元素标识（element_id、'.ant-btn'、'//button'、'text:保 存'、'保 存'）
        point: 视口绝对坐标 {'x': 500, 'y': 300}（Canvas 表格或无 DOM 点位点击）
        x: 目标 X 坐标（与 y 搭配，可替代 point）
        y: 目标 Y 坐标（与 x 搭配，可替代 point）
        frame: 'active'=激活模块 iframe；None=激活 iframe 优先，主文档兜底
        tab_id: 标签页 id，省略时用最新标签页
        double: 是否双击（连续两次点击并触发标准 dblclick 事件）
        button: 'left' | 'right' | 'middle'
        by_js: 是否改用 JS 点击（元素被遮挡时可用）
        timeout: 等待元素可见的超时秒数
        observe: 是否观察点击后的新浮层
    """
    tab, session = manager.get_tab(tab_id)

    # 1. 绝对物理坐标点击模式
    pt_x = None
    pt_y = None
    if point and "x" in point and "y" in point:
        pt_x = float(point["x"])
        pt_y = float(point["y"])
    elif x is not None and y is not None:
        pt_x = float(x)
        pt_y = float(y)

    if pt_x is not None and pt_y is not None:
        glide_cursor(tab, pt_x, pt_y, 180)
        time.sleep(0.08)
        act_cursor(tab, "click", pt_x, pt_y)
        actions = tab.actions
        actions.move_to((pt_x, pt_y))
        if button == "right":
            actions.r_click()
        elif button == "middle":
            actions.m_click()
        elif double:
            actions.click(times=2)
        else:
            actions.click()
        time.sleep(0.2)
        res = {
            "ok": True,
            "clicked": {"x": round(pt_x, 1), "y": round(pt_y, 1)},
            "button": button,
            "double": double,
        }
        try:
            active_frame = manager.resolve_frame(tab, frame or "active")
            overlays = drain_overlays(active_frame)
            if overlays:
                res["overlays"] = overlays
        except Exception:
            pass
        return res

    # 2. 目标元素点击模式
    if not target:
        raise ToolError("必须提供 target（element_id/选择器/文本）或 point/x,y 坐标")
    ele = None
    container = None
    element_id = None

    rec = manager.get_record(target)
    if rec is not None:
        # 明确为已登记的 element_id，执行严格存活校验（若失效抛出已失效 ToolError）
        ele = _require_ele(target)
        container = rec.container or tab
        element_id = target
    else:
        # 非 element_id，按选择器/文本检索
        raw_res, container = manager.search(tab, target, timeout=timeout, frame=frame)
        ele = prefer_visible(raw_res) if isinstance(raw_res, list) else raw_res
        if not ele:
            raise ToolError(f"未找到目标元素: {target!r}")
        element_id = manager.register_element(ele, tab, session.browser_id, container)
    if observe and container is not None:
        arm_overlays(container)

    pt = None
    if hasattr(ele, "rect"):
        try:
            pt = getattr(ele.rect, "viewport_midpoint", None) or getattr(ele.rect, "midpoint", None)
        except Exception:
            pass
    if pt is None:
        pt = _rect_center_in_page(tab, ele, container)

    if pt is not None:
        glide_cursor(tab, pt[0], pt[1], 180)
        time.sleep(0.08)
        act_cursor(tab, "click", pt[0], pt[1])

    if by_js:
        ele.click(by_js=True)
    elif button == "right":
        tab.actions.r_click(ele)
    elif button == "middle":
        tab.actions.m_click(ele)
    elif double:
        tab.actions.click(ele, times=2)
        try:
            ele.run_js("this.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));")
        except Exception:
            pass
    else:
        real_click(tab, ele, container)

    res = {
        "ok": True,
        "message": f"已点击目标: {target}",
        "element_id": element_id,
        "double": double,
        "button": button,
        "by_js": by_js,
    }
    if pt is not None:
        res["clicked"] = {"x": round(pt[0], 1), "y": round(pt[1], 1)}

    if observe and container is not None:
        time.sleep(0.35)
        overlays = drain_overlays(container)
        if overlays:
            res["overlays"] = overlays

    return res


@mcp.tool(
    tags={"element", "action"},
    annotations={"title": "输入文本", "readOnlyHint": False},
)
def element_input(element_id: str, text: str, clear: bool = False, by_js: bool = False) -> MessageResult:
    """向输入框元素输入文本。

    Args:
        element_id: find_element 返回的元素 id
        text: 要输入的文本
        clear: 输入前是否清空已有内容
        by_js: 是否改用 JS 赋值
    """
    ele = _require_ele(element_id)
    ele.input(text, clear=clear, by_js=by_js)
    return MessageResult(ok=True, message=f"已向元素 {element_id} 输入文本")


@mcp.tool(
    tags={"element", "action"},
    annotations={"title": "悬停", "readOnlyHint": False},
)
def element_hover(element_id: str) -> MessageResult:
    """将鼠标悬停在元素上（触发下拉菜单、悬浮提示等）。"""
    ele = _require_ele(element_id)
    ele.hover()
    return MessageResult(ok=True, message=f"已悬停在元素 {element_id}")

@mcp.tool(
    tags={"element", "action"},
    annotations={"title": "滚动元素", "readOnlyHint": False},
)
def element_scroll(
    element_id: str,
    action: str = "to_see",
    pixel: int = 300,
) -> MessageResult:
    """滚动元素（或让元素滚动到可视区域）。

    Args:
        element_id: find_element 返回的元素 id
        action: to_see=滚动到元素可见, to_top=滚到顶, to_bottom=滚到底,
            down=向下滚, up=向上滚
        pixel: down/up 时滚动的像素
    """
    ele = _require_ele(element_id)
    if action == "to_see":
        ele.scroll.to_see()
    elif action == "to_top":
        ele.scroll.to_top()
    elif action == "to_bottom":
        ele.scroll.to_bottom()
    elif action == "down":
        ele.scroll.down(pixel)
    elif action == "up":
        ele.scroll.up(pixel)
    else:
        raise ToolError("action 参数只支持 to_see / to_top / to_bottom / down / up")
    return MessageResult(ok=True, message=f"已对元素 {element_id} 执行滚动: {action}")
