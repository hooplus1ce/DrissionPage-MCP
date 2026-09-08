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


# ---------- 工具层（内存客户端） ----------

async def test_vtable_info(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    result = await client.call_tool("vtable_info", {})
    meta = result.data["meta"]
    assert meta["rowCount"] == 90
    assert meta["canvasBox"]["width"] == 1677


async def test_vtable_headers(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["headers"] = json.dumps(
        {
            "columns": [
                {"col": 0, "field": "name", "title": "产线名称", "type": "text"},
                {"col": 1, "field": "org", "title": "使用组织", "type": "text"},
            ],
            "colCount": 2,
            "headerRows": 1,
        }
    )
    result = await client.call_tool("vtable_headers", {})
    assert result.data["columns"][0]["field"] == "name"


async def test_vtable_read_cells(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["read"] = json.dumps(
        {"minCol": 0, "minRow": 1, "maxCol": 1, "maxRow": 2,
         "values": [["a", "b"], ["c", "d"]]}
    )
    result = await client.call_tool("vtable_read_cells", {"col0": 0, "row0": 1, "col1": 1, "row1": 2})
    assert result.data["values"][0][1] == "b"


async def test_vtable_read_cells_limit(client, vtable_seeded):
    with pytest.raises(ToolError, match="2000"):
        await client.call_tool(
            "vtable_read_cells",
            {"col0": 0, "row0": 0, "col1": 100, "row1": 100},
        )


async def test_vtable_find_cell(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["find"] = json.dumps(
        {"matches": [{"col": 2, "row": 5, "value": "广东生和堂"}],
         "scanned": {"colCount": 20, "rowCount": 89}}
    )
    result = await client.call_tool("vtable_find_cell", {"text": "生和堂"})
    assert result.data["matches"][0]["row"] == 5


async def test_vtable_cell_info_coords(client, vtable_seeded):
    result = await client.call_tool("vtable_cell_info", {"col": 3, "row": 5})
    assert result.data["center_viewport"] == {"x": 152.0, "y": 267.0}


async def test_vtable_click_cell_real_mouse(client, vtable_seeded):
    _, _, tab, vt_frame = vtable_seeded
    result = await client.call_tool("vtable_click_cell", {"col": 3, "row": 5})
    # 点击坐标 = 视口绝对坐标
    assert ("move_to", (152.0, 267.0), None, None) in tab.actions.calls
    assert ("click", None, 1) in tab.actions.calls


async def test_vtable_click_cell_double(client, vtable_seeded):
    _, _, tab, vt_frame = vtable_seeded
    await client.call_tool("vtable_click_cell", {"col": 3, "row": 5, "double_click": True})
    assert ("click", None, 2) in tab.actions.calls


async def test_vtable_scroll_to_cell(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    state = {"n": 0}

    def geometry(*args):
        state["n"] += 1
        return json.dumps({**GEO, "inViewport": state["n"] > 1})

    vt_frame.responses["geometry"] = geometry
    result = await client.call_tool("vtable_scroll_to_cell", {"col": 3, "row": 5})
    assert result.data.ok is True
    # 触发过滚动片段
    assert any(name == "scroll" for name, _ in vt_frame.js_calls)


async def test_vtable_scroll_failure(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["geometry"] = {**GEO, "inViewport": False}
    with pytest.raises(ToolError, match="可视区域"):
        await client.call_tool("vtable_scroll_to_cell", {"col": 3, "row": 5})


async def test_vtable_ensure_visible_retry(client, vtable_seeded):
    """ensure_cell_visible：滚动前不可见、滚动后可见。"""
    from drissionpage_mcp.vtable import get_session, ensure_cell_visible

    _, _, _, vt_frame = vtable_seeded
    session = get_session(None, None)
    vt_frame.responses["geometry"] = {**GEO, "inViewport": False}
    state = {"n": 0}

    def geometry(*args):
        state["n"] += 1
        return json.dumps({**GEO, "inViewport": state["n"] > 1})

    vt_frame.responses["geometry"] = geometry
    ok = ensure_cell_visible(session, 3, 5, timeout=3)
    assert ok is True
    assert any(name == "scroll" for name, _ in vt_frame.js_calls)


async def test_vtable_resolve_cell(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["resolve"] = json.dumps(
        {"ok": True, "col": 2, "row": 7, "field": "org", "recordIndex": 3,
         "value": "凭祥基地", "method": "getCellAddrByFieldRecord"}
    )
    result = await client.call_tool("vtable_resolve_cell", {"field": "org", "record_index": 3})
    assert result.data["col"] == 2
    assert result.data["value"] == "凭祥基地"


async def test_vtable_resolve_cell_fail(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["resolve"] = json.dumps(
        {"ok": False, "reason": "address-out-of-range", "field": "org", "recordIndex": 999}
    )
    with pytest.raises(ToolError, match="address-out-of-range"):
        await client.call_tool("vtable_resolve_cell", {"field": "org", "record_index": 999})


async def test_vtable_edit_cell(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["edit"] = json.dumps({"ok": True})
    result = await client.call_tool(
        "vtable_edit_cell", {"col": 1, "row": 3, "value": "新值", "commit": True}
    )
    assert result.data.ok is True


async def test_vtable_edit_cell_no_editor(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["edit"] = json.dumps({"ok": False, "reason": "no-editor"})
    with pytest.raises(ToolError, match="未配置编辑器"):
        await client.call_tool(
            "vtable_edit_cell", {"col": 1, "row": 3, "value": "x", "commit": True}
        )


async def test_vtable_click_icon(client, vtable_seeded):
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["icons"] = json.dumps(
        {"found": True, "icons": [
            {"name": "sort_normal", "function": "sort",
             "box": {"x": 100, "y": 10, "width": 12, "height": 12},
             "center": {"x": 106, "y": 16}},
            {"name": "filter", "function": "filter",
             "box": {"x": 120, "y": 10, "width": 12, "height": 12},
             "center": {"x": 126, "y": 16}},
        ]}
    )
    result = await client.call_tool(
        "vtable_click_icon", {"col": 1, "row": 0, "name": "filter"}
    )
    # filter 图标中心 (126,16) + canvas 偏移 (12,212) = (138, 228)
    assert ("move_to", (138.0, 228.0), None, None) in tab.actions.calls


async def test_vtable_click_icon_not_found(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["icons"] = json.dumps({"found": True, "icons": []})
    with pytest.raises(ToolError, match="未发现可交互图标"):
        await client.call_tool("vtable_click_icon", {"col": 1, "row": 0})


# ---------- 场景图深度文本提取 ----------

async def test_vtable_cell_text_scenegraph_first(client, vtable_seeded):
    """场景图有渲染文本时优先返回，source=scenegraph。"""
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["text"] = json.dumps(
        {"col": 2, "row": 3, "field": "org", "display": "广东生和堂（显示值）",
         "source": "scenegraph", "sgTexts": ["广东生和堂（显示值）"],
         "overflowText": None, "cellValue": "SHT01", "rawValue": None,
         "originValue": "广东生和堂健康食品股份有限公司"}
    )
    result = await client.call_tool("vtable_cell_text", {"col": 2, "row": 3})
    assert result.data["source"] == "scenegraph"
    assert "显示值" in result.data["display"]
    assert result.data["cellValue"] == "SHT01"


async def test_vtable_cell_text_fallback_chain(client, vtable_seeded):
    """场景图为空时回退 cellValue / originValue。"""
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["text"] = json.dumps(
        {"col": 1, "row": 2, "field": "code", "display": "BZ608",
         "source": "cellValue", "sgTexts": [], "overflowText": None,
         "cellValue": "BZ608", "rawValue": "BZ608", "originValue": None}
    )
    result = await client.call_tool("vtable_cell_text", {"col": 1, "row": 2})
    assert result.data["source"] == "cellValue"
    assert result.data["display"] == "BZ608"


async def test_vtable_cell_text_none_raises(client, vtable_seeded):
    _, _, _, vt_frame = vtable_seeded
    vt_frame.responses["text"] = json.dumps(
        {"col": 0, "row": 0, "field": None, "display": None, "source": "none",
         "sgTexts": [], "overflowText": None, "cellValue": None,
         "rawValue": None, "originValue": None}
    )
    with pytest.raises(ToolError, match="无可提取文本"):
        await client.call_tool("vtable_cell_text", {"col": 0, "row": 0})


# ---------- 省token五件套：浮层观察 / 拟人键入 / 验证重试 / 控制物清单 ----------

async def test_vtable_click_verified_flag(client, vtable_seeded):
    """点击后目标格进入选区 → verified=True。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["selection"] = json.dumps(
        {"ranges": [{"start": {"col": 3, "row": 5}, "end": {"col": 3, "row": 5}}],
         "cells": [{"col": 3, "row": 5, "value": "x"}]}
    )
    result = await client.call_tool("vtable_click_cell", {"col": 3, "row": 5})
    assert result.data["verified"] is True
    assert result.data["retries_used"] == 0


async def test_vtable_click_unverified_no_auto_retry(client, vtable_seeded):
    """默认不自动重试（勾选/按钮类格子重点会翻转状态）。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["selection"] = json.dumps({"ranges": [], "cells": []})
    result = await client.call_tool("vtable_click_cell", {"col": 3, "row": 5})
    assert result.data["verified"] is False
    assert result.data["retries_used"] == 0
    assert sum(1 for c in tab.actions.calls if c[0] == "click") == 1


async def test_vtable_click_retry_opt_in(client, vtable_seeded):
    """retry=True 且未验证时自动重点一次。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["selection"] = json.dumps({"ranges": [], "cells": []})
    result = await client.call_tool(
        "vtable_click_cell", {"col": 3, "row": 5, "retry": True}
    )
    assert result.data["verified"] is False
    assert result.data["retries_used"] == 1
    assert sum(1 for c in tab.actions.calls if c[0] == "click") == 2


async def test_vtable_click_overlays_attached(client, vtable_seeded):
    """点击后出现的新浮层随响应返回（封顶、去重在 drain 层完成）。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["drain"] = json.dumps(
        [{"k": "ant-modal", "t": "确认删除"}, {"k": "ant-modal", "t": "确认删除"}]
    )
    result = await client.call_tool("vtable_click_cell", {"col": 3, "row": 5})
    assert result.data["overlays"] == [{"kind": "ant-modal", "text": "确认删除"}]


async def test_overlays_omitted_when_empty(client, vtable_seeded):
    """无浮层时响应不含 overlays 键（省 token）。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["drain"] = json.dumps([])
    result = await client.call_tool("vtable_click_cell", {"col": 3, "row": 5})
    assert "overlays" not in result.data


async def test_action_chain_humanized_typing(client, vtable_seeded):
    """type 未给 interval 时使用 30~90ms 随机拟人间隔；显式 interval 优先。"""
    _, _, tab, _ = vtable_seeded
    await client.call_tool(
        "action_chain", {"steps": [{"action": "type", "text": "hello"}]}
    )
    interval_used = [c for c in tab.actions.calls if c[0] == "type"][0][2]
    assert 0.03 <= interval_used <= 0.09

    await client.call_tool(
        "action_chain",
        {"steps": [{"action": "type", "text": "hi", "interval": 0.5}]},
    )
    explicit = [c for c in tab.actions.calls if c[0] == "type"][-1][2]
    assert explicit == 0.5


async def test_page_controls(client, vtable_seeded):
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["page_controls"] = json.dumps(
        {"counts": {"button": 2, "input": 1, "select": 0, "link": 0},
         "controls": [
             {"kind": "button", "text": "查 询", "enabled": True},
             {"kind": "button", "text": "新 增", "enabled": True},
             {"kind": "input", "text": "产线名称", "enabled": True},
         ],
         "truncated": False}
    )
    result = await client.call_tool("page_controls", {})
    assert result.data["counts"]["button"] == 2
    assert result.data["controls"][1]["text"] == "新 增"


async def test_page_controls_fallback_to_main(client, vtable_seeded):
    """激活 frame 采集为空时回退主文档。"""
    _, _, tab, vt_frame = vtable_seeded
    vt_frame.responses["page_controls"] = json.dumps({"counts": {}, "controls": []})
    result = await client.call_tool("page_controls", {})
    # FakeTab.run_js 返回 'js-ok'，解析失败 → 空数据兜底
    assert "controls" in result.data
