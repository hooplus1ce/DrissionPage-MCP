"""JS 片段校验（node）：语法 + range 稀疏化 / find 截断语义。

pytest 里的 run_js 全是 fake 预置响应，JS 片段本身从不执行；本模块用 node
直接执行片段（stub 掉 scenegraph 与 DOM），把最容易静默坏掉的两处语义钉住。
node 不可用时整体跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from drissionpage_mcp.vtable_scripts import FIND_CELLS, VTABLE_INSPECT, VTABLE_SCRIPTS
from drissionpage_mcp.x6_scripts import X6_SCRIPTS

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="需要 node 才能校验 JS 片段")


def test_dom_helpers_parse(tmp_path):
    """非 VTable 的两段注入脚本（批量可见性判定、消息浮层采集）语法必须可解析。

    这两段只在真实浏览器执行，pytest 里的 fake run_js 不会碰到它们，
    故用 node --check 兜住最容易静默坏掉的语法问题。
    """
    from drissionpage_mcp.manager import _BATCH_VISIBLE_JS
    from drissionpage_mcp.tools.antd import _COLLECT_JS

    for name, snippet in (
        ("batch_visible", _BATCH_VISIBLE_JS),
        ("collect_messages", _COLLECT_JS),
    ):
        path = tmp_path / f"{name}.js"
        # 必须写成函数表达式（function 语句需要名字），片段内的 return 才合法
        path.write_text(f"(function () {{\n{snippet}\n}});", encoding="utf-8")
        proc = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{name} 语法错误: {proc.stderr[:400]}"


# 3 列 × 4 行 stub 表格：仅 (1,2) 为警示色，第 2 列全部可交互，
# 默认色故意用大写 #FFFFFF 以验证基线比较的大小写归一。
STUB_HARNESS = r"""
const fs = require('fs');
const inspectFn = new Function('window', 'arguments', fs.readFileSync(process.argv[2], 'utf8'));
const findFn = new Function('window', 'arguments', fs.readFileSync(process.argv[3], 'utf8'));

function makeCell(text, bg, fg, interactive) {
  return {
    type: 'cell',
    attribute: { fill: bg },
    children: [{ type: 'text', attribute: { text: text, fill: fg, cursor: interactive ? 'pointer' : 'default' } }],
  };
}
const COLS = 3, ROWS = 4;
const grid = {};
for (let r = 0; r < ROWS; r++) {
  for (let c = 0; c < COLS; c++) {
    const warn = (r === 2 && c === 1);
    grid[`${c},${r}`] = makeCell(`C${c}R${r}`, warn ? '#fff1f0' : '#FFFFFF', '#333333', c === 2);
  }
}
const vt = {
  scenegraph: { getCell: (c, r) => grid[`${c},${r}`] || null },
  colCount: COLS, rowCount: ROWS, columnHeaderLevelCount: 1,
  getCellRelativeRect: (c, r) => ({ left: 10 + c * 100, top: 5 + r * 30, right: 110 + c * 100, bottom: 35 + r * 30 }),
};
const vtBig = Object.assign({}, vt, { colCount: 600 });
const vtFind = {
  colCount: 3, rowCount: 6, columnHeaderLevelCount: 1,
  getCellValue: (c, r) => (r >= 1 ? 'hit' : 'miss'),
};
const out = {
  range: JSON.parse(inspectFn({ __vt: vt }, [null, null, [0, 2], [0, 3]])),
  big: JSON.parse(inspectFn({ __vt: vtBig }, [null, null, [0, 500], [0, 0]])),
  find_capped: JSON.parse(findFn({ __vt: vtFind }, ['hit', false, 1])),
  find_mid: JSON.parse(findFn({ __vt: vtFind }, ['hit', false, 10])),
  find_all: JSON.parse(findFn({ __vt: vtFind }, ['hit', false, 20])),
};
process.stdout.write(JSON.stringify(out));
"""


def test_all_snippets_parse(tmp_path):
    """所有 VTable/X6 片段必须能被 JS 引擎解析（包成函数以允许顶层 return）。"""
    for group in (VTABLE_SCRIPTS, X6_SCRIPTS):
        for name, snippet in group.items():
            f = tmp_path / f"{name}.js"
            f.write_text("(function () {\n" + snippet + "\n})", encoding="utf-8")
            proc = subprocess.run(
                [NODE, "--check", str(f)], capture_output=True, text=True, timeout=60
            )
            assert proc.returncode == 0, f"{name}: {proc.stderr[:800]}"


def test_range_and_find_semantics(tmp_path):
    """range 稀疏化（众数基线、大小写归一）与 find 截断标志的真实语义。"""
    inspect_file = tmp_path / "inspect.js"
    find_file = tmp_path / "find.js"
    inspect_file.write_text(VTABLE_INSPECT, encoding="utf-8")
    find_file.write_text(FIND_CELLS, encoding="utf-8")

    harness = tmp_path / "harness.js"
    harness.write_text(STUB_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(inspect_file), str(find_file)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[:2000]
    data = json.loads(proc.stdout)

    rng = data["range"]
    assert rng["scope"] == "range"
    assert rng["values"] == [
        ["C0R0", "C1R0", "C2R0"],
        ["C0R1", "C1R1", "C2R1"],
        ["C0R2", "C1R2", "C2R2"],
        ["C0R3", "C1R3", "C2R3"],
    ]
    assert rng["baseline_style"] == ["#FFFFFF", "#333333"]  # 众数基线，保留原始拼写
    assert rng["styles"] == [[1, 2, "#fff1f0", "#333333"]]  # 仅偏离基线的一格
    assert [e[0] for e in rng["interactive"]] == [2, 2, 2, 2]
    assert rng["drag_start"] and rng["drag_end"]

    assert data["big"]["error"] == "range-too-large"
    assert data["big"]["requestedCols"] == 501

    assert data["find_capped"]["truncated"] is True
    assert len(data["find_capped"]["matches"]) == 1
    assert data["find_mid"]["truncated"] is True  # 15 个命中、上限 10
    assert len(data["find_mid"]["matches"]) == 10
    assert data["find_all"]["truncated"] is False  # 上限足够时不谎报
    assert len(data["find_all"]["matches"]) == 15
