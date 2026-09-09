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

READ_CELLS_MAX_BYTES = 65536  # read_cells 响应字节级安全网（病态长文本格兜底）


def _guard_read_cells_size(data: dict) -> dict:
    """read_cells 字节级安全网：响应超限时截断尾部行并置 truncated 标志。

    格数上限（2000）防的是"格子太多"，此处防的是"每格文本太长"的病态表
    （UTF-8 字节数，中文按 3 字节计）。截断总是显式标注
    （truncated / truncated_rows / truncated_cells），并把 maxRow 同步为实际
    返回的最后一行，避免调用方按位置映射行时错位。
    """
    import json as _json

    def _nbytes(obj: dict) -> int:
        return len(_json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _clip(s: str, max_bytes: int) -> str:
        raw = s.encode("utf-8")
        return s if len(raw) <= max_bytes else raw[:max_bytes].decode("utf-8", "ignore")

    if _nbytes(data) <= READ_CELLS_MAX_BYTES:
        return data
    rows = data.get("values") or []
    kept: list = []
    for r in rows:
        if _nbytes({**data, "values": kept + [r]}) > READ_CELLS_MAX_BYTES:
            break
        kept.append(r)
    out = {
        **data,
        "values": kept,
        "truncated": True,
        "truncated_rows": len(rows) - len(kept),
    }
    if not kept and rows:
        # 单行本身就超限：保留该行并把格文本压到预算内，避免"一行都拿不到"
        row = list(rows[0])
        budget = max(1, (READ_CELLS_MAX_BYTES - 1024) // max(1, len(row)))
        clipped = 0
        for i, cell in enumerate(row):
            s = cell if isinstance(cell, str) else _json.dumps(cell, ensure_ascii=False)
            if len(s.encode("utf-8")) > budget:
                row[i] = _clip(s, budget)
                clipped += 1
        kept = [row]
        out["values"] = kept
        out["truncated_rows"] = len(rows) - 1
        out["truncated_cells"] = clipped
    if kept:
        out["maxRow"] = data.get("minRow", 0) + len(kept) - 1
    return out


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
    data = _run(session.frame, "read_cells", col0, row0, col1, row1)
    return _guard_read_cells_size(data)


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
        max_results: 最多返回条数（硬上限 100；仍有更多匹配时响应 truncated=true）
        tab_id: 标签页 id
        table_index: 表序号（注意：此处为字符串形式的索引，如 "0"）
    """
    session = get_session(tab_id, None if table_index is None else int(table_index))
    return _run(session.frame, "find_cells", text, exact, min(max(1, max_results), 100))


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
    index: int | None = None,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """点击单元格/表头内的交互图标（排序箭头、筛选漏斗、复选框、展开折叠等）。

    图标通过 VTable scenegraph 发现，坐标为其视觉中心的视口绝对坐标，
    以真实鼠标点击。

    Args:
        col, row: 图标所在单元格坐标（row=0 通常为表头）
        name: 图标名或功能语义过滤（支持中英文，如 'sort'/'排序'、'filter'/'筛选'、'freeze'/'冻结'、'checkbox'/'复选' 等）。
              当单元格内存在多个图标且未指定 index 时必须显式提供 name，避免误操作
        index: 第几个图标（从 1 起）；仅在需按序号点击或单元格仅有 1 个图标时可省略
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
    """VTable 多粒度快照:服务端提取肉眼可见文本/颜色/交互态,坐标自动换算为视口绝对坐标。

    5 种颗粒度(按传参自动选择):
    1. cell 模式(col+row): 单格文本/颜色/可交互性 + bounds/center/blank_point/图标。
    2. column 模式(只传 col 或列名): 列配置 + 表头换序锚点 header_center、调宽线 border_right、
       表头图标 header_icons,及可见行紧凑文本列表(省 token:无逐格几何)。
    3. row 模式(只传 row): 行背景色 bg_color、行高 height 及全列紧凑数据(col/field/title/text)。
    4. range 模式(col_range/row_range): 文本矩阵 values + 框选锚点 drag_start/drag_end,
       颜色/交互态稀疏返回(styles/interactive 只列偏离项,基线见 baseline_style);上限 500 格,超限报错。
    5. 全表快照(全空): 列头 + 视口内行紧凑矩阵(3~5KB)。

    Args:
        col: 列序号或列名(支持 field 或中文表头,如 "申请单号")
        row: 行序号(含表头,从 0 起)
        col_range: 列范围 [起始列, 结束列]
        row_range: 行范围 [起始行, 结束行]
        tab_id: 标签页 id,省略时用最新标签页
        table_index: 多表页面中第几个 .vtable 容器(从 0 起)
    """
    session = get_session(tab_id, table_index)
    return inspect_vtable(
        session,
        col=col,
        row=row,
        col_range=col_range,
        row_range=row_range,
    )
