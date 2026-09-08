"""AntV X6 流程图画布自动化领域逻辑与坐标换算。

针对 @antv/x6 矢量流程图编辑器提供：
- 画布会话与 Iframe 跨层级视口物理坐标精准换算；
- 节点/连线拓扑提取（合并 X6 Graph Model 与 SVG DOM 几何）；
- 真实 CDP 级鼠标拖拽移动、双击配置、端口连线与单键删除；
- 兼顾居中修复（针对前端未居中/未开启 panning 缺陷的补偿能力）。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp.exceptions import ToolError

from .cursor import act_cursor, glide_cursor
from .manager import manager
from .overlays import drain_overlays
from .x6_scripts import X6_SCRIPTS


@dataclass
class X6Session:
    """X6 图形编辑会话。"""

    frame: Any
    tab: Any
    meta: dict = field(default_factory=dict)

    def to_viewport(self, x: float, y: float) -> tuple[float, float]:
        """将 iframe 局部坐标转换为浏览器顶层视口绝对物理坐标。"""
        loc = self.frame.rect.location
        return (float(loc[0]) + float(x), float(loc[1]) + float(y))


def _run_x6(frame: Any, script_name: str, *args: Any) -> dict:
    """执行预置 X6 脚本并解析 JSON 返回值。"""
    raw = frame.run_js(X6_SCRIPTS[script_name], *args)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": raw}


def _ele_midpoint(ele: Any, fallback_x: float, fallback_y: float) -> tuple[float, float]:
    """安全获取元素在顶层视口的中点坐标。"""
    if ele and hasattr(ele, "rect"):
        rect = ele.rect
        mid = getattr(rect, "viewport_midpoint", None) or getattr(rect, "midpoint", None)
        if mid:
            return (float(mid[0]), float(mid[1]))
    return (float(fallback_x), float(fallback_y))

def bind_x6(tab_id: str | None = None) -> X6Session:
    """定位激活模块 iframe -> 绑定 X6 Graph 实例 -> 建立会话。"""
    tab, _ = manager.get_tab(tab_id)
    frame = manager.resolve_frame(tab, "active")
    data = _run_x6(frame, "bind")
    if not data.get("bound"):
        fresh = manager._rebuild_frame(tab, frame, "active")
        if fresh is not None and fresh is not frame:
            frame = fresh
            data = _run_x6(frame, "bind")
    if not data.get("bound"):
        raise ToolError(
            f"未能绑定 AntV X6 画布实例: {data.get('reason', '当前页面未发现 .x6-graph 容器')}"
        )
    session = X6Session(frame=frame, tab=tab)
    session.meta = data
    return session


def fit_view(session: X6Session, padding: int = 40) -> dict:
    """自适应修复画布视口：开启平移缩放并执行 zoomToFit，避免负坐标节点被视口截断。

    针对前端未开启 panning 以及负坐标节点（如 Y=-80）溢出视口上边缘的缺陷做自动补偿。
    """
    js = r"""
    var g = window.__x6_graph;
    if (!g) return JSON.stringify({ ok: false, reason: '未找到 X6 Graph 实例' });
    try {
        if (typeof g.enablePanning === 'function') g.enablePanning();
        if (typeof g.enableMouseWheel === 'function') g.enableMouseWheel();
        if (typeof g.zoomToFit === 'function') {
            g.zoomToFit({ padding: arguments[0] || 40, maxScale: 1 });
        } else if (typeof g.centerContent === 'function') {
            g.centerContent();
        }
        return JSON.stringify({
            ok: true,
            zoom: g.zoom(),
            translate: g.translate(),
            panning: typeof g.isPannable === 'function' ? g.isPannable() : true
        });
    } catch(e) {
        return JSON.stringify({ ok: false, error: e.message });
    }
    """
    raw = session.frame.run_js(js, padding)
    res = json.loads(raw) if isinstance(raw, str) else (raw or {})
    time.sleep(0.15)
    return res


def get_topology(session: X6Session, auto_fit: bool = True) -> dict:
    """一键读取当前流程图的完整拓扑结构与物理坐标。"""
    if auto_fit:
        try:
            fit_view(session)
        except Exception:
            pass
    raw_topo = _run_x6(session.frame, "extract")
    if not raw_topo.get("ok"):
        raise ToolError(f"提取流程图拓扑失败: {raw_topo.get('reason')}")
    model_nodes = {n["id"]: n for n in raw_topo.get("nodes", [])}
    edges = raw_topo.get("edges", [])

    # 扫描 DOM 中的实际渲染节点和端口
    dom_nodes = []
    for node_ele in session.frame.eles("css:g.x6-node"):
        cid = node_ele.attr("data-cell-id")
        shape = node_ele.attr("data-shape")
        text = node_ele.text.strip()
        n_rect = node_ele.rect

        # 视口绝对中点（DrissionPage 的 rect 已包含 iframe 偏移，优先取 viewport_midpoint）
        mid_vx, mid_vy = getattr(n_rect, "viewport_midpoint", None) or n_rect.midpoint
        loc_vx, loc_vy = getattr(n_rect, "viewport_location", None) or n_rect.location

        # 扫描属于该节点的连接桩 (In / Out Ports)
        ports = {}
        for p in node_ele.eles("css:.x6-port-body"):
            pid = p.attr("port") or p.attr("data-port-id")
            pgroup = p.attr("port-group") or ("out" if "out" in str(pid) else "in")
            p_mid = getattr(p.rect, "viewport_midpoint", None) or p.rect.midpoint
            p_vx, p_vy = p_mid
            ports[pid] = {
                "portId": pid,
                "group": pgroup,
                "viewport_center": {"x": round(p_vx, 1), "y": round(p_vy, 1)},
            }
        # 关联模型业务数据
        m_info = model_nodes.get(cid, {})
        dom_nodes.append(
            {
                "cellId": cid,
                "shape": shape,
                "text": text,
                "kind": (m_info.get("data") or {}).get("kind"),
                "data": m_info.get("data"),
                "viewport_center": {"x": round(mid_vx, 1), "y": round(mid_vy, 1)},
                "viewport_rect": {
                    "x": round(loc_vx, 1),
                    "y": round(loc_vy, 1),
                    "width": round(n_rect.size[0], 1),
                    "height": round(n_rect.size[1], 1),
                },
                "ports": ports,
            }
        )

    # 画布容器位置与安全空白点（用于可能需要的平移/取消选择）
    c_box = raw_topo.get("containerRect", {})
    c_vx, c_vy = session.to_viewport(c_box.get("x", 0), c_box.get("y", 0))

    return {
        "node_count": len(dom_nodes),
        "edge_count": len(edges),
        "nodes": dom_nodes,
        "edges": edges,
        "zoom": raw_topo.get("zoom", 1),
        "translate": raw_topo.get("translate", {}),
        "container_viewport": {
            "x": round(c_vx, 1),
            "y": round(c_vy, 1),
            "width": round(c_box.get("width", 0), 1),
            "height": round(c_box.get("height", 0), 1),
        },
        "blank_point": {"x": round(c_vx + 50, 1), "y": round(c_vy + 50, 1)},
    }


def find_node(session: X6Session, node_id_or_name: str) -> dict:
    """按 cellId 或节点显示名称查找单个节点信息。"""
    clean = node_id_or_name.strip().lower()
    topo = get_topology(session)
    nodes = topo["nodes"]

    # 1. 优先按 cellId 精准查找
    for n in nodes:
        if n["cellId"].lower() == clean:
            return n

    # 2. 按文本精准匹配
    for n in nodes:
        if n["text"].lower() == clean:
            return n

    # 3. 按文本包含匹配
    for n in nodes:
        if clean in n["text"].lower():
            return n

    avail = [f"{n['cellId']}: {n['text']}" for n in nodes]
    raise ToolError(
        f"未在画布中找到节点 '{node_id_or_name}'。当前画布可用节点列表: {avail}"
    )


def move_node(
    session: X6Session,
    node_id_or_name: str,
    dx: int,
    dy: int,
    steps: int = 15,
) -> dict:
    """真实鼠标拖拽位移节点（带平滑插值轨迹）。"""
    node = find_node(session, node_id_or_name)
    node_el = session.frame.ele(f"css:g.x6-node[data-cell-id='{node['cellId']}']")
    start_x, start_y = _ele_midpoint(
        node_el, node["viewport_center"]["x"], node["viewport_center"]["y"]
    )
    end_x = start_x + dx
    end_y = start_y + dy

    frame_actions = getattr(session.frame, "actions", None)
    actions = frame_actions if hasattr(frame_actions, "move_to") else session.tab.actions

    glide_cursor(session.tab, start_x, start_y, 250)
    time.sleep(0.15)
    if node_el:
        actions.move_to(node_el)
    else:
        actions.move_to((start_x, start_y))
    actions.wait(0.08)
    act_cursor(session.tab, "down", start_x, start_y)
    actions.hold()
    actions.wait(0.08)
    # 3. 1:1 绝对线性 60FPS 同步拖动（消除三次缓动领先与循环内部多重 0.5s 阻尼）
    dist = math.hypot(dx, dy)
    duration_s = max(0.25, min(0.6, dist * 0.003))
    duration_ms = int(duration_s * 1000)

    glide_cursor(session.tab, end_x, end_y, duration_ms, ease="linear")
    actions.move(dx, dy, duration=duration_s)

    actions.wait(0.05)
    act_cursor(session.tab, "up", end_x, end_y)
    actions.release()
    time.sleep(0.2)
    return {
        "cellId": node["cellId"],
        "node_text": node["text"],
        "from": {"x": round(start_x, 1), "y": round(start_y, 1)},
        "to": {"x": round(end_x, 1), "y": round(end_y, 1)},
        "delta": {"dx": dx, "dy": dy},
    }


def connect_nodes(
    session: X6Session,
    from_node: str,
    to_node: str,
    from_port: str = "out-0",
    to_port: str = "in-0",
    steps: int = 20,
) -> dict:
    """从源节点的出口桩拖拽连接至目标节点的入口桩（真实人工鼠标交互连线）。"""
    s_node = find_node(session, from_node)
    t_node = find_node(session, to_node)

    s_ports = s_node.get("ports", {})
    t_ports = t_node.get("ports", {})

    if from_port not in s_ports:
        avail_s = list(s_ports.keys())
        raise ToolError(
            f"源节点 '{s_node['text']}'(cellId={s_node['cellId']}) 无端口 '{from_port}'，可用端口: {avail_s}"
        )
    if to_port not in t_ports:
        avail_t = list(t_ports.keys())
        raise ToolError(
            f"目标节点 '{t_node['text']}'(cellId={t_node['cellId']}) 无端口 '{to_port}'，可用端口: {avail_t}"
        )

    p1 = session.frame.ele(
        f"css:g.x6-node[data-cell-id='{s_node['cellId']}'] circle[port='{from_port}']"
    )
    p2 = session.frame.ele(
        f"css:g.x6-node[data-cell-id='{t_node['cellId']}'] circle[port='{to_port}']"
    )
    frame_actions = getattr(session.frame, "actions", None)
    actions = frame_actions if hasattr(frame_actions, "move_to") else session.tab.actions

    start_x, start_y = _ele_midpoint(
        p1, s_ports[from_port]["viewport_center"]["x"], s_ports[from_port]["viewport_center"]["y"]
    )
    end_x, end_y = _ele_midpoint(
        p2, t_ports[to_port]["viewport_center"]["x"], t_ports[to_port]["viewport_center"]["y"]
    )
    start_pt = {"x": round(start_x, 1), "y": round(start_y, 1)}
    end_pt = {"x": round(end_x, 1), "y": round(end_y, 1)}

    # 1. 鼠标平滑滑行至源节点出口桩中心并悬停
    glide_cursor(session.tab, start_pt["x"], start_pt["y"], 250)
    time.sleep(0.15)
    if p1:
        actions.move_to(p1)
    else:
        actions.move_to((start_pt["x"], start_pt["y"]))
    actions.wait(0.08)

    # 2. 模拟长按按下出口桩（激活连线手柄）
    act_cursor(session.tab, "down", start_pt["x"], start_pt["y"])
    actions.hold()
    actions.wait(0.08)
    # 3. 1:1 绝对线性 60FPS 同步拖拽拉线（消除三次缓动领先与循环内部多重 0.5s 阻尼）
    dx = end_pt["x"] - start_pt["x"]
    dy = end_pt["y"] - start_pt["y"]
    dist = math.hypot(dx, dy)
    duration_s = max(0.25, min(0.6, dist * 0.003))
    duration_ms = int(duration_s * 1000)

    glide_cursor(session.tab, end_pt["x"], end_pt["y"], duration_ms, ease="linear")
    actions.move(dx, dy, duration=duration_s)
    # 4. 移动至目标桩磁吸区域悬停
    if p2:
        actions.move_to(p2)
    else:
        actions.move_to((end_pt["x"], end_pt["y"]))
    time.sleep(0.08)

    # 5. 松开鼠标完成连线
    act_cursor(session.tab, "up", end_pt["x"], end_pt["y"])
    actions.release()
    time.sleep(0.25)

    return {
        "ok": True,
        "from": {
            "cellId": s_node["cellId"],
            "text": s_node["text"],
            "port": from_port,
            "center": start_pt,
        },
        "to": {
            "cellId": t_node["cellId"],
            "text": t_node["text"],
            "port": to_port,
            "center": end_pt,
        },
    }


def click_node(
    session: X6Session,
    node_id_or_name: str,
    double: bool = False,
) -> dict:
    """单击选中或双击打开节点配置。"""
    node = find_node(session, node_id_or_name)
    node_el = session.frame.ele(f"css:g.x6-node[data-cell-id='{node['cellId']}']")
    pos_x, pos_y = _ele_midpoint(
        node_el, node["viewport_center"]["x"], node["viewport_center"]["y"]
    )

    frame_actions = getattr(session.frame, "actions", None)
    actions = frame_actions if hasattr(frame_actions, "move_to") else session.tab.actions

    glide_cursor(session.tab, pos_x, pos_y, 200)
    time.sleep(0.1)
    if node_el:
        actions.move_to(node_el)
    else:
        actions.move_to((pos_x, pos_y))
    actions.wait(0.05)
    act_cursor(session.tab, "click", pos_x, pos_y)
    actions.click()
    if double:
        time.sleep(0.08)
        act_cursor(session.tab, "click", pos_x, pos_y)
        actions.click()
        # SVG 元素通过 CDP 坐标难以稳定触发原生 dblclick，向目标 node 派发真实 MouseEvent
        try:
            if node_el:
                node_el.run_js("this.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));")
        except Exception:
            pass

    time.sleep(0.3)
    result = {
        "clicked": {"x": round(pos_x, 1), "y": round(pos_y, 1)},
        "node": {"cellId": node["cellId"], "text": node["text"]},
        "double": double,
    }
    overlays = drain_overlays(session.frame)
    if overlays:
        result["overlays"] = overlays
    return result


PALETTE_ALIASES = {
    "审批人": ["审批人", "approver", "user", "审批节点"],
    "判断节点": ["判断节点", "判断", "gateway", "condition", "x判断", "x判断节点"],
    "并行节点": ["并行节点", "并行", "parallel", "+并行", "+并行节点"],
    "开始节点": ["开始节点", "开始", "start"],
    "结束节点": ["结束节点", "结束", "end"],
}


def add_node(session: X6Session, kind: str) -> dict:
    """点击左侧物料栏单点追加节点（Click to Append）。"""
    clean = kind.strip().lower()
    target_label = None
    for label, aliases in PALETTE_ALIASES.items():
        if clean == label.lower() or any(clean == a.lower() for a in aliases):
            target_label = label
            break

    if not target_label:
        target_label = kind.strip()

    # 在激活 frame 内部查找物料项
    palette_item = session.frame.ele(f"text:{target_label}")
    if not palette_item:
        palette_item = session.frame.ele(f"@@text()={target_label}")
    if not palette_item:
        raise ToolError(
            f"未在左侧物料栏找到图元 '{kind}'。可选图元: {list(PALETTE_ALIASES.keys())}"
        )

    # 记录添加前的节点列表
    before_nodes = session.frame.eles("css:g.x6-node")
    before_ids = {n.attr("data-cell-id") for n in before_nodes}
    try:
        vx, vy = _ele_midpoint(palette_item, 0, 0)
        glide_cursor(session.tab, vx, vy, 180)
        time.sleep(0.1)
        act_cursor(session.tab, "click", vx, vy)
    except Exception:
        pass
    palette_item.click()
    time.sleep(0.35)
    after_nodes = session.frame.eles("css:g.x6-node")
    new_ids = [
        n.attr("data-cell-id")
        for n in after_nodes
        if n.attr("data-cell-id") not in before_ids
    ]
    created_id = new_ids[0] if new_ids else None
    return {
        "ok": True,
        "kind": target_label,
        "created_cell_id": created_id,
        "total_nodes_after": len(after_nodes),
    }


def delete_node(session: X6Session, node_id_or_name: str) -> dict:
    """单击选中节点后模拟按 Backspace 键销毁该节点及关联边。"""
    node = find_node(session, node_id_or_name)
    node_el = session.frame.ele(f"css:g.x6-node[data-cell-id='{node['cellId']}']")
    pos_x, pos_y = _ele_midpoint(
        node_el, node["viewport_center"]["x"], node["viewport_center"]["y"]
    )

    frame_actions = getattr(session.frame, "actions", None)
    actions = frame_actions if hasattr(frame_actions, "move_to") else session.tab.actions

    glide_cursor(session.tab, pos_x, pos_y, 200)
    time.sleep(0.1)
    if node_el:
        actions.move_to(node_el)
    else:
        actions.move_to((pos_x, pos_y))
    act_cursor(session.tab, "click", pos_x, pos_y)
    actions.click()
    actions.wait(0.08)
    actions.key_down("BACKSPACE")
    actions.wait(0.05)
    actions.key_up("BACKSPACE")
    time.sleep(0.25)

    # 检查是否已成功删除；若未删除（因前端未开启 allowDeleteAndManualConnect 规则属性），
    # 执行图模型级清理兜底。deleted_via 用于区分删除路径：UI 功能测试断言删除行为时
    # 应校验 deleted_via == 'keyboard'；'api' 说明真实键盘删除未生效、由模型清理补删，
    # 可能掩盖前端缺陷。
    cid = node["cellId"]
    deleted_via = "keyboard"
    try:
        remain = session.frame.eles(f"css:g.x6-node[data-cell-id='{cid}']", timeout=0.1)
        if remain:
            deleted_via = "api"
            clean_js = r"""
            var cid = arguments[0];
            var container = document.querySelector('.x6-graph');
            if (container) {
                var fiberKey = Object.keys(container).find(function(k) { return k.startsWith('__reactInternalInstance$'); });
                if (fiberKey) {
                    var cur = container[fiberKey];
                    while (cur) {
                        if (cur.stateNode && cur.stateNode.removeNodeInternal) {
                            cur.stateNode.removeNodeInternal(cid);
                            break;
                        }
                        cur = cur.return;
                    }
                }
            }
            if (window.__x6_graph && typeof window.__x6_graph.removeCell === 'function') {
                window.__x6_graph.removeCell(cid);
            }
            """
            session.frame.run_js(clean_js, cid)
    except Exception:
        pass
    return {
        "ok": True,
        "deleted_via": deleted_via,
        "deleted_node": {"cellId": node["cellId"], "text": node["text"]},
    }
