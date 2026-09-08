"""端到端检查脚本：通过 stdio 子进程启动 DrissionPage-MCP，对接 9222 端口真实浏览器。

安全约定：
- 对用户已打开的标签页只做只读操作（页面信息/元素定位/HTML/cookies 读取）
- 交互类操作（点击/输入）只在脚本自建的标签页中执行
- 结束时只清理脚本自建的标签页与上下文，不调用 browser_close 之外的危险操作
"""

import asyncio
import json
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

CONNECT_ADDR = "127.0.0.1:9222"

results: list[tuple[str, bool, str]] = []


def record(step: str, ok: bool, detail: str = "") -> None:
    results.append((step, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {step}" + (f" | {detail}" if detail else ""))


async def call(c: Client, name: str, args: dict | None = None, step: str = ""):
    step = step or name
    try:
        res = await c.call_tool(name, args or {})
        record(step, True)
        return res
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("\n", " ")[:300]
        record(step, False, f"{type(e).__name__}: {msg}")
        return None


async def main() -> int:
    transport = StdioTransport(sys.executable, ["-m", "drissionpage_mcp"])
    async with Client(transport) as c:
        tools = await c.list_tools()
        record("服务启动并列出工具", len(tools) >= 25, f"共 {len(tools)} 个工具")

        # ---------- 1. 接管已有浏览器 ----------
        r = await call(c, "browser_connect", {"address": CONNECT_ADDR}, "接管 9222 浏览器")
        if r is None:
            return summary()
        browser_id = r.data.browser_id
        print(f"    browser_id={browser_id} kind={r.data.kind} alive={r.data.is_alive}")

        r = await call(c, "browser_status", {}, "查询会话状态")
        if r:
            for b in r.data:
                print(f"    会话 {b.browser_id}: {b.address} tabs={len(b.tab_ids)}")

        # ---------- 2. 标签页清单（只读） ----------
        r = await call(c, "tab_list", {"browser_id": browser_id}, "列出所有标签页")
        if r is None:
            return summary()
        tabs = r.data
        for t in tabs:
            print(f"    tab {t.tab_id}: {(t.title or '')[:40]} | {(t.url or '')[:70]}")
        if not tabs:
            record("存在可用标签页", False, "浏览器没有标签页，后续用例跳过")
            return summary()

        # ---------- 3. 页面信息（对 APS 页只读） ----------
        target = tabs[0]
        r = await call(
            c,
            "get_page_info",
            {"tab_id": target.tab_id},
            f"读取页面信息 tab={target.tab_id}",
        )
        if r:
            print(f"    url={r.data.url} title={r.data.title} ready={r.data.ready_state}")

        r = await call(
            c,
            "wait_element",
            {"locator": "tag:body", "tab_id": target.tab_id, "timeout": 5},
            "等待 body 元素",
        )

        r = await call(
            c,
            "find_element",
            {"locator": "tag:body", "tab_id": target.tab_id, "timeout": 5},
            "定位 body 元素",
        )
        body_eid = r.data.element_id if r else None
        if r:
            print(f"    element_id={r.data.element_id} tag={r.data.tag}")

        r = await call(
            c,
            "element_info",
            {"element_id": body_eid},
            "读取元素详情",
        ) if body_eid else None
        if r:
            print(f"    attrs keys={list(r.data.attrs.keys())[:6]} rect={r.data.rect}")

        r = await call(
            c,
            "find_elements",
            {"locator": "tag:a", "tab_id": target.tab_id, "limit": 5, "timeout": 3},
            "批量定位链接元素",
        )
        if r:
            print(f"    找到 {r.data.count} 个链接")

        r = await call(
            c,
            "get_page_html",
            {"tab_id": target.tab_id, "max_chars": 3000},
            "读取页面 HTML(截断 3000 字符)",
        )
        if r:
            print(f"    truncated={r.data.truncated} len={len(r.data.html)}")

        r = await call(
            c,
            "run_js",
            {"script": "return document.title", "tab_id": target.tab_id},
            "执行只读 JS",
        )
        if r:
            print(f"    js 返回: {r.data}")

        r = await call(
            c,
            "cookies_get",
            {"tab_id": target.tab_id},
            "读取 cookies(只读)",
        )
        if r:
            names = [ck.get("name") for ck in r.data.cookies[:5]]
            print(f"    共 {r.data.count} 条, 前 5: {names}")

        # ---------- 4. 自建标签页中做交互类测试 ----------
        r = await call(
            c,
            "tab_new",
            {"browser_id": browser_id, "url": "https://example.com/"},
            "新建标签页打开 example.com",
        )
        if r is None:
            return summary()
        my_tab = r.data.tab_id
        created: list[str] = [my_tab]

        nav = await call(c, "navigate", {"url": "https://www.iana.org/", "tab_id": my_tab}, "导航到 iana.org")
        if nav:
            print(f"    status={nav.data.status} ok={nav.data.ok}")

        r = await call(c, "find_element", {"locator": "tag:h1", "tab_id": my_tab, "timeout": 10}, "定位 h1")
        h1_eid = r.data.element_id if r else None

        if h1_eid:
            r = await call(c, "element_info", {"element_id": h1_eid}, "读取 h1 详情")
            if r:
                print(f"    h1 text={r.data.text!r}")

            r = await call(
                c, "element_click", {"element_id": h1_eid}, "点击 h1(无害元素)"
            )

        r = await call(
            c,
            "find_element",
            {"locator": "text:Domains", "tab_id": my_tab, "timeout": 5},
            "文本方式定位(text: 前缀)",
        )

        r = await call(
            c,
            "run_js",
            {"script": "return {w: window.innerWidth, h: window.innerHeight}", "tab_id": my_tab},
            "执行 JS 返回对象",
        )
        if r:
            print(f"    js 返回: {r.data}")

        r = await call(
            c,
            "element_scroll",
            {"element_id": h1_eid or "", "action": "down", "pixel": 200},
            "元素滚动",
        ) if h1_eid else None

        r = await call(c, "tab_close", {"tab_id": my_tab}, "关闭自建标签页")
        if r:
            created.remove(my_tab)

        # ---------- 5. 多账号上下文 ----------
        r = await call(c, "context_new", {"browser_id": browser_id}, "新建多账号上下文")
        if r:
            ctx_id = r.data.context_id
            r2 = await call(
                c,
                "tab_new",
                {"browser_id": browser_id, "context_id": ctx_id, "url": "https://example.com/"},
                "在上下文中新建标签页",
            )
            if r2:
                created.append(r2.data.tab_id)
                r3 = await call(
                    c, "cookies_get", {"tab_id": r2.data.tab_id}, "读取上下文 cookies(应为空)"
                )
                if r3:
                    print(f"    上下文 cookies 数: {r3.data.count}")
            await call(c, "context_close", {"context_id": ctx_id}, "关闭上下文")

        # ---------- 6. 清理 ----------
        for tid in created:
            # 上下文关闭时其标签页会被一并销毁，视为清理成功
            try:
                await c.call_tool("tab_close", {"tab_id": tid})
                record(f"清理标签页 {tid}", True)
            except Exception as e:  # noqa: BLE001
                if "未找到标签页" in str(e):
                    record(f"清理标签页 {tid}", True, "已随上下文关闭")
                else:
                    record(f"清理标签页 {tid}", False, str(e)[:200])

    return summary()


def summary() -> int:
    failed = [r for r in results if not r[1]]
    print("\n========== 结果汇总 ==========")
    print(f"总计 {len(results)} 项, 通过 {len(results) - len(failed)}, 失败 {len(failed)}")
    for step, _, detail in failed:
        print(f"  ✗ {step}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
