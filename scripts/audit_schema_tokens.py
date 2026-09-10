"""量化 MCP 工具 Schema 的 Token 成本（省 token 设计的守门脚本）。

用法：
    uv run python scripts/audit_schema_tokens.py
    ENABLE_TOOL_SEARCH=true uv run python scripts/audit_schema_tokens.py

估算口径：CJK 1 token/字 + ASCII 4 字符/token（±20%），用于横向比较而非精确计费。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from fastmcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from drissionpage_mcp.server import INSTRUCTIONS, mcp  # noqa: E402


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3000 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF


def est_tokens(text: str) -> float:
    cjk = sum(1 for c in text if _is_cjk(c))
    return cjk + (len(text) - cjk) / 4


async def main() -> None:
    search = os.getenv("ENABLE_TOOL_SEARCH", "").lower() in ("1", "true", "yes")
    mode = "BM25-search" if search else "default"
    async with Client(mcp) as c:
        tools = await c.list_tools()
        rows = []
        for t in tools:
            desc = t.description or ""
            inp = json.dumps(getattr(t, "inputSchema", None) or {}, ensure_ascii=False)
            out = json.dumps(getattr(t, "outputSchema", None) or {}, ensure_ascii=False)
            rows.append((t.name, est_tokens(desc + inp + out), len(desc), len(inp), len(out)))
        rows.sort(key=lambda r: -r[1])
        schema_tokens = sum(r[1] for r in rows)
        inst_tokens = est_tokens(INSTRUCTIONS)
        print(f"[模式 {mode}] 工具数={len(rows)}")
        print(f"  Schema   ≈ {schema_tokens:,.0f} tokens")
        print(f"  INSTRUCTIONS ≈ {inst_tokens:,.0f} tokens（{len(INSTRUCTIONS)} 字符）")
        print(f"  合计     ≈ {schema_tokens + inst_tokens:,.0f} tokens")
        print(f"  字段小计 desc={sum(r[2] for r in rows):,} in={sum(r[3] for r in rows):,} "
              f"out={sum(r[4] for r in rows):,} 字符")
        print("\n  最贵 10 个工具:")
        for name, tk, d, i, o in rows[:10]:
            print(f"    {name:<22}≈{tk:>6.0f} tok (desc {d:>4} / in {i:>5} / out {o:>5})")


asyncio.run(main())
