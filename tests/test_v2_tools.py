"""v2 新能力测试：frame 支持、Actions 真实交互、AntD portal 工具。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeElement, FakeFrame


# ---------- frame ----------

async def test_frame_list(client, seeded_manager):
    _, chromium, _ = seeded_manager
    result = await client.call_tool("frame_list", {})
    frames = result.data
    assert len(frames) == 2
    assert frames[0].iframe_id == "react_iframe_111"
    assert frames[0].displayed is False
    assert frames[1].displayed is True


async def test_find_element_in_active_frame(client, seeded_manager):
    _, chromium, tab = seeded_manager
    active = tab.iframes[1]
    active.ele_result = FakeElement(tag="button", text="查询")
    found = await client.call_tool(
        "find_element", {"locator": "text:查询", "frame": "active"}
    )
    assert found.data.text == "查询"
    assert ("eles", "text:查询") in active.actions


async def test_find_element_by_iframe_id(client, seeded_manager):
    _, chromium, tab = seeded_manager
    frame = tab.iframes[0]
    frame.ele_result = FakeElement(tag="input")
    await client.call_tool("find_element", {"locator": ".ant-input", "frame": "react_iframe_111"})
    assert ("eles", "css:.ant-input") in frame.actions


async def test_find_element_invalid_frame(client, seeded_manager):
    with pytest.raises(ToolError, match="iframe"):
        await client.call_tool("find_element", {"locator": "#x", "frame": "nope"})


# ---------- action_chain / press_key ----------

async def test_action_chain_sequence(client, seeded_manager):
    _, chromium, tab = seeded_manager
    ele = FakeElement(tag="button")
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "#btn1"})
    eid = found.data.element_id

    result = await client.call_tool(
        "action_chain",
        {
            "steps": [
                {"action": "move_to", "element_id": eid},
                {"action": "click"},
                {"action": "type", "text": "hello", "interval": 0.05},
                {"action": "key_down", "key": "ENTER"},
                {"action": "key_up", "key": "ENTER"},
                {"action": "wait", "seconds": 0.5},
            ]
        },
    )
    assert result.data.ok is True
    assert result.data.steps_executed == 6
    calls = tab.actions.calls
    assert ("move_to", "button", None, None) in calls
    assert ("click", None, 1) in calls  # move_to 后在当前位置真实点击
    assert ("type", "hello", 0.05) in calls
    assert ("key_down", "ENTER") in calls
    assert ("key_up", "ENTER") in calls
    assert ("wait", 0.5) in calls


async def test_action_chain_move_to_coords(client, seeded_manager):
    _, chromium, tab = seeded_manager
    await client.call_tool(
        "action_chain",
        {"steps": [{"action": "move_to", "x": 100, "y": 200}]},
    )
    assert ("move_to", (100, 200), None, None) in tab.actions.calls


async def test_action_chain_unknown_action(client, seeded_manager):
    with pytest.raises(ToolError, match="未知操作"):
        await client.call_tool("action_chain", {"steps": [{"action": "fly"}]})


async def test_action_chain_missing_param(client, seeded_manager):
    with pytest.raises(ToolError, match="move_to"):
        await client.call_tool("action_chain", {"steps": [{"action": "move_to"}]})


async def test_press_key(client, seeded_manager):
    _, chromium, tab = seeded_manager
    await client.call_tool("press_key", {"key": "ESC"})
    calls = tab.actions.calls
    assert ("key_down", "ESCAPE") in calls  # ESC 别名归一化为 DP 的 ESCAPE
    assert ("key_up", "ESCAPE") in calls


# ---------- element_click 走 Actions ----------

async def test_element_click_uses_actions_by_default(client, seeded_manager):
    _, chromium, tab = seeded_manager
    ele = FakeElement(tag="button")
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "#btn1"})
    await client.call_tool("element_click", {"element_id": found.data.element_id})
    assert ("click", ele, 1) in tab.actions.calls


# ---------- AntD portal ----------

async def test_get_toasts(client, seeded_manager):
    _, chromium, tab = seeded_manager
    toast = FakeElement(tag="div", text="保存成功")
    noti = FakeElement(tag="div", text="系统通知")
    tab.eles_results = {
        "css:.ant-message-notice-content": [toast],
        "css:.ant-notification-notice": [noti],
    }
    result = await client.call_tool("get_toasts", {})
    assert result.data.message_texts == ["保存成功"]
    assert result.data.notification_texts == ["系统通知"]


async def test_antd_select_flow(client, seeded_manager):
    _, chromium, tab = seeded_manager
    select_ele = FakeElement(tag="div")
    tab.ele_result = select_ele
    found = await client.call_tool("find_element", {"locator": ".ant-select"})
    eid = found.data.element_id

    dropdown = FakeFrame("dropdown")
    opt1 = FakeElement(tag="div", text="选项一")
    opt2 = FakeElement(tag="div", text="目标选项")
    dropdown.eles_results = {"css:.ant-select-item-option": [opt1, opt2]}
    # 浮层已在 DOM 中且可见（has_box），无需再点击 select
    tab.eles_results = {
        "css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)": [dropdown]
    }

    result = await client.call_tool("antd_select", {"element_id": eid, "option_text": "目标"})
    assert result.data["ok"] is True
    calls = tab.actions.calls
    assert ("click", opt2, 1) in calls


async def test_antd_select_option_not_found(client, seeded_manager):
    _, chromium, tab = seeded_manager
    select_ele = FakeElement(tag="div")
    tab.ele_result = select_ele
    found = await client.call_tool("find_element", {"locator": ".ant-select"})
    eid = found.data.element_id

    dropdown = FakeFrame("dropdown")
    dropdown.eles_results = {"css:.ant-select-item-option": [FakeElement(tag="div", text="其他")]}
    tab.eles_results = {
        "css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)": [dropdown]
    }

    with pytest.raises(ToolError, match="未找到选项"):
        await client.call_tool("antd_select", {"element_id": eid, "option_text": "不存在"})


async def test_antd_get_options_paging(client, seeded_manager):
    """选项列表封顶 50 条 + truncated 标志，offset 翻页；total 为全量去重数。"""
    _, chromium, tab = seeded_manager
    tab.ele_result = FakeElement(tag="div")
    found = await client.call_tool("find_element", {"locator": ".ant-select"})
    eid = found.data.element_id

    dropdown = FakeFrame("dropdown")
    dropdown.eles_results = {
        "css:.ant-select-item-option": [
            FakeElement(tag="div", text=f"选项{i:03d}") for i in range(120)
        ]
    }
    tab.eles_results = {
        "css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)": [dropdown]
    }

    page1 = await client.call_tool("antd_get_options", {"element_id": eid})
    assert page1.data["total"] == 120
    assert len(page1.data["options"]) == 50
    assert page1.data["truncated"] is True
    assert page1.data["options"][0] == "选项000"

    page2 = await client.call_tool("antd_get_options", {"element_id": eid, "offset": 100})
    assert len(page2.data["options"]) == 20
    assert page2.data["truncated"] is False
    assert page2.data["options"][0] == "选项100"


async def test_antd_select_dropdown_never_shows(client, seeded_manager):
    _, chromium, tab = seeded_manager
    select_ele = FakeElement(tag="div")
    tab.ele_result = select_ele
    found = await client.call_tool("find_element", {"locator": ".ant-select"})

    class NoneElement:
        def __bool__(self):
            return False

    tab.eles_results = {
        "css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)": []
    }
    with pytest.raises(ToolError, match="下拉浮层未出现"):
        await client.call_tool(
            "antd_select", {"element_id": found.data.element_id, "option_text": "x", "timeout": 0.1}
        )
    # 浮层未出现时应已真实点击过 select
    assert ("click", select_ele, 1) in tab.actions.calls


async def test_antd_date_pick(client, seeded_manager):
    _, chromium, tab = seeded_manager
    picker_ele = FakeElement(tag="input")
    tab.ele_result = picker_ele
    found = await client.call_tool("find_element", {"locator": ".ant-picker"})
    eid = found.data.element_id

    picker = FakeFrame("picker")
    cell = FakeElement(tag="td", text="15")
    picker.ele_result = cell
    tab.ele_queue = [None, None, picker]

    result = await client.call_tool(
        "antd_date_pick", {"element_id": eid, "date": "2026-09-15"}
    )
    assert result.data.ok is True
    assert ('css:.ant-picker-cell[title="2026-09-15"]',) == picker.actions[0][0:1] or \
        ("ele", 'css:.ant-picker-cell[title="2026-09-15"]') in picker.actions
    assert ("click", cell, 1) in tab.actions.calls


async def test_antd_date_pick_bad_format(client, seeded_manager):
    _, chromium, tab = seeded_manager
    tab.ele_result = FakeElement(tag="input")
    found = await client.call_tool("find_element", {"locator": ".ant-picker"})
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        await client.call_tool(
            "antd_date_pick", {"element_id": found.data.element_id, "date": "2026/09/15"}
        )


async def test_antd_modal_click(client, seeded_manager):
    _, chromium, tab = seeded_manager
    ok_btn = FakeElement(tag="button", text="确 定")
    cancel_btn = FakeElement(tag="button", text="取 消")
    modal = FakeFrame("modal")
    modal.eles_results = {"css:.ant-modal-footer button": [cancel_btn, ok_btn]}
    tab.eles_results = {"css:.ant-modal": [modal]}

    result = await client.call_tool("antd_modal_click", {"button_text": "确定"})
    assert result.data.ok is True
    assert ("click", ok_btn, 1) in tab.actions.calls


async def test_antd_modal_not_visible(client, seeded_manager):
    _, chromium, tab = seeded_manager
    tab.eles_results = {".ant-modal": []}
    with pytest.raises(ToolError, match="未出现可见"):
        await client.call_tool(
            "antd_modal_click", {"button_text": "确定", "timeout": 0.2}
        )


# ---------- 定位符规范化 ----------

async def test_normalize_bare_class_locator(client, seeded_manager):
    """裸 .cls 定位符在 frame/相对检索中应自动加 css: 前缀（规避 DP b1 缺陷）。"""
    _, chromium, tab = seeded_manager
    active = tab.iframes[1]
    active.ele_result = FakeElement(tag="button")
    await client.call_tool("find_element", {"locator": ".ant-btn", "frame": "active"})
    assert ("eles", "css:.ant-btn") in active.actions
