"""VTable 工具族真机验证：绑定 → 元数据 → 列头 → 找值 → 坐标 → 点击 → 图标。"""

import asyncio

from fastmcp import Client

from drissionpage_mcp.server import mcp


def p(step: str, detail: str = "") -> None:
    print(f"[{step}] {detail}")


async def call(c: Client, name: str, args: dict | None = None, required: bool = True):
    try:
        return await c.call_tool(name, args or {})
    except Exception as e:  # noqa: BLE001
        p(f"✗ {name}", f"{type(e).__name__}: {str(e)[:180]}")
        if required:
            raise
        return None


async def main() -> None:
    async with Client(mcp) as c:
        await c.call_tool("browser_connect", {"address": "127.0.0.1:9222"})

        # 1. 绑定 + 元数据
        r = await call(c, "vtable_info", {})
        meta = r.data["meta"]
        p("1.绑定", f"type={meta.get('instanceType')} rows={meta.get('rowCount')} "
                    f"cols={meta.get('colCount')} headerRows={meta.get('headerRows')} "
                    f"frozen=({meta.get('frozenColCount')},{meta.get('frozenRowCount')})")
        print(f"    canvas@iframe={meta.get('canvasBox')}")

        # 2. 列头清单
        r = await call(c, "vtable_headers", {})
        cols = r.data["columns"]
        p("2.列头", f"共 {len(cols)} 列: " + ", ".join(
            f"{c['col']}:{c['title'] or c['field']}" for c in cols[:10]
        ))

        # 3. 读前 3 行 × 前 5 列
        r = await call(c, "vtable_read_cells", {"col0": 0, "row0": 0, "col1": 4, "row1": 3})
        p("3.读值", f"矩阵 {len(r.data['values'])}x{len(r.data['values'][0])}")
        for line in r.data["values"]:
            print(f"    {line}")

        # 4. 按文本找单元格（取第一列数据里的值）
        first_col_val = r.data["values"][1][0]
        target_text = str(first_col_val).strip() if first_col_val else None
        found = None
        if target_text:
            r = await call(c, "vtable_find_cell", {"text": target_text, "max_results": 3})
            found = r.data["matches"]
            p("4.找值", f"{target_text!r} -> {found}")

        # 5. 单元格详情与视口坐标
        col, row = (found[0]["col"], found[0]["row"]) if found else (1, 2)
        r = await call(c, "vtable_cell_info", {"col": col, "row": row})
        info = r.data
        p("5.坐标", f"({col},{row}) canvas中心={info['box_canvas']} "
                    f"视口中心={info['center_viewport']} inViewport={info['in_viewport']}")

        # 6. 真实鼠标点击该单元格（应触发行选中）
        r = await call(c, "vtable_click_cell", {"col": col, "row": row})
        p("6.点击", f"已点击 {r.data['clicked']}")

        # 7. 回读选区验证点击生效
        r = await call(c, "run_js", {
            "script": "var t = window.__vt; return t && t.getSelectedCellRanges ? JSON.stringify(t.getSelectedCellRanges()) : 'no-api'",
            "tab_id": None,
        })
        p("7.选区回读", str(r.data)[:120])

        # 8. 表头图标探测与点击（排序）
        r = await call(c, "vtable_click_icon", {"col": 1, "row": 0, "index": 1}, required=False)
        if r:
            p("8.图标点击", str(r.data["icon"]["name"]))

        # 9. 业务寻址
        r = await call(c, "vtable_resolve_cell", {"field": "", "record_index": 0}, required=False)
        if r is None:
            # 用真实字段名重试
            field = cols[1].get("field") if len(cols) > 1 else ""
            r = await call(c, "vtable_resolve_cell", {"field": field, "record_index": 0}, required=False)
        if r:
            p("9.寻址", str(dict(r.data))[:140])


if __name__ == "__main__":
    asyncio.run(main())
