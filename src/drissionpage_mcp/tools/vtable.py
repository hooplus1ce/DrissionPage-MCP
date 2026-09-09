"""VTable 表格自动化精简工具。

VTable 是 canvas 渲染，DOM 只有一个画布。本组工具通过注入 JS 拿到页面中的
VTable 实例，用其官方 API 读取单元格数据与 scenegraph 几何（canvas 局部坐标），
再经 iframe/canvas 双层偏移换算为页面视口绝对坐标，最终由 action_chain 派发
真实鼠标事件完成交互。

收敛后的 3 个核心能力：
1. vtable_inspect：全功能多粒度快照（查全表、查列、查行、查范围、查单格及交互锚点）
2. vtable_find_cell：按文本搜索单元格坐标
3. vtable_click_cell：点击单元格或单元格内部图标（支持双击、单复选/开关、自动滚动到位）
"""

from __future__ import annotations

from fastmcp import FastMCP
from ..vtable import (
    _run,
    click_cell,
    click_icon,
    get_session,
    inspect_vtable,
)

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("VTable")


@mcp.tool(
    tags={"vtable"},
    annotations={"title": "VTable 快照与交互锚点", "readOnlyHint": True},
)
def vtable_inspect(
    col: int | str | None = None,
    row: int | None = None,
    col_range: list[int] | None = None,
    row_range: list[int] | None = None,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """VTable 多粒度快照：服务端提取肉眼可见文本/颜色/交互态，坐标自动换算为视口绝对坐标。

    5 种颗粒度（按传参自动选择）：
    1. 全表快照（参数全空）：列头 + 视口内行紧凑矩阵（3~5KB，极省 token）。
    2. cell 模式（col+row）：单格文本/颜色/可交互性 + bounds/center/blank_point/图标。
    3. column 模式（只传 col 或列名）：列配置 + 表头换序锚点 header_center、调宽线 border_right、
       表头图标 header_icons，及可见行紧凑文本列表。
    4. row 模式（只传 row）：行背景色 bg_color、行高 height 及全列紧凑数据（col/field/title/text）。
    5. range 模式（col_range/row_range）：文本矩阵 values + 框选锚点 drag_start/drag_end。

    Args:
        col: 列序号或列名（支持 field 或中文表头，如 "申请单号"）
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
    tags={"vtable", "action"},
    annotations={"title": "点击 VTable 单元格或图标", "readOnlyHint": False},
)
def vtable_click_cell(
    col: int,
    row: int,
    icon: str | None = None,
    icon_index: int | None = None,
    double_click: bool = False,
    retry: bool = False,
    observe: bool = True,
    tab_id: str | None = None,
    table_index: int | None = None,
) -> dict:
    """点击 VTable 单元格或其内部图标：自动滚动到位 → 换算视口绝对坐标 → 真实鼠标点击。

    若指定 icon，则点击单元格/表头内的特定交互图标（排序箭头、筛选漏斗、复选框、展开折叠等）；
    未指定 icon 则点击单元格中心。

    Args:
        col, row: 单元格坐标（含表头行，从 0 起；表头点击=排序/筛选交互）
        icon: 单元格内图标名或语义过滤（如 'sort'/'排序'、'filter'/'筛选'、'checkbox'/'复选'）
        icon_index: 单元格内第几个图标（从 1 起，仅在有多个相同图标时需指定）
        double_click: 是否双击（双击常用于进入单元格编辑态）
        retry: 未验证（目标格未进入选区）时自动重点一次；勾选/开关类保持 False
        observe: 是否观察点击后的新浮层（封顶 4 条，无则省略）
    """
    session = get_session(tab_id, table_index)
    if icon or icon_index is not None:
        return click_icon(session, col, row, name=icon, index=icon_index)
    return click_cell(
        session, col, row, double_click=double_click, retry=retry, observe=observe
    )

