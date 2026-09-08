"""真浏览器冒烟测试：设置环境变量 DPMCP_SMOKE=1 且本机装有 Chrome 时执行。"""

from __future__ import annotations


async def test_smoke_launch_navigate_find(client, run_smoke):
    launched = await client.call_tool("browser_launch", {"headless": True})
    browser_id = launched.data.browser_id
    try:
        tab = await client.call_tool(
            "tab_new", {"url": "https://example.com/", "browser_id": browser_id}
        )
        nav = await client.call_tool(
            "navigate", {"url": "https://example.com/", "tab_id": tab.data.tab_id}
        )
        assert nav.data.ok is True

        found = await client.call_tool(
            "find_element", {"locator": "tag:h1", "tab_id": tab.data.tab_id}
        )
        assert found.data.tag == "h1"

        info = await client.call_tool("get_page_info", {"tab_id": tab.data.tab_id})
        assert "example" in (info.data.url or "")
    finally:
        await client.call_tool("browser_close", {"browser_id": browser_id, "force": True})
