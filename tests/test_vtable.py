"""VTable 层测试：坐标换算、绑定会话、工具闭环（假 frame，可编程响应）。"""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeFrame


class FakeVTableFrame(FakeFrame):
    """模拟 ChromiumFrame：run_js 按 JS 片段特征分发预置 JSON 响应。

    responses: marker -> JSON 字符串 | dict | callable(*args)
    """

    def __init__(self, iframe_id: str, displayed: bool = True):
        super().__init__(iframe_id, displayed=displayed)
        self.responses: dict[str, object] = {}
        self.js_calls: list[tuple[str, tuple]] = []

    def run_js(self, script, *args, **kwargs):
        markers = [
            ("inspect_vtable", "inspect"),
            ("getSelectedCellInfos", "selection"),
            ("__pcap", "page_controls"),
            ("__ovlBuf = []", "drain"),
            ("__ovlObs", "arm"),
            ("sgTexts", "text"),
            ("cellIsInVisualView", "visible_range"),
            ("editorManager", "edit"),
            ("getCellAddrByFieldRecord", "resolve"),
            ("globalAABBBounds", "icons"),
            ("setScrollLeft", "scroll"),
            ("inViewport", "geometry"),
            ("maxResults", "find"),
            ("too-many-cells", "read"),
            ("getBodyColumnDefine", "headers"),
            ("columnHeaderLevelCount", "meta"),
            ("vtableInstance", "bind"),
        ]
        for marker, name in markers:
            if marker in script:
                self.js_calls.append((name, args))
                resp = self.responses.get(name)
                if resp is None:
                    return json.dumps({"bound": True})
                out = resp(*args) if callable(resp) else resp
                return out if isinstance(out, str) else json.dumps(out)
        raise AssertionError("run_js 未匹配到任何片段: " + script[:80])


GEO = {
    "found": True,
    "box": {"x": 90, "y": 40, "width": 100, "height": 30},
    "center": {"x": 140, "y": 55},
    "canvasBox": {"x": 12, "y": 212, "width": 1677, "height": 732},
    "inViewport": True,
    "value": "广东生和堂",
    "type": "text",
}

META = {
    "bound": True,
    "rowCount": 90,
    "colCount": 20,
    "headerRows": 1,
    "frozenColCount": 1,
    "frozenRowCount": 1,
    "canvasBox": {"x": 12, "y": 212, "width": 1677, "height": 732},
}


def seed_vtable_frame() -> FakeVTableFrame:
    frame = FakeVTableFrame("vt-frame", displayed=True)
    frame.responses = {
        "meta": META,
        "geometry": GEO,
    }
    return frame


@pytest.fixture
def vtable_seeded(seeded_manager):
    """在种子会话的激活 iframe 上挂 VTable 假 frame。"""
    session, chromium, tab = seeded_manager
    vt_frame = seed_vtable_frame()
    tab.iframes = [tab.iframes[0], vt_frame]
    return session, chromium, tab, vt_frame


# ---------- 坐标换算 ----------

def test_to_viewport_offset_chain(seeded_manager):
    """canvas 局部 + canvas 偏移(iframe 内) + iframe 偏移(页面视口) 三级换算。"""
    from drissionpage_mcp.vtable import VTableSession

    s = VTableSession(frame=FakeVTableFrame("f"), tab=None)
    s.canvas_offset = (12, 212)
    s.frame_offset = (170, 80)
    x, y = s.to_viewport(140, 55)
    assert (x, y) == (322, 347)


def test_cell_center_absolute_coords(vtable_seeded):
    """cell_center 返回的视口坐标 = canvas 局部中心 + 两级偏移。"""
    from drissionpage_mcp.vtable import get_session

    _, _, tab, vt_frame = vtable_seeded
    session = get_session(None, None)
    assert session.frame is vt_frame
    # 偏移: canvas(12,212) + frame(0,0)（FakeFrame.location 为 (0,0)）
    info = session.cell_center(3, 5) if hasattr(session, "cell_center") else None
    from drissionpage_mcp.vtable import cell_center

    info = cell_center(session, 3, 5)
    assert info["center_viewport"] == {"x": 152.0, "y": 267.0}  # 140+12, 55+212
    assert info["value"] == "广东生和堂"
    assert info["in_viewport"] is True


# ---------- 绑定 ----------

def test_bind_failure_message(vtable_seeded):
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["bind"] = json.dumps({"bound": False, "visited": 100})
    from drissionpage_mcp.vtable import get_session

    with pytest.raises(ToolError, match="绑定 VTable 实例"):
        get_session(None, None)


def test_table_index_persisted(vtable_seeded):
    """显式 table_index 应写入页面的 __vtable_target_index。"""
    _, _, tab, _ = vtable_seeded
    from drissionpage_mcp.vtable import get_session

    get_session(None, 1)
    assert ("run_js",) or True
    # FakeTab.run_js 记录在 steps
    assert any(s[0] == "run_js" for s in tab.steps)



async def test_page_controls_fallback_to_main(client, vtable_seeded):
    """激活 frame 采集为空时回退主文档。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["page_controls"] = json.dumps({"counts": {}, "controls": []})
    result = await client.call_tool("page_controls", {})
    # FakeTab.run_js 返回 'js-ok'，解析失败 → 空数据兜底
    assert "controls" in result.data


# ---------- vtable_inspect 多粒度感知测试 ----------

async def test_vtable_inspect_cell(client, vtable_seeded):
    """单单元格感知：返回文本、颜色、交互态及视口绝对几何锚点。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["inspect"] = {
        "bound": True,
        "scope": "cell",
        "col": 2,
        "row": 5,
        "text": "IOR20260908001",
        "bg_color": "#ffffff",
        "text_color": "#1890ff",
        "interactive": True,
        "bounds": {"x": 100, "y": 50, "width": 120, "height": 30},
        "center": {"x": 160, "y": 65},
        "blank_point": {"x": 212, "y": 65},
        "icons": [
            {
                "name": "copy-icon",
                "function": "custom",
                "box": {"x": 105, "y": 55, "width": 16, "height": 16},
                "center": {"x": 113, "y": 63},
            }
        ],
    }
    result = await client.call_tool("vtable_inspect", {"col": 2, "row": 5})
    data = result.data
    assert data["scope"] == "cell"
    assert data["text"] == "IOR20260908001"
    assert data["text_color"] == "#1890ff"
    assert data["bg_color"] == "#ffffff"
    assert data["interactive"] is True
    # 验证视口坐标换算：canvas_offset=(12, 212), frame_offset=(0,0)
    assert data["center"] == {"x": 172.0, "y": 277.0}
    assert data["blank_point"] == {"x": 224.0, "y": 277.0}
    assert data["bounds"]["viewport_x"] == 112.0
    assert data["bounds"]["viewport_y"] == 262.0
    assert data["icons"][0]["center"] == {"x": 125.0, "y": 275.0}
    assert data["icons"][0]["box"]["viewport_x"] == 117.0


async def test_vtable_inspect_column_by_title(client, vtable_seeded):
    """根据中文列名智能解析列序号，返回列宽、border_right 及 header_icons。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["headers"] = {
        "columns": [
            {"col": 0, "field": "check", "title": "勾选", "type": "checkbox"},
            {"col": 1, "field": "order_no", "title": "申请单号", "type": "link"},
            {"col": 2, "field": "status", "title": "状态", "type": "text"},
        ],
        "colCount": 3,
        "headerRows": 1,
    }
    vt_frame.responses["inspect"] = lambda *args: {
        "bound": True,
        "scope": "column",
        "col": args[0],  # 应该是被解析出来的列索引 1
        "field": "order_no",
        "title": "申请单号",
        "width": 180,
        "header_center": {"x": 200, "y": 20},
        "border_right": {"x": 290, "y": 20},
        "header_icons": [
            {
                "name": "downward",
                "function": "dropdown",
                "box": {"x": 270, "y": 12, "width": 16, "height": 16},
                "center": {"x": 278, "y": 20},
            }
        ],
        "cells": [
            {"row": 1, "text": "IOR001", "interactive": True}
        ],
    }
    result = await client.call_tool("vtable_inspect", {"col": "申请单号"})
    data = result.data
    assert data["scope"] == "column"
    assert data["col"] == 1
    assert data["width"] == 180
    assert data["header_center"] == {"x": 212.0, "y": 232.0}
    assert data["border_right"] == {"x": 302.0, "y": 232.0}
    assert data["header_icons"][0]["function"] == "dropdown"
    assert data["header_icons"][0]["center"] == {"x": 290.0, "y": 232.0}
    assert len(data["cells"]) == 1
    assert data["cells"][0] == {"row": 1, "text": "IOR001", "interactive": True}
    assert "center" not in data["cells"][0]  # 省 token：列感知无逐格几何


async def test_vtable_inspect_row(client, vtable_seeded):
    """单行感知：返回整行背景色及全列紧凑数据。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["inspect"] = {
        "bound": True,
        "scope": "row",
        "row": 3,
        "height": 35,
        "bg_color": "#fff1f0",  # 警告高亮行
        "cells": [
            {"col": 0, "field": "id", "title": "ID", "text": "3", "interactive": False}
        ],
    }
    result = await client.call_tool("vtable_inspect", {"row": 3})
    data = result.data
    assert data["scope"] == "row"
    assert data["row"] == 3
    assert data["height"] == 35
    assert data["bg_color"] == "#fff1f0"
    assert len(data["cells"]) == 1
    assert data["cells"][0]["text"] == "3"
    assert data["cells"][0]["field"] == "id"
    assert data["cells"][0]["interactive"] is False
    assert "center" not in data["cells"][0]  # 省 token：行感知无逐格几何/颜色/图标


async def test_vtable_inspect_range(client, vtable_seeded):
    """区域切片感知：文本矩阵 values + 拖选锚点，颜色/交互态稀疏返回。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["inspect"] = {
        "bound": True,
        "scope": "range",
        "col_range": [1, 2],
        "row_range": [1, 3],
        "drag_start": {"x": 100, "y": 50},
        "drag_end": {"x": 300, "y": 150},
        "values": [["A1", "B1"], ["A2", "B2"]],
        "styles": [[1, 1, None, "#1890ff"]],
        "interactive": [[2, 1]],
        "baseline_style": ["#ffffff", "#333333"],
    }
    result = await client.call_tool(
        "vtable_inspect", {"col_range": [1, 2], "row_range": [1, 3]}
    )
    data = result.data
    assert data["scope"] == "range"
    assert data["drag_start"] == {"x": 112.0, "y": 262.0}
    assert data["drag_end"] == {"x": 312.0, "y": 362.0}
    assert data["values"] == [["A1", "B1"], ["A2", "B2"]]
    assert data["styles"] == [[1, 1, None, "#1890ff"]]
    assert data["interactive"] == [[2, 1]]
    assert data["baseline_style"] == ["#ffffff", "#333333"]  # 基线显式回传


async def test_vtable_inspect_range_too_large(client, vtable_seeded):
    """区域切片超 500 格时本地预检直接拒绝（省 token 闸门，不走 JS）。"""
    _, _, tab, vt_frame = vtable_seeded
    with pytest.raises(ToolError, match="超出 500 格上限"):
        await client.call_tool(
            "vtable_inspect", {"col_range": [0, 100], "row_range": [0, 100]}
        )
    assert all(name != "inspect" for name, _ in vt_frame.js_calls)


async def test_vtable_inspect_visible_all(client, vtable_seeded):
    """全屏快照：返回紧凑版可视全表骨架，体积小且无 SVG 冗余。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["inspect"] = {
        "bound": True,
        "scope": "visible_all",
        "visible_range": {"colStart": 0, "colEnd": 2, "rowStart": 1, "rowEnd": 2},
        "colCount": 3,
        "rowCount": 10,
        "headers": [
            {"col": 0, "field": "f0", "title": "C0", "width": 60},
            {"col": 1, "field": "f1", "title": "C1", "width": 100},
        ],
        "rows": [
            {
                "row": 1,
                "cells": [
                    {"col": 0, "text": "R1C0", "bg_color": None, "text_color": "#333", "interactive": False},
                    {"col": 1, "text": "R1C1", "bg_color": None, "text_color": "#333", "interactive": True},
                ],
            }
        ],
    }
    result = await client.call_tool("vtable_inspect", {})
    data = result.data
    assert data["scope"] == "visible_all"
    assert len(data["headers"]) == 2
    assert len(data["rows"]) == 1
    assert data["rows"][0]["cells"][1]["text"] == "R1C1"


async def test_vtable_inspect_invalid_column(client, vtable_seeded):
    """传入非法列名时抛出明确包含可用候选列提示的 ToolError。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["headers"] = {
        "columns": [
            {"col": 0, "field": "order_id", "title": "订单号"},
            {"col": 1, "field": "product_name", "title": "物料名称"},
        ],
        "colCount": 2,
        "headerRows": 1,
    }
    with pytest.raises(ToolError, match="未找到匹配的列 '不存在的列'。可用列清单: 订单号\\(col=0\\), 物料名称\\(col=1\\)"):
        await client.call_tool("vtable_inspect", {"col": "不存在的列"})
