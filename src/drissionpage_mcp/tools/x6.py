"""AntV X6 流程图画布自动化 MCP 工具集。

针对审批流/流程设计器中的 AntV X6 矢量图画布提供：
- 画布拓扑与节点端口绝对视口坐标一键提取；
- 真实 CDP 拖拽移动节点（平滑插值防跳帧）；
- 节点出口桩（Out-Port）到入口桩（In-Port）真实连线；
- 双击节点呼出配置表单与单击选中；
- 单点追加新增节点与 Backspace 键删除节点；
- 视图自适应与画布平移缺陷补偿（fit_view）。
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..x6 import (
    add_node,
    bind_x6,
    click_node,
    connect_nodes,
    delete_node,
    fit_view,
    get_topology,
    move_node,
)

mcp = FastMCP("X6")


@mcp.tool(
    tags={"x6", "flow"},
    annotations={"title": "查看 X6 流程图拓扑与节点坐标", "readOnlyHint": True},
)
def x6_nodes(tab_id: str | None = None, auto_fit: bool = True) -> dict:
    """一键读取当前 X6 流程图画布的拓扑结构、全部节点及端口信息。

    返回数据顶层字段：node_count / edge_count / nodes / edges（画布已有连线关系）
    / zoom / translate / container_viewport / blank_point。
    nodes 内每个节点包含：
    - cellId: 节点唯一标识（如 'n1', 'n2', 'f1'）
    - text: 显示名称（如 '开始', '审批A(lgq)'）
    - kind / data: 业务类型（start / approver / parallelGateway / end）及配置详情
    - viewport_center: 节点物理中心绝对视口坐标（已自动完成跨 iframe 偏移换算）
    - ports: 该节点所有可用连接桩及其视口坐标（如 out-0, in-0）

    Args:
        tab_id: 标签页 id，省略时用最新激活标签页
        auto_fit: 是否自动居中并激活平移缩放（针对前端未居中/未开启 panning 缺陷的补偿），默认 True
    """
    # 视口补偿已在 bind_x6 内按 auto_fit 执行一次，此处不再重复 zoomToFit
    session = bind_x6(tab_id, auto_fit=auto_fit)
    return get_topology(session, auto_fit=False)


@mcp.tool(
    tags={"x6", "flow"},
    annotations={"title": "自适应居中与平移补偿", "readOnlyHint": False},
)
def x6_fit(padding: int = 40, tab_id: str | None = None) -> dict:
    """自适应回正画布视口并激活平移/滚轮缩放。

    针对前端初始化缺陷（未启用 panning、负坐标节点如 Y=-80 溢出上边缘）进行自动修复补偿，
    确保所有节点完整呈现在视口中央且可拖拽。

    Args:
        padding: 边距内衬像素，默认 40
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    # 本工具的职责就是回正画布，bind 阶段无需再补偿一次（避免两次 zoomToFit）
    session = bind_x6(tab_id, auto_fit=False)
    return fit_view(session, padding=padding)


@mcp.tool(
    tags={"x6", "action"},
    annotations={"title": "拖拽移动 X6 节点", "readOnlyHint": False},
)
def x6_move_node(
    node: str,
    dx: int,
    dy: int,
    tab_id: str | None = None,
) -> dict:
    """真实鼠标拖拽位移指定的流程图节点（基于 CDP Input 平滑轨迹）。

    Args:
        node: 目标节点 cellId（如 'n2'）或节点显示名称（如 '审批A'）
        dx: X 轴位移像素差（正向右，负向左）
        dy: Y 轴位移像素差（正向下，负向上）
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    session = bind_x6(tab_id)
    return move_node(session, node, dx=dx, dy=dy)


@mcp.tool(
    tags={"x6", "action"},
    annotations={"title": "拖拽端口连线", "readOnlyHint": False},
)
def x6_connect(
    from_node: str,
    to_node: str,
    from_port: str = "out-0",
    to_port: str = "in-0",
    tab_id: str | None = None,
) -> dict:
    """从源节点的出口桩拖拽连接至目标节点的入口桩（真实 CDP 物理拉线）。

    Args:
        from_node: 源节点 cellId（如 'n1'）或节点名称（如 '开始'）
        to_node: 目标节点 cellId（如 'n2'）或节点名称（如 '审批A'）
        from_port: 源节点出口桩 id；默认 'out-0' 仅在节点只有一个出口桩时自动纠正，
            端口命名随节点类型而变（真机实测审批人为 out-right/in-top/in-left/in-bottom），
            建议先用 x6_nodes 查看该节点的 ports 再显式指定
        to_port: 目标节点入口桩 id，同上（默认 'in-0'，唯一时自动纠正）
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    session = bind_x6(tab_id)
    return connect_nodes(
        session,
        from_node=from_node,
        to_node=to_node,
        from_port=from_port,
        to_port=to_port,
    )


@mcp.tool(
    tags={"x6", "action"},
    annotations={"title": "点击或双击节点", "readOnlyHint": False},
)
def x6_click_node(
    node: str,
    double: bool = False,
    tab_id: str | None = None,
) -> dict:
    """单击选中节点或双击呼出节点配置弹窗/抽屉。

    Args:
        node: 目标节点 cellId（如 'n2'）或节点显示名称（如 '审批A'）
        double: 是否双击（True 则双击呼出「配置审批人」等 AntD 弹窗，False 为单击选中）
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    session = bind_x6(tab_id)
    return click_node(session, node, double=double)


@mcp.tool(
    tags={"x6", "action"},
    annotations={"title": "新增流程节点(长按拖拽移入)", "readOnlyHint": False},
)
def x6_add_node(
    kind: str,
    target_x: int | None = None,
    target_y: int | None = None,
    tab_id: str | None = None,
) -> dict:
    """从左侧物料栏长按拖拽新流程节点移入画布中（Drag-and-Drop from Palette）。

    通过真实 CDP 长按（hold）、60 FPS 轨迹平滑拖拽（move_to）与释放（release），
    将左侧物料栏的节点拖拽落入画布中，取代已废弃的单点追加方式。

    响应含 created_via 与 verified：created_via='drag' 表示真实拖拽生效；
    'api' 表示原生拖拽未生效、由组件 API 补建；verified=False 说明画布上没有
    渲染出该节点（可能被建在可视区外），此时不应断言新增成功。

    Args:
        kind: 图元类型或名称，支持：'审批人'（或 'approver'）、'判断节点'（或 'gateway'）、
              '并行节点'（或 'parallel'）、'开始节点'、'结束节点'
        target_x: 拖拽落点的视口绝对 X 坐标，省略时自动计算画布右下方安全空白区域
        target_y: 拖拽落点的视口绝对 Y 坐标，省略时自动计算画布右下方安全空白区域
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    session = bind_x6(tab_id)
    return add_node(session, kind, target_x=target_x, target_y=target_y)

@mcp.tool(
    tags={"x6", "action"},
    annotations={"title": "删除流程节点", "readOnlyHint": False},
)
def x6_delete_node(
    node: str,
    tab_id: str | None = None,
) -> dict:
    """选中指定节点并模拟按 Backspace 键级联销毁该节点及关联连接线。

    响应含 deleted_via：'keyboard'=真实键盘删除生效（UI 行为正确的证据）；
    'api'=键盘删除未生效、由图模型级清理补删（断言 UI 删除行为时应视为失败）。

    Args:
        node: 目标节点 cellId（如 'n3'）或节点显示名称（如 '审批B'）
        tab_id: 标签页 id，省略时用最新激活标签页
    """
    session = bind_x6(tab_id)
    return delete_node(session, node)
