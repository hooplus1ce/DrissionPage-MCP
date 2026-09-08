"""元素定位、信息与交互工具。

元素先通过 find_element / find_elements 定位并登记 element_id，
后续交互工具按 element_id 引用。页面刷新后元素会失效，需重新定位。
"""

from __future__ import annotations

import time

from fastmcp.exceptions import ToolError

from ..manager import manager, normalize_locator, prefer_visible, prepare_locator, real_click
from ..overlays import arm_overlays, drain_overlays
from ..models import ElementDetail, ElementListResult, ElementSummary, MessageResult
from ..server import mcp

TEXT_LIMIT = 500


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
        limit: 最多返回与登记的元素数量
        frame: 搜索范围，同 find_element 的 frame 参数
    """
    tab, session = manager.get_tab(tab_id)
    prepare_locator(tab, locator)
    eles, container = manager.search(tab, locator, many=True, timeout=timeout, frame=frame)
    if not eles:
        raise ToolError(f"未找到元素: {locator}。可先调整定位符，或用 wait_element 等待元素出现")
    result = []
    for ele in prefer_visible(list(eles))[: max(limit, 1)]:
        element_id = manager.register_element(ele, tab, session.browser_id, container)
        result.append(_summary(ele, element_id))
    return ElementListResult(count=len(result), elements=result)


@mcp.tool(
    tags={"element"},
    annotations={"title": "元素详情", "readOnlyHint": True},
)
def element_info(element_id: str) -> ElementDetail:
    """获取元素完整信息：文本、HTML、属性、值、链接、位置尺寸与状态。"""
    ele = _require_ele(element_id)
    try:
        attrs = dict(ele.attrs)
    except Exception:
        attrs = {}
    try:
        value = ele.value
    except Exception:
        value = None
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
        inner = (ele.inner_html or "")[:5000]
    except Exception:
        inner = None
    s = _summary(ele, element_id)
    return ElementDetail(
        element_id=s.element_id,
        tag=s.tag,
        text=s.text,
        css_selector=s.css_selector,
        xpath=s.xpath,
        inner_html=inner,
        attrs={k: str(v) for k, v in attrs.items()},
        value=str(value) if value is not None else None,
        link=link,
        rect=_rect_dict(ele),
        states=states,
    )


@mcp.tool(
    tags={"element", "action"},
    annotations={"title": "点击元素", "readOnlyHint": False},
)
def element_click(
    element_id: str,
    by_js: bool = False,
    use_action: bool = True,
    observe: bool = True,
) -> dict:
    """点击元素。默认通过 Actions 派发真实鼠标事件（移动→按下→抬起），
    保证 hover/焦点/事件链真实触发，适合 UI 自动化测试。

    响应在有新浮层时附带 overlays（弹窗/提示等，封顶 4 条），无则省略。

    Args:
        element_id: find_element 返回的元素 id
        by_js: 是否改用 JS 点击（元素被遮挡时可用，会绕过真实事件链）
        use_action: True=用 Actions 真实鼠标点击；False=用元素自带的模拟点击
        observe: 是否观察点击后的新浮层
    """
    ele = _require_ele(element_id)
    record = manager.get_record(element_id)
    container = (record.container if record else None) or None
    if observe and container is not None:
        arm_overlays(container)
    if by_js:
        ele.click(by_js=True)
    elif use_action:
        tab, _ = manager.get_tab(record.tab_id if record else None)
        real_click(tab, ele, record.container if record else None)
    else:
        ele.click()
    resp = {"ok": True, "message": f"已点击元素 {element_id}"}
    if observe and container is not None:
        time.sleep(0.45)
        overlays = drain_overlays(container)
        if overlays:
            resp["overlays"] = overlays
    return resp


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
    annotations={"title": "选择下拉项", "readOnlyHint": False},
)
def element_select(element_id: str, by: str, value: str | int) -> MessageResult:
    """在下拉框（<select>）元素中选择选项。

    Args:
        element_id: find_element 返回的 <select> 元素 id
        by: 选择方式：text=按可见文本, value=按 value 属性, index=按序号（从 1 开始）
        value: 匹配的文本 / value / 序号
    """
    ele = _require_ele(element_id)
    if not ele.select:
        raise ToolError(f"元素 {element_id} 不是 <select> 下拉框，无法执行选择操作")
    ok: bool | None
    if by == "text":
        ok = ele.select.by_text(str(value))
    elif by == "value":
        ok = ele.select.by_value(str(value))
    elif by == "index":
        ok = ele.select.by_index(int(value))
    else:
        raise ToolError("by 参数只支持 text / value / index")
    if ok is None:
        raise ToolError(f"选择失败：元素 {element_id} 可能不是多选下拉框或选项不存在")
    return MessageResult(ok=True, message=f"已在元素 {element_id} 中选择 {by}={value}")


@mcp.tool(
    tags={"element", "action"},
    annotations={"title": "勾选复选框", "readOnlyHint": False},
)
def element_check(element_id: str, checked: bool = True) -> MessageResult:
    """勾选或取消勾选复选框/单选框。

    Args:
        element_id: find_element 返回的元素 id
        checked: True=勾选，False=取消勾选
    """
    ele = _require_ele(element_id)
    ele.check(checked=checked)
    return MessageResult(ok=True, message=f"已将元素 {element_id} 设为 {'勾选' if checked else '取消勾选'}")


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
