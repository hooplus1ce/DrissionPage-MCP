"""APS 系统（React + AntDesign + VTable）适配性只读探测。

全部为只读操作：仅调用定位/信息类工具，不点击、不输入、不导航。
"""

import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

CONNECT_ADDR = "127.0.0.1:9222"


async def call(c: Client, name: str, args: dict | None = None, step: str = ""):
    step = step or name
    try:
        res = await c.call_tool(name, args or {})
        print(f"[PASS] {step}")
        return res
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {step} | {type(e).__name__}: {str(e)[:200]}")
        return None


async def main() -> None:
    transport = StdioTransport(sys.executable, ["-m", "drissionpage_mcp"])
    async with Client(transport) as c:
        r = await call(c, "browser_connect", {"address": CONNECT_ADDR})
        if not r:
            return
        browser_id = r.data.browser_id

        r = await call(c, "tab_list", {"browser_id": browser_id}, "列出标签页")
        if not r or not r.data:
            return
        tab_id = r.data[0].tab_id
        print(f"    目标标签页: {r.data[0].title} | {r.data[0].url}")

        # ---------- AntDesign 组件类定位 ----------
        for locator, label in [
            (".ant-btn", "ant 按钮类简写定位"),
            ("css:.ant-btn", "ant 按钮 css: 显式定位"),
            (".ant-input", "ant 输入框"),
            (".ant-menu-item", "ant 菜单项"),
            ("tag:button", "页面全部 button"),
        ]:
            r = await call(
                c,
                "find_elements",
                {"locator": locator, "tab_id": tab_id, "limit": 6, "timeout": 2},
                f"定位 {label} ({locator})",
            )
            if r:
                for e in r.data.elements[:3]:
                    print(f"    <{e.tag}> {e.text!r:.40} {e.css_selector[:60] if e.css_selector else ''}")

        # ---------- SPA 动态渲染等待 ----------
        r = await call(
            c,
            "wait_element",
            {"locator": ".ant-btn", "tab_id": tab_id, "timeout": 8},
            "等待 ant 按钮渲染(SPA)",
        )

        # ---------- 5.0 无障碍(ax:)定位 ----------
        for locator, label in [
            ("ax:@role=menuitem", "ax 菜单项"),
            ("ax:@role=link", "ax 链接"),
        ]:
            r = await call(
                c,
                "find_elements",
                {"locator": locator, "tab_id": tab_id, "limit": 4, "timeout": 2},
                f"5.0 ax: 定位 {label}",
            )
            if r:
                for e in r.data.elements[:3]:
                    print(f"    <{e.tag}> {e.text!r:.40}")

        # ---------- VTable / 表格结构探测 ----------
        for locator, label in [
            (".ant-table", "ant Table 组件"),
            ("tag:canvas", "VTable canvas 画布"),
            ("tag:table", "原生 table"),
            (".vtable", "vtable 类名"),
        ]:
            r = await call(
                c,
                "find_elements",
                {"locator": locator, "tab_id": tab_id, "limit": 3, "timeout": 2},
                f"探测 {label} ({locator})",
            )
            if r and r.data.count:
                for e in r.data.elements[:2]:
                    print(f"    <{e.tag}> rect 可从 element_info 获取, css={e.css_selector[:60] if e.css_selector else ''}")

        r = await call(
            c,
            "element_info",
            {"element_id": None},
            "占位(不应调用)",
        ) if False else None

        # canvas 元素详情（VTable 场景的关键点：canvas 内内容无法用 DOM 定位）
        r = await call(
            c,
            "find_elements",
            {"locator": "tag:canvas", "tab_id": tab_id, "limit": 1, "timeout": 2},
            "canvas 元素定位",
        )
        if r and r.data.count:
            eid = r.data.elements[0].element_id
            r2 = await call(c, "element_info", {"element_id": eid}, "canvas 元素详情")
            if r2:
                print(f"    canvas rect={r2.data.rect}")


if __name__ == "__main__":
    asyncio.run(main())
