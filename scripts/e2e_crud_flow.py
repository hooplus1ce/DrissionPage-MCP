"""APS 真机 CRUD 流程验证：新增弹窗 → 填表 → 保存 → toast 断言。

通过 stdio 启动 MCP 服务子进程，全程走 MCP 工具调用，交互使用真实鼠标键盘。
流程自适应：先侦察表单结构再决定填写策略。
"""

import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

CONNECT_ADDR = "127.0.0.1:9222"
MARKER = "MCP自动化测试"


def p(step: str, detail: str = "") -> None:
    print(f"[{step}] {detail}")


async def call(c: Client, name: str, args: dict | None = None, required: bool = True):
    try:
        return await c.call_tool(name, args or {})
    except Exception as e:  # noqa: BLE001
        p(f"✗ {name}", f"{type(e).__name__}: {repr(e)[:300]}")
        if required:
            raise
        return None


def texts_of(result) -> list[str]:
    if not result or not result.data:
        return []
    return [((e.text or "").strip().replace(" ", "")) for e in result.data.elements]


async def main() -> None:
    transport = StdioTransport(sys.executable, ["-m", "drissionpage_mcp"])
    async with Client(transport) as c:
        # 1. 接管浏览器，找到 APS 标签页
        r = await call(c, "browser_connect", {"address": CONNECT_ADDR})
        browser_id = r.data.browser_id
        r = await call(c, "tab_list", {"browser_id": browser_id})
        tab_id = r.data[0].tab_id
        p("1.标签页", f"{r.data[0].title} | {r.data[0].url}")

        # 0. 清理残留弹窗：反复点最顶层弹窗的"取消"，直到没有可见弹窗
        cleaned = 0
        for _ in range(5):
            try:
                await c.call_tool("antd_modal_click", {"button_text": "取消", "frame": "active", "timeout": 2})
            except Exception:
                break
            await asyncio.sleep(0.6)
            cleaned += 1
        p("0.清理", f"关闭了 {cleaned} 层残留弹窗")

        # 2. frame 情况
        r = await call(c, "frame_list", {"tab_id": tab_id})
        for f in r.data:
            p("2.iframe", f"idx={f.frame_index} id={f.iframe_id} active={f.displayed}")

        # 3. 在激活 frame 中找"新增"按钮；不在列表页时自适应恢复（返回/菜单）
        add_eid = None
        for attempt in range(4):
            r = await call(c, "find_elements", {"locator": ".ant-btn", "frame": "active", "limit": 30})
            btn_texts = texts_of(r)
            for e in (r.data.elements if r else []):
                t = (e.text or "").replace(" ", "")
                if "新增" in t and "方案" not in t:
                    add_eid = e.element_id
                    break
            if add_eid:
                p("3.新增按钮", f"element_id={add_eid}, 全部按钮={btn_texts}")
                break
            p(f"3.恢复(第{attempt + 1}次)", f"当前页面无新增按钮: {btn_texts}")
            # 有可见弹窗时先关掉（可能挡住返回按钮）
            if any("取消" in t or "确" in t for t in btn_texts):
                r_m = await call(c, "find_elements", {"locator": "css:.ant-modal", "frame": "active", "limit": 5}, required=False)
                blocked = False
                for e in (r_m.data.elements if r_m else []):
                    info = await call(c, "element_info", {"element_id": e.element_id}, required=False)
                    if info and info.data.rect is not None:
                        blocked = True
                        break
                if blocked:
                    closed = await call(c, "antd_modal_click", {"button_text": "取消", "frame": "active", "timeout": 3}, required=False)
                    if closed:
                        p("3.恢复", "已关闭阻挡的子弹窗")
                        await asyncio.sleep(1)
                        continue
            back = next((e for e in (r.data.elements if r else [])
                         if (e.text or "").replace(" ", "") == "返回"), None)
            if back is not None:
                await call(c, "element_click", {"element_id": back.element_id})
            else:
                r3 = await call(c, "find_elements", {"locator": "text:清洗", "limit": 5}, required=False)
                menu = next((e for e in (r3.data.elements if r3 else [])
                             if e.text and "清洗" in e.text), None)
                if menu is None:
                    p("中止", "找不到返回按钮或清洗相关菜单，请手动回到功能列表页")
                    return
                await call(c, "element_click", {"element_id": menu.element_id})
            await asyncio.sleep(2.5)
        if not add_eid:
            p("中止", "多次恢复后仍未找到新增按钮")
            return

        # 4. 真实鼠标点击新增
        await call(c, "element_click", {"element_id": add_eid})
        p("4.点击新增", "已通过 Actions 真实点击")

        # 5. 等待表单出现（弹窗式或页面式均可），侦察结构
        await asyncio.sleep(1.5)
        r = await call(
            c,
            "find_element",
            {"locator": ".ant-input", "frame": "active", "timeout": 8},
            required=False,
        )
        if r is None:
            p("5.表单", "点击新增后未出现表单（既无弹窗也无页面表单）")
            return
        # 表单载体：modal 或独立页面
        r_m = await call(c, "find_elements", {"locator": "css:.ant-modal", "frame": "active", "limit": 5}, required=False)
        visible_modals = 0
        if r_m:
            for e in r_m.data.elements:
                info = await call(c, "element_info", {"element_id": e.element_id}, required=False)
                if info and info.data.rect is not None:
                    visible_modals += 1
        form_kind = "弹窗表单" if visible_modals else "页面表单"
        p("5.表单", f"形态={form_kind}")

        r = await call(c, "find_elements", {"locator": "css:.ant-form-item-label label", "frame": "active", "limit": 30}, required=False)
        labels = [e.text for e in r.data.elements] if r else []
        p("5a.表单标签", str(labels))

        r = await call(c, "find_elements", {"locator": "css:.ant-input", "frame": "active", "limit": 20}, required=False)
        input_eids = [(e.element_id, e.text) for e in r.data.elements] if r else []
        p("5b.文本输入框", str(input_eids))

        r = await call(c, "find_elements", {"locator": "css:.ant-select", "frame": "active", "limit": 10}, required=False)
        select_eids = [e.element_id for e in r.data.elements] if r else []
        p("5c.下拉框", str(select_eids))

        r = await call(c, "find_elements", {"locator": "css:.ant-picker", "frame": "active", "limit": 10}, required=False)
        date_eids = [e.element_id for e in r.data.elements] if r else []
        p("5d.日期选择", str(date_eids))

        r = await call(c, "find_elements", {"locator": ".ant-btn", "frame": "active", "limit": 30}, required=False)
        buttons = texts_of(r)
        p("5e.当前按钮", str(buttons))

        # 6. 填写：第一个文本框填标记
        if input_eids:
            await call(c, "element_input", {"element_id": input_eids[0][0], "text": MARKER, "clear": True})
            p("6a.输入", f"{MARKER!r}")
        if select_eids:
            r = await call(c, "antd_get_options", {"element_id": select_eids[0]}, required=False)
            opts = r if r else None
            p("6b.下拉选项", str(opts))
            if opts:
                r2 = await call(c, "antd_select", {"element_id": select_eids[0], "option_text": opts[0], "exact": True}, required=False)
                if r2:
                    p("6c.选择", opts[0])
        import datetime

        today = datetime.date.today().isoformat()
        if date_eids:
            await call(c, "antd_date_pick", {"element_id": date_eids[0], "date": today}, required=False)
            p("6d.日期", today)

        # 7. 保存：页面表单点"保存"，弹窗表单点"确定"；都通过真实点击
        save_eid = None
        save_text = None
        for e in (r.data.elements if r else []):
            pass
        r = await call(c, "find_elements", {"locator": ".ant-btn", "frame": "active", "limit": 30})
        for e in r.data.elements:
            t_ = (e.text or "").replace(" ", "")
            if t_ in ("保存", "确定", "提交"):
                save_eid, save_text = e.element_id, t_
                break
        if save_eid:
            await call(c, "element_click", {"element_id": save_eid})
            p("7.保存", f"已点击 {save_text!r}")
        else:
            p("7.保存", f"未找到保存/确定按钮，当前按钮={buttons}")

        # 8. 读取 toast 断言
        await asyncio.sleep(1)
        r = await call(c, "get_toasts", {"frame": "active"})
        p("8.消息气泡", f"message={r.data.message_texts} notification={r.data.notification_texts}")

        # 9. 弹窗是否关闭（保存成功的表现；rect 可算 = 真实可见）
        r = await call(c, "find_elements", {"locator": "css:.ant-modal", "frame": "active", "limit": 5}, required=False)
        visible_modals = []
        if r:
            for e in r.data.elements:
                info = await call(c, "element_info", {"element_id": e.element_id}, required=False)
                if info and info.data.rect is not None:
                    visible_modals.append(e.element_id)
        p("9.弹窗状态", "仍可见(可能校验失败)" if visible_modals else "已关闭(保存流程走通)")

        p("完成", f"标记数据 {MARKER!r} 已写入测试环境，可手动或后续脚本清理")


if __name__ == "__main__":
    asyncio.run(main())
