"""AntV X6 流程图工具测试：拓扑提取、节点位移、连线、点击与删除。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeElement, FakeFrame


class FakeX6Element(FakeElement):
    """模拟 SVG / HTML 元素，支持 rect, attr, eles。"""

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        text: str = "",
        loc: tuple[int, int] = (100, 100),
        size: tuple[int, int] = (100, 50),
    ):
        super().__init__(tag=tag, text=text)
        if attrs is not None:
            self.attrs = attrs
        self._rect_loc = loc
        self._rect_size = size
        self.children: list[FakeX6Element] = []

    def attr(self, name: str):
        return self.attrs.get(name)

    def eles(self, locator: str):
        if "x6-port-body" in locator:
            return [c for c in self.children if "x6-port-body" in c.attrs.get("class", "")]
        return []

    def ele(self, locator: str):
        res = self.eles(locator)
        return res[0] if res else None

class FakeX6Frame(FakeFrame):
    """模拟含 X6 画布的 ChromiumFrame。"""

    def __init__(self, iframe_id: str, displayed: bool = True, location: tuple[int, int] = (170, 80)):
        super().__init__(iframe_id, displayed=displayed)
        self._location = location
        self.responses: dict[str, object] = {}
        self.dom_nodes: list[FakeX6Element] = []
        self.palette_items: dict[str, FakeX6Element] = {}

    @property
    def location(self):
        return self._location

    def run_js(self, script, *args, **kwargs):
        if "centerContent" in script or "zoomToFit" in script:
            return json.dumps({"ok": True, "zoom": 1, "translate": {"tx": 100, "ty": 50}})
        if "g.getNodes" in script or "EXTRACT_GRAPH" in script:
            return json.dumps({
                "ok": True,
                "zoom": 1,
                "translate": {"tx": 0, "ty": 0},
                "containerRect": {"x": 100, "y": 50, "width": 1000, "height": 600},
                "nodes": [
                    {"id": "n1", "shape": "circle", "position": {"x": 0, "y": 0}, "size": {"width": 40, "height": 40}, "data": {"kind": "start"}},
                    {"id": "n2", "shape": "rect", "position": {"x": 200, "y": 0}, "size": {"width": 120, "height": 40}, "data": {"kind": "approver"}},
                ],
                "edges": [
                    {"id": "e1", "source": "n1", "target": "n2", "sourcePort": "out-0", "targetPort": "in-0"}
                ]
            })
        return json.dumps({
            "bound": True,
            "ok": True,
            "source": "fiber",
            "zoom": 1,
            "translate": {"tx": 0, "ty": 0},
            "containerRect": {"x": 100, "y": 50, "width": 1000, "height": 600}
        })

    def eles(self, locator: str):
        if "x6-node" in locator:
            return self.dom_nodes
        return []

    def ele(self, locator: str):
        if "text:" in locator:
            text = locator.split("text:")[1].strip()
            return self.palette_items.get(text)
        if "@@text()=" in locator:
            text = locator.split("@@text()=")[1].strip()
            return self.palette_items.get(text)
        return None


@pytest.fixture
def x6_seeded(seeded_manager):
    session, chromium, tab = seeded_manager
    x6_frame = FakeX6Frame("x6_iframe", displayed=True, location=(170, 80))
    tab.iframes = [tab.iframes[0], x6_frame]
    frame_x, frame_y = 170, 80
    # 构造两个 DOM 节点（真实 DrissionPage 中，iframe 内部元素的 rect 已内建 iframe 视口偏移）
    n1 = FakeX6Element("g", attrs={"class": "x6-cell x6-node", "data-cell-id": "n1", "data-shape": "circle"}, text="开始", loc=(200 + frame_x, 200 + frame_y), size=(40, 40))
    p1_out = FakeX6Element("circle", attrs={"class": "x6-port-body", "port": "out-0", "port-group": "out"}, loc=(235 + frame_x, 215 + frame_y), size=(10, 10))
    n1.children.append(p1_out)

    n2 = FakeX6Element("g", attrs={"class": "x6-cell x6-node", "data-cell-id": "n2", "data-shape": "rect"}, text="审批A", loc=(400 + frame_x, 200 + frame_y), size=(120, 40))
    p2_in = FakeX6Element("circle", attrs={"class": "x6-port-body", "port": "in-0", "port-group": "in"}, loc=(395 + frame_x, 215 + frame_y), size=(10, 10))
    p2_out = FakeX6Element("circle", attrs={"class": "x6-port-body", "port": "out-0", "port-group": "out"}, loc=(515 + frame_x, 215 + frame_y), size=(10, 10))
    n2.children.extend([p2_in, p2_out])
    x6_frame.dom_nodes = [n1, n2]

    # 物料项
    approver_item = FakeX6Element("div", text="审批人", loc=(50, 150), size=(80, 30))
    approver_item.click = MagicMock()
    x6_frame.palette_items["审批人"] = approver_item

    return session, chromium, tab, x6_frame


async def test_x6_nodes_topology(client, x6_seeded):
    """测试获取拓扑结构及带 iframe 偏移的视口物理坐标。"""
    res = await client.call_tool("x6_nodes", {})
    data = res.data
    assert data["node_count"] == 2
    assert data["edge_count"] == 1
    assert data["nodes"][0]["cellId"] == "n1"
    assert data["nodes"][0]["text"] == "开始"
    # local center (220, 220) + iframe (170, 80) = (390.0, 300.0)
    assert data["nodes"][0]["viewport_center"]["x"] == 390.0
    assert data["nodes"][0]["viewport_center"]["y"] == 300.0
    assert "out-0" in data["nodes"][0]["ports"]


class FlakyScanFrame(FakeX6Frame):
    """首次 x6-node 检索返回空，复现 iframe 刚切换时的空文档症状。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.empty_scans = 1

    def eles(self, locator: str):
        if "x6-node" in locator and self.empty_scans > 0:
            self.empty_scans -= 1
            return []
        return super().eles(locator)


def test_x6_nodes_recovers_from_empty_first_scan(x6_seeded):
    """真机缺陷回归：设计器 iframe 刚可见时首帧 DOM 扫描为空，
    应重建 frame 会话重试一次，而不是返回 node_count=0 的假拓扑。"""
    _, _, tab, x6_frame = x6_seeded
    flaky = FlakyScanFrame("x6_iframe", displayed=True, location=(170, 80))
    flaky.dom_nodes = x6_frame.dom_nodes
    tab.iframes = [tab.iframes[0], flaky]

    from drissionpage_mcp.x6 import bind_x6, get_topology

    session = bind_x6(None, auto_fit=False)
    topo = get_topology(session, auto_fit=False)

    assert flaky.empty_scans == 0  # 空扫描已发生并被重试覆盖
    assert topo["node_count"] == 2
    assert topo["edge_count"] == 1
    assert topo["nodes"][0]["viewport_center"]["x"] == 390.0


def test_x6_default_drop_point_stays_inside_canvas(x6_seeded):
    """真机缺陷回归：默认落点必须落在画布可视区内。

    旧实现从图模型节点取 viewport_center（模型无此字段）→ max 恒为 0 →
    落点退化成 (f_loc+120, f_loc+60)，实测在画布之外，节点拖不进去。
    """
    _, _, tab, x6_frame = x6_seeded
    from drissionpage_mcp.x6 import _default_drop_point, bind_x6

    session = bind_x6(None, auto_fit=False)
    dst_x, dst_y = _default_drop_point(session, (170, 80), (1694, 903))

    # 画布可视区：iframe 视口位置 + 尺寸（留出边距）
    assert 170 + 60 <= dst_x <= 170 + 1694 - 120
    assert 80 + 60 <= dst_y <= 80 + 903 - 80
    # 落在最右/最下节点之外，避免与已有节点重叠
    assert dst_x == 750.0 and dst_y == 360.0


def test_pick_port_corrects_default_by_direction_and_anchor():
    """真机回归：该应用的 port-group 存方位名（right/top/left/bottom），
    in/out 只在端口 id 前缀里，且默认 out-0/in-0 必然不存在。"""
    from drissionpage_mcp.x6 import _pick_port

    approver = {
        "in-top": {"group": "top", "viewport_center": {"x": 462, "y": 256}},
        "in-left": {"group": "left", "viewport_center": {"x": 402, "y": 278}},
        "in-bottom": {"group": "bottom", "viewport_center": {"x": 462, "y": 300}},
        "out-right": {"group": "right", "viewport_center": {"x": 522, "y": 278}},
    }
    # 出口唯一 -> 纠正（group 是方位名时仍按 id 前缀识别方向）
    assert _pick_port(approver, "out-0", "out", default="out-0") == "out-right"
    # 入口多候选 + 锚点在左侧 -> 就近选 in-left
    assert _pick_port(approver, "in-0", "in", default="in-0", anchor=(300, 278)) == "in-left"
    # 无锚点时不猜，交由「可用端口」报错
    assert _pick_port(approver, "in-0", "in", default="in-0") == "in-0"
    # 显式指定优先；显式非默认且不存在时不替换（拼写错误应报错）
    assert _pick_port(approver, "in-left", "in", default="in-0") == "in-left"
    assert _pick_port(approver, "no-such-port", "out", default="out-0") == "no-such-port"
    assert _pick_port({}, "out-0", "out", default="out-0") == "out-0"


async def test_x6_fit(client, x6_seeded):
    """测试自适应回正与开启平移。"""
    res = await client.call_tool("x6_fit", {"padding": 30})
    assert res.data["ok"] is True


async def test_x6_move_node(client, x6_seeded):
    """测试拖拽移动节点生成平滑 action 轨迹。"""
    _, _, tab, _ = x6_seeded
    res = await client.call_tool("x6_move_node", {"node": "n2", "dx": 50, "dy": 30})
    assert res.data["cellId"] == "n2"
    assert res.data["delta"] == {"dx": 50, "dy": 30}
    # 验证真实 actions 调用
    assert any(c[0] == "move_to" for c in tab.actions.calls)
    assert any(c[0] == "hold" for c in tab.actions.calls)
    assert any(c[0] == "release" for c in tab.actions.calls)


async def test_x6_move_node_viewport_protection(client, x6_seeded):
    """测试大幅度位移触发视口自动平移保护。"""
    _, _, tab, _ = x6_seeded
    res = await client.call_tool("x6_move_node", {"node": "n2", "dx": 0, "dy": -500})
    assert res.data["cellId"] == "n2"
    assert res.data["delta"] == {"dx": 0, "dy": -500}
    assert any(c[0] == "move_to" for c in tab.actions.calls)
    assert any(c[0] == "hold" for c in tab.actions.calls)
    assert any(c[0] == "release" for c in tab.actions.calls)


async def test_x6_connect_ports(client, x6_seeded):
    """测试出口桩至入口桩拖拽连线。"""
    _, _, tab, _ = x6_seeded
    res = await client.call_tool("x6_connect", {"from_node": "n1", "to_node": "n2"})
    assert res.data["ok"] is True
    assert res.data["from"]["port"] == "out-0"
    assert res.data["to"]["port"] == "in-0"
    assert any(c[0] == "hold" for c in tab.actions.calls)
    assert any(c[0] == "release" for c in tab.actions.calls)


async def test_x6_click_node(client, x6_seeded):
    """测试单击与双击节点。"""
    _, _, tab, _ = x6_seeded
    res = await client.call_tool("x6_click_node", {"node": "审批A", "double": True})
    assert res.data["double"] is True
    assert res.data["node"]["cellId"] == "n2"
    assert any(c[0] == "click" for c in tab.actions.calls)


async def test_x6_add_node(client, x6_seeded):
    """测试从左侧物料栏长按拖拽追加节点。"""
    _, _, tab, frame = x6_seeded
    res = await client.call_tool("x6_add_node", {"kind": "审批人", "target_x": 600, "target_y": 400})
    assert res.data["ok"] is True
    assert res.data["kind"] == "审批人"
    assert res.data["drop_position"] == {"x": 600.0, "y": 400.0}
    assert any(c[0] == "hold" for c in tab.actions.calls)
    assert any(c[0] == "release" for c in tab.actions.calls)

async def test_x6_delete_node(client, x6_seeded):
    """测试选中节点并发送 Backspace 删除，响应标注 deleted_via 删除路径。"""
    _, _, tab, _ = x6_seeded
    res = await client.call_tool("x6_delete_node", {"node": "n2"})
    assert res.data["ok"] is True
    assert res.data["deleted_node"]["cellId"] == "n2"
    assert res.data["deleted_via"] in ("keyboard", "api")
    assert any(c[0] == "key_down" and c[1] == "BACKSPACE" for c in tab.actions.calls)


async def test_x6_node_not_found(client, x6_seeded):
    """节点不存在时抛出包含可用列表的 ToolError。"""
    with pytest.raises(ToolError, match="未在画布中找到节点"):
        await client.call_tool("x6_click_node", {"node": "不存在的节点"})


async def test_x6_port_not_found(client, x6_seeded):
    """端口不存在时抛出 ToolError。"""
    with pytest.raises(ToolError, match="无端口"):
        await client.call_tool("x6_connect", {"from_node": "n1", "to_node": "n2", "from_port": "no-such-port"})
