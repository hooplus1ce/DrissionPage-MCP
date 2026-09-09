"""D6 截图工具 (screenshot) 与 D2 模块路由工具 (nav_menu) 单测（离线，Fake 环境）。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeElement, FakeFrame


# ==========================================
# D6: screenshot 单测
# ==========================================


async def test_screenshot_viewport_default(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    res = await client.call_tool("screenshot", {})
    assert len(res.content) == 2
    text_block, img_block = res.content
    assert text_block.type == "text"
    assert "截图成功" in text_block.text
    assert "视口" in text_block.text
    assert img_block.type == "image"
    assert any(step[0] == "get_screenshot" and step[1] is False for step in tab.steps)


async def test_screenshot_full_page(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    res = await client.call_tool("screenshot", {"full_page": True, "format": "jpeg"})
    assert len(res.content) == 2
    text_block, img_block = res.content
    assert "整页" in text_block.text
    assert "jpeg" in text_block.text
    assert any(step[0] == "get_screenshot" and step[1] is True for step in tab.steps)


async def test_screenshot_with_path(client, seeded_manager, tmp_path):
    _session, _chromium, _tab = seeded_manager
    save_file = tmp_path / "test_shot.png"
    res = await client.call_tool("screenshot", {"path": str(save_file)})
    assert len(res.content) == 2
    text_block = res.content[0]
    assert str(save_file) in text_block.text
    assert save_file.is_file()
    assert save_file.read_bytes() == b"\x89PNG-fake-tab-screenshot"


async def test_screenshot_element_id(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    from drissionpage_mcp.manager import manager

    ele = FakeElement("div", text="测试容器")
    eid = manager.register_element(ele, tab, _session.browser_id)

    res = await client.call_tool("screenshot", {"element_id": eid})
    assert len(res.content) == 2
    text_block, img_block = res.content
    assert f"元素 [id={eid}]" in text_block.text
    assert any(act[0] == "get_screenshot" for act in ele.actions)


async def test_screenshot_locator(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    ele = FakeElement("table", text="表格元素")
    tab.ele_result = ele

    res = await client.call_tool("screenshot", {"locator": "css:.ant-table"})
    assert len(res.content) == 2
    text_block, _ = res.content
    assert "css:.ant-table" in text_block.text
    assert any(act[0] == "get_screenshot" for act in ele.actions)


async def test_screenshot_frame(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    # tab.iframes[1] is react_iframe_222 (displayed=True)
    res = await client.call_tool("screenshot", {"frame": "active"})
    assert len(res.content) == 2
    text_block, _ = res.content
    assert "iframe [active]" in text_block.text


async def test_screenshot_invalid_element_id_raises(client, seeded_manager):
    with pytest.raises(ToolError, match="未找到元素"):
        await client.call_tool("screenshot", {"element_id": "nonexistent_999"})


# ==========================================
# D2: nav_menu 单测
# ==========================================


async def test_nav_menu_empty_name_raises(client, seeded_manager):
    with pytest.raises(ToolError, match="菜单名称不能为空"):
        await client.call_tool("nav_menu", {"menu_name": "  "})


async def test_nav_menu_reused_active_tab(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    # 模拟已有 tab
    tab_ele = FakeElement("div", text="采购订单")
    tab_ele.attrs["class"] = "ant-tabs-tab ant-tabs-tab-active"
    tab.eles_results["css:.ant-tabs-tab"] = [tab_ele]

    res = await client.call_tool("nav_menu", {"menu_name": "采购订单"})
    data = res.data
    assert data.ok is True
    assert data.menu_name == "采购订单"
    assert data.reused_tab is True
    assert data.tab_id == tab.tab_id


async def test_nav_menu_switches_inactive_tab(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab_ele = FakeElement("div", text="产线管理")
    tab_ele.attrs["class"] = "ant-tabs-tab"
    tab.eles_results["css:.ant-tabs-tab"] = [tab_ele]

    res = await client.call_tool("nav_menu", {"menu_name": "产线管理"})
    data = res.data
    assert data.ok is True
    assert data.menu_name == "产线管理"
    assert data.reused_tab is True
    assert any(act[0] == "click" for act in tab_ele.actions)


async def test_nav_menu_force_reload_closes_existing_tab(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab_ele = FakeElement("div", text="产线管理")
    close_btn = FakeElement("i", text="")
    tab_ele.ele_result = close_btn
    tab.eles_results["css:.ant-tabs-tab"] = [tab_ele]

    # 准备搜索到达菜单
    select_ele = FakeElement("div", text="到达菜单")
    search_input = FakeElement("input", text="")
    select_ele.ele_result = search_input
    dropdown = FakeElement("div", text="")
    menu_item = FakeElement("li", text="产线管理")
    dropdown.eles_results["css:.ant-select-dropdown-menu-item"] = [menu_item]

    tab.ele_results["css:.right-header .ant-select"] = select_ele
    tab.ele_results["css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)"] = dropdown
    tab.ele_results["css:.ant-select-dropdown"] = dropdown
    res = await client.call_tool("nav_menu", {"menu_name": "产线管理", "force_reload": True})
    assert any(act[0] == "click" for act in close_btn.actions)
    assert res.data.ok is True
    assert res.data.reused_tab is False


async def test_nav_menu_missing_select_raises(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab.eles_results["css:.ant-tabs-tab"] = []
    tab.ele_result = None

    with pytest.raises(ToolError, match="未找到顶部菜单导航框"):
        await client.call_tool("nav_menu", {"menu_name": "未知模块"})


async def test_nav_menu_item_not_found_raises(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab.eles_results["css:.ant-tabs-tab"] = []

    select_ele = FakeElement("div", text="到达菜单")
    search_input = FakeElement("input", text="")
    select_ele.ele_result = search_input
    dropdown = FakeElement("div", text="")
    dropdown.eles_results["css:.ant-select-dropdown-menu-item"] = []

    tab.ele_results["css:.right-header .ant-select"] = select_ele
    tab.ele_results["css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)"] = dropdown
    tab.ele_results["css:.ant-select-dropdown"] = dropdown
    with pytest.raises(ToolError, match="未找到名称包含"):
        await client.call_tool("nav_menu", {"menu_name": "不存在的菜单"})


# ==========================================
# D3: wait_message 单测
# ==========================================


async def test_wait_message_matched_antd_message(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    msg_ele = FakeElement("div", text="保存成功")
    msg_ele.attrs["class"] = "ant-message-notice ant-message-notice-success"
    tab.eles_results["css:.ant-message-notice"] = [msg_ele]

    res = await client.call_tool("wait_message", {"pattern": "保存.*", "timeout": 1.0})
    data = res.data
    assert data.found is True
    assert data.matched_text == "保存成功"
    assert data.source == "message"
    assert data.level == "success"
    assert "保存成功" in data.all_messages


async def test_wait_message_matched_notification(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    notif_ele = FakeElement("div", text="审批驳回提醒：预算超出限制")
    notif_ele.attrs["class"] = "ant-notification-notice ant-notification-notice-error"
    tab.eles_results["css:.ant-notification-notice"] = [notif_ele]

    res = await client.call_tool("wait_message", {"pattern": "驳回|超限", "timeout": 1.0})
    data = res.data
    assert data.found is True
    assert "审批驳回提醒" in data.matched_text
    assert data.source == "notification"
    assert data.level == "error"


async def test_wait_message_timeout_raises_tool_error(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab.eles_results[".ant-message-notice"] = []
    tab.eles_results[".ant-notification-notice"] = []

    with pytest.raises(ToolError, match="未等到匹配"):
        await client.call_tool("wait_message", {"pattern": "绝对不会出现的文本", "timeout": 0.5})


async def test_wait_message_raise_false_returns_found_false(client, seeded_manager):
    _session, _chromium, tab = seeded_manager
    tab.eles_results[".ant-message-notice"] = []
    tab.eles_results[".ant-notification-notice"] = []

    res = await client.call_tool(
        "wait_message",
        {"pattern": "找不到的消息", "timeout": 0.5, "raise_if_not_found": False},
    )
    data = res.data
    assert data.found is False
    assert data.matched_text is None
    assert data.all_messages == []
