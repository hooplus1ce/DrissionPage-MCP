"""Ant Design portal 弹层专用工具。

AntD 的下拉浮层、日期选择器、弹窗、message/notification 气泡均通过
portal 渲染到所在文档的 body 末尾。交互一律使用 Actions 真实鼠标事件，
保证 onChange 等事件链真实触发。
"""

from __future__ import annotations

import re
import time

from fastmcp.exceptions import ToolError

from ..manager import has_box, manager, normalize_locator, real_click
from ..models import MessageResult, ToastResult
from ..overlays import arm_overlays, drain_overlays
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("AntD Portal")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_OPTIONS_PER_PAGE = 50  # antd_get_options 单页封顶（省 token，超出用 offset 翻页）


def _resolve_search_root(tab, frame: str | None = None, element_id: str | None = None):
    """确定 portal 浮层的搜索容器：优先用元素所在文档，其次 frame 参数。"""
    if element_id:
        record = manager.get_record(element_id)
        if record is not None and record.container is not None:
            return record.container
    return manager.resolve_frame(tab, frame) if frame else tab


@mcp.tool(
    tags={"antd", "assert"},
    annotations={"title": "读取操作提示气泡", "readOnlyHint": True},
)
def get_toasts(tab_id: str | None = None, frame: str | None = None) -> ToastResult:
    """读取当前显示的 AntD message 全局提示与 notification 通知内容（用于操作结果断言）。

    查询范围默认为主文档+自动穿透 iframe；portal 弹层归属触发它的功能模块文档，
    建议传 frame='active' 或使用触发元素的所在文档。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        frame: 搜索范围（'active'=激活态 iframe，见 frame_list）
    """
    tab, _ = manager.get_tab(tab_id)
    root = _resolve_search_root(tab, frame)

    messages: list[str] = []
    notifications: list[str] = []
    try:
        found, root = manager.search(
            tab, ".ant-message-notice-content", many=True, timeout=1, frame=frame
        )
        for n in found or []:
            if n.text and n.text.strip():
                messages.append(n.text.strip()[:200])
    except Exception:
        pass
    try:
        found, root = manager.search(
            tab, ".ant-notification-notice", many=True, timeout=1, frame=frame
        )
        for n in found or []:
            if n.text and n.text.strip():
                notifications.append(n.text.strip()[:200])
    except Exception:
        pass
    return ToastResult(message_texts=messages, notification_texts=notifications)


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
    annotations={"title": "列出下拉选项", "readOnlyHint": True},
)
def antd_get_options(
    element_id: str, tab_id: str | None = None, timeout: float = 5, offset: int = 0
) -> dict:
    """展开 AntD 下拉选择框并列出可见选项文本（先于 antd_select 使用）。

    省 token：单次最多返回 50 条，超出时 truncated=true 用 offset 翻页；
    即使被截断，antd_select 仍可按已知文本直接选中（匹配在服务端完成）。

    Args:
        element_id: Select 元素的 element_id（find_element 定位 '.ant-select' 等）
        tab_id: 标签页 id，省略时用最新标签页
        timeout: 等待浮层出现的秒数
        offset: 选项起始下标（分页翻页用，配合 total/truncated）
    """
    ele = manager.get_element(element_id)
    tab, _ = manager.get_tab(tab_id)
    root = _resolve_search_root(tab, None, element_id)
    dropdown, dd_root = _open_dropdown(
        tab, ele, root, ".ant-select-dropdown:not(.ant-select-dropdown-hidden)", timeout
    )
    if dropdown is None:
        raise ToolError("下拉浮层未出现，请确认元素是 AntD Select 且可展开")
    options = []
    for loc in ("css:.ant-select-item-option", "css:.ant-select-dropdown-menu-item"):
        try:
            for o in dropdown.eles(normalize_locator(loc), timeout=2) or []:
                t = (o.text or "").strip()
                if t and t not in options:
                    options.append(t)
        except Exception:
            continue
    if not options:
        raise ToolError("浮层已展开但未检索到选项，可能为异步加载，请稍后重试")
    offset = max(0, offset)
    page = options[offset : offset + MAX_OPTIONS_PER_PAGE]
    return {
        "options": page,
        "total": len(options),
        "truncated": offset + MAX_OPTIONS_PER_PAGE < len(options),
    }


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
