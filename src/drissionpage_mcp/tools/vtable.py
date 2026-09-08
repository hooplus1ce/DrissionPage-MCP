"""VTable 表格自动化工具。

VTable 是 canvas 渲染，DOM 只有一个画布。本组工具通过注入 JS 拿到页面中的
VTable 实例，用其官方 API 读取单元格数据与 scenegraph 几何（canvas 局部坐标），
再经 iframe/canvas 双层偏移换算为页面视口绝对坐标，最终由 action_chain 派发
真实鼠标事件完成交互。
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from ..models import MessageResult
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("VTable")
from ..vtable import (
    MAX_READ_CELLS,
    cell_center,
    cell_state,
    click_cell,
    click_icon,
    drag_scrollbar,
    cell_text_deep,
    get_selection,
    get_session,
    hover_cell,
    inspect_vtable,
    _run,
)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "VTable 概览", "readOnlyHint": True},
)
def vtable_info(
    tab_id: str | None = None, table_index: int | None = None
) -> dict:
    """绑定激活页面中的 VTable 并返回元数据：行列数、表头行数、冻结行列、
    canvas 位置尺寸、实例类型。后续 VTable 工具沿用此绑定（table_index 可省略）。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        table_index: 多表页面中第几个 .vtable 容器（从 0 起），省略时自动选
            可见弹窗中的表或第一个可见表
    """
    session = get_session(tab_id, table_index)
    return {
        "meta": session.meta,
        "frame_offset": session.frame_offset,
        "canvas_offset": session.canvas_offset,
    }


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "VTable 列头清单", "readOnlyHint": True},
)
def vtable_headers(tab_id: str | None = None, table_index: int | None = None) -> dict:
    """列出 VTable 全部列：col 序号、业务字段 field、表头标题 title、单元格类型。
    用于把业务列名映射为 col 序号。"""
    session = get_session(tab_id, table_index)
    return _run(session.frame, "headers")


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "批量读取单元格", "readOnlyHint": True},
)
def vtable_read_cells(
    col0: int,
    row0: int,
    col1: int,
    row1: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """批量读取 VTable 矩形区域的单元格值（行优先矩阵，最多 2000 格）。

    Args:
        col0, row0: 左上角单元格坐标（含表头行，行号从 0 起）
        col1, row1: 右下角单元格坐标
    """
    if (abs(col1 - col0) + 1) * (abs(row1 - row0) + 1) > MAX_READ_CELLS:
        raise ToolError(f"单次最多读取 {MAX_READ_CELLS} 格，请缩小范围分页读取")
    session = get_session(tab_id, table_index)
    return _run(session.frame, "read_cells", col0, row0, col1, row1)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "按文本找单元格", "readOnlyHint": True},
)
def vtable_find_cell(
    text: str,
    exact: bool = False,
    max_results: int = 20,
    tab_id: str | None = None,
    table_index: str | None = None,
) -> dict:
    """在 VTable 全表（含表头）中按文本查找单元格，返回 {col,row,value} 列表。

    Args:
        text: 要查找的文本
        exact: 是否精确匹配（默认包含匹配）
        max_results: 最多返回条数
        tab_id: 标签页 id
        table_index: 表序号（注意：此处为字符串形式的索引，如 "0"）
    """
    session = get_session(tab_id, None if table_index is None else int(table_index))
    return _run(session.frame, "find_cells", text, exact, max_results)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "单元格详情与坐标", "readOnlyHint": True},
)
def vtable_cell_info(
    col: int,
    row: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """读取单元格的值/类型/几何信息，并换算出页面视口绝对中心点坐标
    （该坐标可直接用于 action_chain 的 move_to）。

    Args:
        col, row: 单元格坐标（含表头行，从 0 起）
    """
    session = get_session(tab_id, table_index)
    return cell_center(session, col, row)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "滚动到单元格", "readOnlyHint": True},
)
def vtable_scroll_to_cell(
    col: int,
    row: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> MessageResult:
    """把 VTable 滚动到指定单元格使其进入可视区域（支持虚拟滚动）。"""
    from ..vtable import ensure_cell_visible

    session = get_session(tab_id, table_index)
    ok = ensure_cell_visible(session, col, row)
    if not ok:
        raise ToolError(f"滚动后单元格 ({col}, {row}) 仍未进入可视区域")
    return MessageResult(ok=True, message=f"单元格 ({col}, {row}) 已在可视区域内")


@mcp.tool(
    tags={"vtable", "action"},
    annotations={"title": "点击 VTable 单元格", "readOnlyHint": False},
)
def vtable_click_cell(
    col: int,
    row: int,
    double_click: bool = False,
    retry: bool = False,
    observe: bool = True,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """点击 VTable 单元格：自动滚动到位 → 换算视口绝对坐标 → action_chain
    真实鼠标移动并点击（非 JS 合成事件，触发完整 hover/focus/选区行为）。

    Args:
        col, row: 单元格坐标（含表头行，从 0 起；表头点击=排序/筛选交互）
        double_click: 是否双击（双击常用于进入单元格编辑态）
        retry: 未验证（目标格未进入选区）时自动重点一次；
            勾选/开关类单元格保持 False 避免状态翻转
        observe: 是否观察点击后的新浮层（封顶 4 条，无则省略）
    """
    session = get_session(tab_id, table_index)
    return click_cell(
        session, col, row, double_click=double_click, retry=retry, observe=observe
    )


@mcp.tool(
    tags={"vtable", "action"},
    annotations={"title": "点击 VTable 图标", "readOnlyHint": False},
)
def vtable_click_icon(
    col: int,
    row: int,
    name: str | None = None,
    index: int = 1,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """点击单元格/表头内的交互图标（排序箭头、筛选漏斗、复选框、展开折叠等）。

    图标通过 VTable scenegraph 发现，坐标为其视觉中心的视口绝对坐标，
    以真实鼠标点击。

    Args:
        col, row: 图标所在单元格坐标
        name: 图标名或功能名过滤（如 sort/filter/freeze/checkbox），省略时用 index
        index: 第几个图标（从 1 起）
    """
    session = get_session(tab_id, table_index)
    return click_icon(session, col, row, name=name, index=index)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "单元格显示文本(深度提取)", "readOnlyHint": True},
)
def vtable_cell_text(
    col: int,
    row: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """深度提取单元格的显示文本：scenegraph 场景图渲染文本优先（格式化器/
    自定义渲染后的"所见即所得"），依次回退溢出全文/显示值/原始值/业务记录
    字段，并标注命中的来源。当 getCellValue 与页面显示不一致时以此为准。

    Args:
        col, row: 单元格坐标
    """
    session = get_session(tab_id, table_index)
    return cell_text_deep(session, col, row)


@mcp.tool(
    tags={"vtable", "assert"},
    annotations={"title": "当前选区明细", "readOnlyHint": True},
)
def vtable_get_selection(
    tab_id: str | None = None, table_index: int | None = None
) -> dict:
    """读取 VTable 当前选区的单元格明细：col/row/field/title/value 及完整业务记录
    originData。点击/框选单元格后用它做断言（数据源为官方 getSelectedCellInfos）。
    """
    session = get_session(tab_id, table_index)
    return get_selection(session)


@mcp.tool(
    tags={"vtable", "action"},
    annotations={"title": "拖拽滚动条", "readOnlyHint": False},
)
def vtable_drag_scrollbar(
    direction: str = "vertical",
    distance_px: float = 200,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """拖拽 VTable 滚动条滑块实现滚动（真实鼠标：按下→拖动→释放）。

    滑块几何由表格总量与滚动比例确定性计算（滚动条为 canvas 绘制）。
    返回拖拽前后的 scrollTop/scrollLeft 供断言。

    Args:
        direction: vertical=纵向滚动条, horizontal=横向滚动条
        distance_px: 滑块拖动的像素距离（内容滚动距离 = 该值 × 内容比率）
    """
    if direction not in ("vertical", "horizontal"):
        raise ToolError("direction 只支持 vertical / horizontal")
    session = get_session(tab_id, table_index)
    return drag_scrollbar(session, direction, distance_px)


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "悬停单元格读取颜色", "readOnlyHint": True},
)
def vtable_hover_cell(
    col: int,
    row: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """真实鼠标悬停在单元格上（自动滚动到位），读取悬停态颜色信息：
    单元格背景色、文本颜色、是否可交互文本（link/cursor:pointer/下划线）
    及同行邻格背景（用于检测行高亮）。

    Args:
        col, row: 单元格坐标
    """
    session = get_session(tab_id, table_index)
    return hover_cell(session, col, row)


@mcp.tool(
    tags={"vtable", "assert"},
    annotations={"title": "单元格状态", "readOnlyHint": True},
)
def vtable_cell_state(
    col: int,
    row: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """读取单元格状态：是否选中、复选/单选/开关状态值，以及 scenegraph 视觉签名
    （fill/stroke 等，可在操作前后对比以断言高亮/颜色变化）。

    Args:
        col, row: 单元格坐标
    """
    session = get_session(tab_id, table_index)
    return cell_state(session, col, row)


@mcp.tool(
    tags={"vtable", "action"},
    annotations={"title": "滚轮滚动表格", "readOnlyHint": False},
)
def vtable_scroll_viewport(
    delta_y: float = 0,
    delta_x: float = 0,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> MessageResult:
    """对 VTable 画布中心派发真实鼠标滚轮事件（delta_y 正=向下滚，负=向上滚；
    delta_x 正=向右滚）。用于手动调整可视窗口。

    Args:
        delta_y: 垂直滚动量（像素近似）
        delta_x: 水平滚动量（像素近似）
    """
    if not delta_y and not delta_x:
        raise ToolError("delta_y / delta_x 至少传一个")
    session = get_session(tab_id, table_index)
    from ..vtable import _wheel_scroll

    _wheel_scroll(session, delta_y, delta_x)
    return MessageResult(ok=True, message=f"已滚动 delta_y={delta_y} delta_x={delta_x}")


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "业务字段寻址", "readOnlyHint": True},
)
def vtable_resolve_cell(
    field: str,
    record_index: int,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """用业务字段名 + 记录索引解析单元格地址（col/row）与当前值。
    比 find_cell 更稳定：不依赖文本内容，直接走 VTable 内部映射 API。

    Args:
        field: 业务字段名（vtable_headers 中的 field）
        record_index: 数据记录索引（从 0 起，不含表头）
    """
    session = get_session(tab_id, table_index)
    data = _run(session.frame, "resolve_cell", field, record_index)
    if not data.get("ok"):
        raise ToolError(
            f"字段 {field!r} + 记录 {record_index} 寻址失败: {data.get('reason')}"
        )
    return data


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "编辑单元格(API)", "readOnlyHint": False},
)
def vtable_edit_cell(
    col: int,
    row: int,
    value: str,
    commit: bool = True,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> MessageResult:
    """通过 VTable editorManager 编辑单元格并落值（API 级编辑，要求该列配置了编辑器）。
    如需真实键入流程：先 vtable_click_cell(double_click=True) 进入编辑态，
    再用 action_chain 的 type 步骤输入，最后 press_key ENTER。

    Args:
        col, row: 单元格坐标
        value: 要写入的值
        commit: 是否立即提交（False 保持编辑态便于连续操作）
    """
    session = get_session(tab_id, table_index)
    data = _run(session.frame, "edit_cell", col, row, value, commit)
    if not data.get("ok"):
        raise ToolError(f"编辑失败: {data.get('reason')}（该列可能未配置编辑器）")
    return MessageResult(ok=True, message=f"已写入单元格 ({col}, {row}): {value!r}")


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "VTable 可视化多粒度快照与交互锚点", "readOnlyHint": True},
)
def vtable_inspect(
    col: int | str | None = None,
    row: int | None = None,
    col_range: list[int] | None = None,
    row_range: list[int] | None = None,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """VTable 可视化多粒度快照与交互锚点感知。

    在 MCP 服务端一次性完成肉眼真实文本提取、视觉样式解析（前景色/背景色/可交互性）、
    列分界线与安全空白拖拽点计算，并自动换算为页面视口绝对坐标，为 action_chain 派发
    鼠标真实操作提供端到端数据支持。

    支持 5 种自适应调用颗粒度：
    1. 单单元格模式 (传入 col 与 row)：
       返回指定单元格肉眼可见文本、前景色、背景色、是否可交互（链接/编辑/指针）、
       视口绝对外接矩形 bounds、中心点 center、安全空白拖拽点 blank_point 与内部图标列表。
    2. 单列感知模式 (传入 col 或列名，row 为空)：
       返回列配置（col、field、title、width）、表头换序拖拽中心点 header_center、
       表头调列宽右边界线点 border_right、表头图标列表 header_icons 及视口内可见单元格列表。
       col 参数支持传入列字段名或中文表头标题（智能匹配，不区分大小写）。
    3. 单行感知模式 (传入 row，col 为空)：
       返回行背景色 bg_color（用于断言选中态或警示色）、行高 height 及该行所有列单元格紧凑数据。
    4. 区域切片模式 (传入 col_range=[c0, c1] 或 row_range=[r0, r1])：
       返回指定区域单元格矩阵，并显式计算出框选起始锚点 drag_start（左上格 blank_point）
       与结束锚点 drag_end（右下格 blank_point），可直接传给 action_chain 派发 hold+move+release。
    5. 全表可视快照模式 (全部参数为空)：
       返回轻量级可视全表骨架（列头清单 + 视口内行矩阵），体积控制在 3~5KB，彻底剔除 SVG 噪音。

    Args:
        col: 列序号或列名（支持 field 字段名或 title 中文表头，如 "申请单号"）
        row: 行序号（含表头，从 0 起）
        col_range: 列范围 [起始列, 结束列]
        row_range: 行范围 [起始行, 结束行]
        tab_id: 标签页 id，省略时用最新标签页
        table_index: 多表页面中第几个 .vtable 容器（从 0 起）
    """
    session = get_session(tab_id, table_index)
    return inspect_vtable(
        session,
        col=col,
        row=row,
        col_range=col_range,
        row_range=row_range,
    )
