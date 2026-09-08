"""通过 FastMCP 内存客户端测试工具层。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeElement


async def test_server_lists_expected_tools(client):
    tools = await client.list_tools()
    names = {t.name for t in tools}
    expected = {
        "browser_launch",
        "browser_connect",
        "browser_close",
        "browser_status",
        "tab_new",
        "tab_list",
        "tab_close",
        "tab_info",
        "enable_dev_tool",
        "disable_dev_tool",
        "navigate",
        "navigate_back",
        "navigate_forward",
        "refresh",
        "wait_element",
        "get_page_info",
        "get_page_html",
        "find_element",
        "find_elements",
        "element_info",
        "element_click",
        "element_input",
        "element_hover",
        "element_select",
        "element_check",
        "element_scroll",
        "context_new",
        "context_close",
        "context_list",
        "cookies_get",
        "cookies_set",
        "cookies_clear",
    }
    assert expected <= names
    assert "run_js" not in names


async def test_browser_status_returns_seeded_session(client, seeded_manager):
    session, chromium, tab = seeded_manager
    result = await client.call_tool("browser_status", {})
    data = result.data
    assert len(data) == 1
    assert data[0].browser_id == session.browser_id
    assert data[0].kind == "launched"
    assert tab.tab_id in data[0].tab_ids


async def test_navigate_and_page_info(client, seeded_manager):
    _, _, tab = seeded_manager
    nav = await client.call_tool("navigate", {"url": "https://example.com/page"})
    assert nav.data.ok is True
    assert nav.data.url == "https://example.com/page"
    assert ("get", "https://example.com/page") in tab.steps

    info = await client.call_tool("get_page_info", {})
    assert info.data.url == tab.url
    assert info.data.ready_state == "complete"


async def test_navigate_failure_surfaced(client, seeded_manager):
    from conftest import FakeNav

    _, _, tab = seeded_manager
    tab.get = lambda url, timeout=None, **kw: FakeNav(url, ok=False, status="net::ERR_TIMED_OUT")
    nav = await client.call_tool("navigate", {"url": "https://bad.example.com"})
    assert nav.data.ok is False
    assert "net::ERR_TIMED_OUT" in str(nav.data.status)


async def test_wait_element(client, seeded_manager):
    from conftest import FakeElement as FE

    _, _, tab = seeded_manager
    tab.ele_result = FE(tag="div")
    result = await client.call_tool("wait_element", {"locator": "#btn", "timeout": 1})
    assert result.data.found is True

    class NoneElement:
        def __bool__(self):
            return False

    tab.ele_result = NoneElement()
    result = await client.call_tool("wait_element", {"locator": "#btn", "timeout": 1})
    assert result.data.found is False


async def test_get_page_html_truncates(client, seeded_manager):
    _, _, tab = seeded_manager
    result = await client.call_tool("get_page_html", {"max_chars": 5})
    assert result.data.truncated is True
    assert len(result.data.html) == 5


async def test_find_and_click_element(client, seeded_manager):
    _, _, tab = seeded_manager
    ele = FakeElement(text="提交")
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "text:提交"})
    element_id = found.data.element_id
    assert found.data.tag == "button"
    assert found.data.text == "提交"

    clicked = await client.call_tool("element_click", {"element_id": element_id})
    assert clicked.data["ok"] is True
    assert ("click", ele, 1) in tab.actions.calls


async def test_find_element_not_found(client, seeded_manager):
    _, _, tab = seeded_manager

    class NoneElement:
        def __bool__(self):
            return False

    tab.ele_result = NoneElement()
    with pytest.raises(ToolError, match="未找到元素"):
        await client.call_tool("find_element", {"locator": "#nope", "timeout": 0})


async def test_element_info_and_states(client, seeded_manager):
    _, _, tab = seeded_manager
    ele = FakeElement()
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "#btn1"})
    info = await client.call_tool("element_info", {"element_id": found.data.element_id})
    assert info.data.attrs["id"] == "btn1"
    assert info.data.rect["width"] == 80
    assert info.data.states["is_displayed"] is True


async def test_element_input_hover_check_select_scroll(client, seeded_manager):
    _, _, tab = seeded_manager
    ele = FakeElement(tag="input")
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "#kw"})
    eid = found.data.element_id

    await client.call_tool("element_input", {"element_id": eid, "text": "hello", "clear": True})
    assert ("input", "hello", True, False) in ele.actions

    await client.call_tool("element_hover", {"element_id": eid})
    assert ("hover",) in ele.actions

    await client.call_tool("element_check", {"element_id": eid, "checked": False})
    assert ("check", False) in ele.actions

    await client.call_tool("element_select", {"element_id": eid, "by": "text", "value": "选项"})
    await client.call_tool("element_scroll", {"element_id": eid, "action": "down", "pixel": 100})
    assert ("scroll_down", 100) in ele.actions

    with pytest.raises(ToolError, match="by 参数"):
        await client.call_tool("element_select", {"element_id": eid, "by": "bad", "value": "x"})


async def test_find_elements(client, seeded_manager):
    _, _, tab = seeded_manager
    tab.eles_result = [FakeElement(text=f"item{i}") for i in range(3)]
    result = await client.call_tool("find_elements", {"locator": ".item", "limit": 2})
    assert result.data.count == 2


async def test_stale_element_error(client, seeded_manager):
    _, _, tab = seeded_manager
    ele = FakeElement()
    tab.ele_result = ele
    found = await client.call_tool("find_element", {"locator": "#btn1"})
    ele.alive = False
    with pytest.raises(ToolError, match="已失效"):
        await client.call_tool("element_click", {"element_id": found.data.element_id})


async def test_tab_lifecycle_and_run_js(client, seeded_manager):
    session, chromium, tab = seeded_manager
    created = await client.call_tool("tab_new", {"url": "https://example.com/new"})
    assert created.data.tab_id in chromium.tabs

    tabs = await client.call_tool("tab_list", {})
    assert len(tabs.data) == 2

    # 默认 run_js 对外隐藏并禁用
    with pytest.raises(ToolError, match="Unknown tool"):
        await client.call_tool("run_js", {"script": "return 1 + 1"})

    # 传入用户明确指示后解锁
    unlock = await client.call_tool(
        "enable_dev_tool",
        {"name": "run_js", "user_explicit_instruction": "执行测试 JS 脚本"},
    )
    assert "已临时解锁" in unlock.data

    js = await client.call_tool("run_js", {"script": "return 1 + 1"})
    assert js.data == "js-ok"

    # 重新锁定
    relock = await client.call_tool("disable_dev_tool", {"name": "run_js"})
    assert "已锁定" in relock.data

    with pytest.raises(ToolError, match="Unknown tool"):
        await client.call_tool("run_js", {"script": "return 1 + 1"})

    await client.call_tool("tab_close", {"tab_id": created.data.tab_id})
    assert ("close", False) in chromium.tabs[created.data.tab_id].steps


async def test_enable_dev_tool_validation(client):
    with pytest.raises(ToolError, match="未受管控"):
        await client.call_tool(
            "enable_dev_tool",
            {"name": "unknown_tool", "user_explicit_instruction": "测试"},
        )

    with pytest.raises(ToolError, match="必须提供用户明确要求"):
        await client.call_tool(
            "enable_dev_tool",
            {"name": "run_js", "user_explicit_instruction": "   "},
        )
async def test_context_and_cookies(client, seeded_manager):
    _, chromium, tab = seeded_manager
    ctx = await client.call_tool("context_new", {})
    assert ctx.data.browser_id

    tab_new = await client.call_tool(
        "tab_new", {"url": "https://example.com/account", "context_id": ctx.data.context_id}
    )
    assert tab_new.data.context_id == ctx.data.context_id

    listed = await client.call_tool("context_list", {})
    assert any(c.context_id == ctx.data.context_id for c in listed.data)

    await client.call_tool(
        "cookies_set",
        {
            "cookies": [{"name": "token", "value": "abc", "domain": ".example.com"}],
        },
    )
    cookies = await client.call_tool("cookies_get", {})
    assert cookies.data.count == 1
    assert cookies.data.cookies[0]["name"] == "token"

    await client.call_tool("cookies_clear", {})
    cookies = await client.call_tool("cookies_get", {})
    assert cookies.data.count == 0

    await client.call_tool("context_close", {"context_id": ctx.data.context_id})


async def test_no_session_error_message(client):
    """全局 manager 清空后，调用需要浏览器的工具应报中文错误。"""
    from drissionpage_mcp.manager import manager as global_manager

    backup = dict(global_manager._sessions)
    global_manager._sessions.clear()
    try:
        with pytest.raises(ToolError, match="browser_launch"):
            await client.call_tool("navigate", {"url": "https://example.com"})
    finally:
        global_manager._sessions = backup


async def test_page_info_and_controls_breadcrumb(client, seeded_manager):
    """测试 get_page_info 与 page_controls 提取权威面包屑路径。"""
    import json
    _, _, tab = seeded_manager

    orig_run_js = tab.run_js

    def mock_run_js(script, *args, **kwargs):
        if "ant-breadcrumb" in script:
            return ["审批流管理", "审批单列表"]
        if "__pcap" in script:
            return json.dumps({"counts": {"button": 1}, "controls": [{"kind": "button", "text": "查询"}]})
        return orig_run_js(script, *args, **kwargs)

    tab.run_js = mock_run_js
    for f in tab.iframes:
        f.run_js = mock_run_js

    info = await client.call_tool("get_page_info", {})
    assert info.data.breadcrumb == "审批流管理 > 审批单列表"
    assert info.data.breadcrumb_items == ["审批流管理", "审批单列表"]

    controls = await client.call_tool("page_controls", {})
    assert controls.data["module_path"] == "审批流管理 > 审批单列表"
    assert controls.data["breadcrumb"] == ["审批流管理", "审批单列表"]


async def test_unified_click(client, seeded_manager):
    """测试统一全能 click 工具：支持 element_id、选择器、坐标点位与单双击。"""
    _, _, tab = seeded_manager
    btn = FakeElement(tag="button", text="保 存")
    tab.ele_result = btn

    # 1. 选择器点击
    res_sel = await client.call_tool("click", {"target": "text:保 存"})
    assert res_sel.data["ok"] is True
    assert "clicked" in res_sel.data

    # 2. 坐标点位点击
    res_pt = await client.call_tool("click", {"point": {"x": 300, "y": 450}, "button": "right"})
    assert res_pt.data["ok"] is True
    assert res_pt.data["clicked"]["x"] == 300.0
    assert res_pt.data["button"] == "right"

    # 3. 双击
    res_dbl = await client.call_tool("click", {"x": 200, "y": 150, "double": True})
    assert res_dbl.data["ok"] is True
    assert res_dbl.data["double"] is True
