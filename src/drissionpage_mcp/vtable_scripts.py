"""VTable JS 片段库。

设计哲学（借鉴 Qa-Automation-MCP 参考实现并适配 DrissionPage）：
- 实例绑定到 window.__vt，三级降级：容器/canvas __vtable__ 直连 → React Fiber
  绝对路径 → BFS 全树扫描；绑定幂等，每次工具调用前重跑（廉价且抗路由变化）。
- 几何一律通过 VTable 实例 API 读取 canvas 局部坐标（getCellRelativeRect 的
  bounds 字段、scenegraph 节点 globalAABBBounds），不猜测、不读 DOM 布局。
- frame→视口的偏移换算统一在 Python 层（vtable.py）完成。
- 所有片段返回 JSON 字符串，由 Python 侧 json.loads 解析。
- 适配实测指纹：实例无 headerRowCount（用 columnHeaderLevelCount 兜底）、
  getCellRelativeRect 返回 {bounds: {x1,y1,x2,y2}}。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 实例绑定：选表（可见弹窗优先）→ 三级绑定探测 → window.__vt
# ---------------------------------------------------------------------------
BIND = r"""
var __vt_visible = function (node) {
  if (!node) return false;
  var style = getComputedStyle(node);
  var rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' &&
    rect.width > 0 && rect.height > 0;
};
var roots = Array.from(document.querySelectorAll('.vtable'));
var requestedIndex = Number(window.__vtable_target_index);
var el = Number.isInteger(requestedIndex) ? roots[requestedIndex] : null;
if (!el) {
  var inModal = roots.filter(function (n) {
    return n.closest('.ant-modal') && __vt_visible(n.closest('.ant-modal')) && __vt_visible(n);
  });
  el = (inModal.length ? inModal[inModal.length - 1] : roots.filter(__vt_visible)[0]) || null;
}
var out = { bound: false, containers: roots.length, source: null, type: null };
if (el && __vt_visible(el)) {
  var canvas = el.querySelector('canvas');
  var native = el.__vtable__ || (canvas && canvas.__vtable__);
  if (native && typeof native.getCellRelativeRect === 'function') {
    window.__vt = native;
    out.bound = true; out.source = '__vtable__';
    out.type = native.constructor ? native.constructor.name : 'unknown';
    return JSON.stringify(out);
  }
  var probe = el.parentElement || el;
  var fk = null;
  var keys = Object.keys(probe);
  for (var i = 0; i < keys.length; i++) {
    if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternalInstance') === 0) { fk = keys[i]; break; }
  }
  if (fk) {
    var queue = [{ node: probe[fk], depth: 0 }];
    var seen = new Set();
    var visited = 0;
    while (queue.length && visited < 4000) {
      var cur = queue.shift();
      var node = cur.node;
      if (!node || seen.has(node)) continue;
      seen.add(node); visited++;
      var st = node.stateNode;
      var inst = st ? (st.vtableInstance || st.tableInstance) : null;
      if (inst && typeof inst.getCellRelativeRect === 'function') {
        window.__vt = inst;
        out.bound = true; out.source = 'fiber:depth' + cur.depth;
        out.type = inst.constructor ? inst.constructor.name : 'unknown';
        return JSON.stringify(out);
      }
      if (cur.depth < 30) {
        if (node.child) queue.push({ node: node.child, depth: cur.depth + 1 });
        if (node.sibling) queue.push({ node: node.sibling, depth: cur.depth + 1 });
        if (node.return) queue.push({ node: node.return, depth: cur.depth + 1 });
      }
    }
    out.visited = visited;
  }
}
return JSON.stringify(out);
"""

# ---------------------------------------------------------------------------
# 表格元数据 + canvas 盒（iframe 内坐标）
# ---------------------------------------------------------------------------
TABLE_META = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var num = function (v) { var n = Number(v); return Number.isFinite(n) ? n : null; };
var headerRows = num(t.columnHeaderLevelCount);
if (headerRows === null) headerRows = num(t.headerRowCount);
if (headerRows === null) headerRows = 1;
var canvas = t.canvas || document.querySelector('.vtable canvas');
var cr = canvas ? canvas.getBoundingClientRect() : null;
return JSON.stringify({
  bound: true,
  type: t.constructor ? t.constructor.name : 'unknown',
  rowCount: num(t.rowCount), colCount: num(t.colCount),
  headerRows: headerRows,
  frozenColCount: num(t.frozenColCount) || 0,
  frozenRowCount: num(t.frozenRowCount) || 0,
  rightFrozenColCount: num(t.rightFrozenColCount) || 0,
  canvasBox: cr ? { x: cr.x, y: cr.y, width: cr.width, height: cr.height } : null,
  scrollTop: num(t.scrollTop) || 0,
  scrollLeft: num(t.scrollLeft) || 0,
});
"""

# ---------------------------------------------------------------------------
# 列头清单：field/title/能力（无值采样，供 AI 选列）
# ---------------------------------------------------------------------------
HEADERS = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var num = function (v) { var n = Number(v); return Number.isFinite(n) ? n : null; };
var text = function (v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v).trim().slice(0, 120);
  if (typeof v === 'object') {
    var keys = ['title', 'text', 'label', 'name', 'field', 'key'];
    for (var i = 0; i < keys.length; i++) {
      if (v[keys[i]] !== undefined && String(v[keys[i]]).trim()) return String(v[keys[i]]).trim().slice(0, 120);
    }
  }
  return '';
};
var colCount = num(t.colCount) || 0;
var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
var headerRow = Math.max(0, headerRows - 1);
var columns = [];
for (var col = 0; col < colCount; col++) {
  var field = '', title = '', type = '';
  try { field = text(t.getBodyField && t.getBodyField(col, headerRows)); } catch (e) {}
  try {
    var def = t.getBodyColumnDefine && t.getBodyColumnDefine(col, headerRows);
    if (!field && def) field = text(def.field || def.key);
    if (def && (def.title || def.header)) title = text(def.title || def.header);
  } catch (e) {}
  try { if (!title) title = text(t.getCellValue && t.getCellValue(col, headerRow)); } catch (e) {}
  try { type = text(t.getCellType && t.getCellType(col, headerRows)); } catch (e) {}
  columns.push({ col: col, field: field, title: title, type: type });
}
return JSON.stringify({ columns: columns, colCount: colCount, headerRows: headerRows });
"""

# ---------------------------------------------------------------------------
# 批量读值：矩形区域，行优先矩阵
# ---------------------------------------------------------------------------
READ_CELLS = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var col0 = arguments[0], row0 = arguments[1], col1 = arguments[2], row1 = arguments[3];
var minC = Math.min(col0, col1), maxC = Math.max(col0, col1);
var minR = Math.min(row0, row1), maxR = Math.max(row0, row1);
if ((maxC - minC + 1) * (maxR - minR + 1) > 2000) return JSON.stringify({ error: 'too-many-cells', max: 2000 });
var values = [];
for (var r = minR; r <= maxR; r++) {
  var line = [];
  for (var c = minC; c <= maxC; c++) {
    var v = null;
    try { v = t.getCellValue(c, r); } catch (e) { v = null; }
    if (v === null || v === undefined) line.push(null);
    else if (typeof v === 'object') { try { line.push(JSON.stringify(v).slice(0, 200)); } catch (e2) { line.push(String(v).slice(0, 200)); } }
    else line.push(String(v).slice(0, 200));
  }
  values.push(line);
}
return JSON.stringify({ minCol: minC, minRow: minR, maxCol: maxC, maxRow: maxR, values: values });
"""

# ---------------------------------------------------------------------------
# 按文本找单元格：全表扫描（有界），返回地址+值
# ---------------------------------------------------------------------------
FIND_CELLS = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var want = String(arguments[0]);
var exact = !!arguments[1];
var maxResults = arguments[2] || 20;
var num = function (v) { var n = Number(v); return Number.isFinite(n) ? n : null; };
var colCount = num(t.colCount) || 0;
var rowCount = num(t.rowCount) || 0;
var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
var matches = [];
for (var row = headerRows; row < rowCount && matches.length < maxResults; row++) {
  for (var col = 0; col < colCount && matches.length < maxResults; col++) {
    var v = null;
    try { v = t.getCellValue(col, row); } catch (e) { continue; }
    if (v === null || v === undefined) continue;
    var s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    var hit = exact ? s.trim() === want : s.indexOf(want) >= 0;
    if (hit) matches.push({ col: col, row: row, value: s.slice(0, 120) });
  }
}
return JSON.stringify({ matches: matches, scanned: { colCount: colCount, rowCount: rowCount - headerRows }, truncated: false });
"""

# ---------------------------------------------------------------------------
# 单元格几何：canvas 局部中心/盒 + 视口可见性（冻结行列补偿）
# ---------------------------------------------------------------------------
CELL_GEOMETRY = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1];
var pick = function () {
  for (var i = 0; i < arguments.length; i++) {
    if (arguments[i] !== undefined && arguments[i] !== null) return arguments[i];
  }
  return undefined;
};
var rect = null;
try { rect = t.getCellRelativeRect(col, row); } catch (e) {}
if (!rect) return JSON.stringify({ found: false });
var left = num2(pick(rect.left, pick(rect.x1, rect.bounds && rect.bounds.x1)));
var top = num2(pick(rect.top, pick(rect.y1, rect.bounds && rect.bounds.y1)));
var right = num2(pick(rect.right, pick(rect.x2, rect.bounds && rect.bounds.x2)));
var bottom = num2(pick(rect.bottom, pick(rect.y2, rect.bounds && rect.bounds.y2)));
function num2(v) { var n = Number(v); return Number.isFinite(n) ? n : undefined; }
if (left === undefined || top === undefined || right === undefined || bottom === undefined) {
  return JSON.stringify({ found: false });
}
var box = { x: left, y: top, width: right - left, height: bottom - top };
var center = { x: (left + right) / 2, y: (top + bottom) / 2 };
var canvas = t.canvas || document.querySelector('.vtable canvas');
var cr = canvas ? canvas.getBoundingClientRect() : { width: 0, height: 0 };
var fc = t.frozenColCount || 0, fr = t.frozenRowCount || 0;
var rfc = t.rightFrozenColCount || 0, bfr = t.bottomFrozenRowCount || 0;
var fw = (fc && t.getFrozenColsWidth) ? t.getFrozenColsWidth() : 0;
var fh = (fr && t.getFrozenRowsHeight) ? t.getFrozenRowsHeight() : 0;
var rfw = (rfc && t.getRightFrozenColsWidth) ? t.getRightFrozenColsWidth() : 0;
var bfh = (bfr && t.getBottomFrozenRowsHeight) ? t.getBottomFrozenRowsHeight() : 0;
var tol = 1;
var cx = center.x, cy = center.y;
var isLF = col < fc, isTF = row < fr;
var isRF = col >= (t.colCount - rfc), isBF = row >= (t.rowCount - bfr);
var hOk = isLF ? cx <= fw + tol : (isRF ? (cx >= cr.width - rfw - tol) : (cx >= fw - tol && cx <= cr.width - rfw + tol));
var vOk = isTF ? cy <= fh + tol : (isBF ? (cy >= cr.height - bfh - tol) : (cy >= fh - tol && cy <= cr.height - bfr + tol));
return JSON.stringify({
  found: true, box: box, center: center,
  canvasBox: { x: cr.x, y: cr.y, width: cr.width, height: cr.height },
  inViewport: hOk && vOk,
  value: (function () { try { var v = t.getCellValue(col, row); return v === null || v === undefined ? null : String(v).slice(0, 120); } catch (e) { return null; } })(),
  type: (function () { try { return t.getCellType ? t.getCellType(col, row) : null; } catch (e) { return null; } })(),
});
"""

# ---------------------------------------------------------------------------
# 滚动到单元格：scrollToCell API + 手动补偿，滚动后返回新几何
# ---------------------------------------------------------------------------
SCROLL_TO_CELL = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1];
try { if (typeof t.scrollToCell === 'function') t.scrollToCell({ col: col, row: row }); } catch (e) {}
var rect = null;
try { rect = t.getCellRelativeRect(col, row); } catch (e) {}
if (rect && rect.bounds) {
  var cx = (rect.bounds.x1 + rect.bounds.x2) / 2;
  var cy = (rect.bounds.y1 + rect.bounds.y2) / 2;
  var canvas = t.canvas || document.querySelector('.vtable canvas');
  var cr = canvas ? canvas.getBoundingClientRect() : { width: 1000, height: 500 };
  var rfw = (t.rightFrozenColCount && t.getRightFrozenColsWidth) ? t.getRightFrozenColsWidth() : 0;
  var fw = (t.frozenColCount && t.getFrozenColsWidth) ? t.getFrozenColsWidth() : 0;
  var fh = (t.frozenRowCount && t.getFrozenRowsHeight) ? t.getFrozenRowsHeight() : 0;
  var isLF = col < (t.frozenColCount || 0);
  var isRF = col >= (t.colCount - (t.rightFrozenColCount || 0));
  if (!isLF && !isRF) {
    if (cx > cr.width - rfw) {
      var dx = cx - (cr.width - rfw) + 60;
      if (t.setScrollLeft) t.setScrollLeft((t.scrollLeft || 0) + dx); else t.scrollLeft = (t.scrollLeft || 0) + dx;
    } else if (cx < fw) {
      var dx2 = fw - cx + 60;
      if (t.setScrollLeft) t.setScrollLeft(Math.max(0, (t.scrollLeft || 0) - dx2)); else t.scrollLeft = Math.max(0, (t.scrollLeft || 0) - dx2);
    }
  }
  if (row >= (t.frozenRowCount || 0) && row < (t.rowCount - (t.bottomFrozenRowCount || 0))) {
    if (cy > cr.height) {
      if (t.setScrollTop) t.setScrollTop((t.scrollTop || 0) + (cy - cr.height) + 40); else t.scrollTop = (t.scrollTop || 0) + (cy - cr.height) + 40;
    } else if (cy < fh) {
      if (t.setScrollTop) t.setScrollTop(Math.max(0, (t.scrollTop || 0) - (fh - cy) - 40)); else t.scrollTop = Math.max(0, (t.scrollTop || 0) - (fh - cy) - 40);
    }
  }
  try { if (typeof t.render === 'function') t.render(); } catch (e) {}
}
return JSON.stringify({ scrolled: true });
"""

# ---------------------------------------------------------------------------
# 单元格内交互图标：scenegraph 遍历（排除结构节点），返回图标盒/中心（canvas 局部）
# ---------------------------------------------------------------------------
CELL_ICONS = r"""
var t = window.__vt;
if (!t || !t.scenegraph) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1];
var structural = { '': 1, 'group': 1, 'cell': 1, 'cell-group': 1, 'content': 1, 'text': 1, 'background': 1, 'border': 1, 'line': 1, 'rect': 1, 'shadow': 1, 'stroke': 1 };
var childrenOf = function (node) {
  if (!node) return [];
  if (Array.isArray(node.children)) return node.children;
  try { var c = node.getChildren && node.getChildren(); if (Array.isArray(c)) return c; } catch (e) {}
  var out = [];
  try { if (typeof node.forEachChildren === 'function') node.forEachChildren(function (ch) { out.push(ch); }); } catch (e2) {}
  return out;
};
var iconName = function (node) {
  var a = node.attribute || {};
  var v = node.name || a.name || a.iconName || a.funcType || '';
  return String(v || '');
};
var icons = [];
var cell = null;
try { cell = t.scenegraph.getCell(col, row); } catch (e) {}
if (!cell) return JSON.stringify({ found: false, icons: [] });
var queue = [{ node: cell, depth: 0 }];
var seen = new Set();
var visited = 0;
while (queue.length && visited < 400) {
  var cur = queue.shift();
  var node = cur.node;
  if (!node || seen.has(node) || cur.depth > 8) continue;
  seen.add(node); visited++;
  if (cur.depth > 0) {
    var a = node.attribute || {};
    var name = iconName(node);
    var isText = String(node.type || '').toLowerCase() === 'text' || a.text !== undefined;
    var b = node.globalAABBBounds;
    if (name && !isText && !structural[name.toLowerCase()] && b) {
      var x1 = Number(b.x1), y1 = Number(b.y1), x2 = Number(b.x2), y2 = Number(b.y2);
      var w = x2 - x1, h = y2 - y1;
      if ([x1, y1, x2, y2, w, h].every(Number.isFinite) && w > 0 && w < 300 && h > 0 && h < 300) {
        var fname = name.toLowerCase();
        var fn = 'custom';
        if (fname.indexOf('sort') >= 0) fn = 'sort';
        else if (fname.indexOf('filter') >= 0) fn = 'filter';
        else if (fname.indexOf('dropdown') >= 0 || fname.indexOf('downward') >= 0) fn = 'dropdown';
        else if (fname.indexOf('freeze') >= 0) fn = 'freeze';
        else if (fname.indexOf('checkbox') >= 0) fn = 'checkbox';
        else if (fname.indexOf('expand') >= 0) fn = 'expand';
        else if (fname.indexOf('collapse') >= 0) fn = 'collapse';
        icons.push({ name: name, function: fn, box: { x: x1, y: y1, width: w, height: h }, center: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 } });
      }
    }
  }
  var kids = childrenOf(node);
  for (var i = 0; i < kids.length; i++) queue.push({ node: kids[i], depth: cur.depth + 1 });
}
return JSON.stringify({ found: true, icons: icons.slice(0, 12) });
"""

# ---------------------------------------------------------------------------
# 业务寻址：字段 + 记录索引 → 单元格地址
# ---------------------------------------------------------------------------
RESOLVE_CELL = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var field = String(arguments[0]), recordIndex = arguments[1];
var address = null, method = '';
try {
  if (typeof t.getCellAddrByFieldRecord === 'function') {
    address = t.getCellAddrByFieldRecord(field, recordIndex);
    method = 'getCellAddrByFieldRecord';
  }
} catch (e) { address = null; }
if ((!address || !Number.isFinite(Number(address.col))) &&
    typeof t.getTableIndexByField === 'function' && typeof t.getTableIndexByRecordIndex === 'function') {
  try {
    address = { col: t.getTableIndexByField(field), row: t.getTableIndexByRecordIndex(recordIndex) };
    method = 'getTableIndexByField+getTableIndexByRecordIndex';
  } catch (e2) { address = null; }
}
if (!address) return JSON.stringify({ ok: false, reason: 'address-unavailable', field: field, recordIndex: recordIndex });
var col = Number(address.col), row = Number(address.row);
if (!Number.isInteger(col) || !Number.isInteger(row) || col < 0 || row < 0 ||
    col >= Number(t.colCount || 0) || row >= Number(t.rowCount || 0)) {
  return JSON.stringify({ ok: false, reason: 'address-out-of-range', field: field, recordIndex: recordIndex, address: { col: col, row: row }, method: method });
}
var value = null;
try { value = t.getCellValue(col, row); } catch (e3) {}
return JSON.stringify({ ok: true, col: col, row: row, field: field, recordIndex: recordIndex, value: value === null || value === undefined ? null : String(value).slice(0, 200), method: method });
"""

# ---------------------------------------------------------------------------
# 编辑单元格：editorManager 写入并落值（API 级编辑，绕过双击）
# ---------------------------------------------------------------------------
EDIT_CELL = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1], value = arguments[2], commit = arguments[3];
var editor = (t.getEditor && t.getEditor(col, row)) || null;
if (!editor) return JSON.stringify({ ok: false, reason: 'no-editor' });
try { t.editorManager.startEditCell(col, row); } catch (e) { return JSON.stringify({ ok: false, reason: 'start-failed: ' + e }); }
var editing = t.editorManager.editingEditor;
if (!editing) return JSON.stringify({ ok: false, reason: 'start-failed' });
try { if (editing.setValue) editing.setValue(value); } catch (e2) { return JSON.stringify({ ok: false, reason: 'set-value-failed: ' + e2 }); }
if (commit) { try { t.editorManager.completeEdit(); } catch (e3) { return JSON.stringify({ ok: false, reason: 'commit-failed: ' + e3 }); } }
return JSON.stringify({ ok: true });
"""

VTABLE_SCRIPTS = {
    "bind": BIND,
    "table_meta": TABLE_META,
    "headers": HEADERS,
    "read_cells": READ_CELLS,
    "find_cells": FIND_CELLS,
    "cell_geometry": CELL_GEOMETRY,
    "scroll_to_cell": SCROLL_TO_CELL,
    "cell_icons": CELL_ICONS,
    "resolve_cell": RESOLVE_CELL,
    "edit_cell": EDIT_CELL,
}

# ---------------------------------------------------------------------------
# 选区信息：getSelectedCellInfos 紧凑化（含业务记录 originData）
# ---------------------------------------------------------------------------
SELECTION_INFO = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var ranges = null;
try { ranges = t.getSelectedCellRanges ? t.getSelectedCellRanges() : null; } catch (e) {}
var cells = [];
try {
  var infos = t.getSelectedCellInfos ? t.getSelectedCellInfos() : null;
  if (Array.isArray(infos)) {
    for (var i = 0; i < infos.length && cells.length < 30; i++) {
      var rowCells = infos[i];
      if (!Array.isArray(rowCells)) continue;
      for (var j = 0; j < rowCells.length && cells.length < 30; j++) {
        var c = rowCells[j];
        if (!c) continue;
        var origin = null;
        try {
          if (c.originData && typeof c.originData === 'object') {
            var keys = ['id', 'lineCode', 'lineName', 'dataStatus', 'dataStatusName'];
            origin = {};
            for (var k in c.originData) {
              if (origin && Object.keys(origin).length >= 12) break;
              var v = c.originData[k];
              if (v === null || v === undefined || typeof v !== 'object') origin[k] = v;
            }
          }
        } catch (e2) {}
        cells.push({
          col: c.col, row: c.row, field: c.field, title: c.title,
          cellType: c.cellType, value: c.value === undefined ? null : String(c.value).slice(0, 160),
          origin: origin
        });
      }
    }
  }
} catch (e3) {}
return JSON.stringify({ ranges: ranges, cells: cells });
"""

# ---------------------------------------------------------------------------
# 单元格状态：选中 / 复选 / 单选 / 开关 / scenegraph 视觉签名
# ---------------------------------------------------------------------------
CELL_STATE_EXT = r"""
var t = window.__vt;
if (!t || !t.scenegraph) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1];
var selected = false;
try {
  var ranges = t.getSelectedCellRanges ? t.getSelectedCellRanges() : [];
  for (var i = 0; i < ranges.length; i++) {
    var r = ranges[i];
    var rs = r.start || r, re = r.end || r;
    if (col >= Math.min(rs.col, re.col) && col <= Math.max(rs.col, re.col) &&
        row >= Math.min(rs.row, re.row) && row <= Math.max(rs.row, re.row)) { selected = true; break; }
  }
} catch (e) {}
var checkbox = null, radio = null, switchState = null;
try { if (t.getCellCheckboxState) checkbox = t.getCellCheckboxState(col, row); } catch (e2) {}
try { if (t.getCellRadioState) radio = t.getCellRadioState(col, row); } catch (e3) {}
try { if (t.getCellSwitchState) switchState = t.getCellSwitchState(col, row); } catch (e4) {}
if (checkbox === undefined) checkbox = null;
if (radio === undefined) radio = null;
if (switchState === undefined) switchState = null;
var nodes = [], paints = [];
var cell = null;
try { cell = t.scenegraph.getCell(col, row); } catch (e5) {}
if (cell) {
  var walk = function (node, depth) {
    if (!node || depth > 8 || nodes.length >= 60) return;
    var a = node.attribute || {};
    var visual = {};
    ['fill', 'background', 'backgroundColor', 'stroke', 'opacity', 'cornerRadius'].forEach(function (key) {
      if (a[key] !== undefined && a[key] !== null) visual[key] = a[key];
    });
    if (Object.keys(visual).length) {
      nodes.push({ type: String(node.type || node.name || 'node'), visual: visual });
      ['fill', 'background', 'backgroundColor', 'stroke'].forEach(function (key) {
        if (visual[key] !== undefined) paints.push(String(visual[key]));
      });
    }
    var children = node.children || (node.getChildren && node.getChildren());
    if (Array.isArray(children)) children.forEach(function (ch) { walk(ch, depth + 1); });
    else if (node.forEachChildren) node.forEachChildren(function (ch) { walk(ch, depth + 1); });
  };
  walk(cell, 0);
}
return JSON.stringify({
  col: col, row: row, selected: selected,
  checkbox: checkbox, radio: radio, switch: switchState,
  visual: { signature: JSON.stringify(nodes).slice(0, 600), paints: paints.slice(0, 10) }
});
"""

# ---------------------------------------------------------------------------
# 可视窗口范围 + 官方视口判定
# ---------------------------------------------------------------------------
VISIBLE_RANGE = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var out = { inVisualView: null, visible: null };
try { if (t.cellIsInVisualView) out.inVisualView = !!t.cellIsInVisualView(arguments[0], arguments[1]); } catch (e) {}
try { if (t.getBodyVisibleCellRange) out.visible = t.getBodyVisibleCellRange(); } catch (e2) {}
return JSON.stringify(out);
"""

VTABLE_SCRIPTS["selection_info"] = SELECTION_INFO
VTABLE_SCRIPTS["cell_state"] = CELL_STATE_EXT
VTABLE_SCRIPTS["visible_range"] = VISIBLE_RANGE

# ---------------------------------------------------------------------------
# 滚动条几何：由已知量确定性计算滑块位置（滚动条是 canvas 绘制，不在 DOM/scenegraph）
# ---------------------------------------------------------------------------
SCROLLBAR_GEOMETRY = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var cv = (t.canvas || document.querySelector('.vtable canvas')).getBoundingClientRect();
var BAR = 14;
var totalH = 0, totalW = 0;
try { totalH = t.getAllRowsHeight ? t.getAllRowsHeight() : 0; } catch (e) {}
try { totalW = t.getAllColsWidth ? t.getAllColsWidth() : 0; } catch (e2) {}
if (!totalW) { try { totalW = t.getColsWidth ? t.getColsWidth() : 0; } catch (e3) {} }
var top = Number(t.scrollTop) || 0;
var left = Number(t.scrollLeft) || 0;
var out = {
  canvas: { x: cv.x, y: cv.y, width: cv.width, height: cv.height },
  scroll: { top: top, left: left },
  total: { height: totalH, width: totalW },
};
var maxTop = Math.max(0, totalH - cv.height);
var maxLeft = Math.max(0, totalW - cv.width);
if (totalH > cv.height) {
  var thumbH = Math.max(cv.height * cv.height / totalH, 20);
  var thumbY = (top / Math.max(1, maxTop)) * (cv.height - thumbH);
  out.vertical = {
    scrollable: true,
    track: { x: cv.width - BAR, y: 0, width: BAR, height: cv.height },
    thumbCenter: { x: cv.width - BAR / 2, y: thumbY + thumbH / 2 },
    thumbHeight: thumbH,
    pxPerPx: maxTop / Math.max(1, cv.height - thumbH),
  };
} else {
  out.vertical = { scrollable: false };
}
if (totalW > cv.width) {
  var thumbW = Math.max(cv.width * cv.width / totalW, 20);
  var thumbX = (left / Math.max(1, maxLeft)) * (cv.width - thumbW);
  out.horizontal = {
    scrollable: true,
    track: { x: 0, y: cv.height - BAR, width: cv.width, height: BAR },
    thumbCenter: { x: thumbX + thumbW / 2, y: cv.height - BAR / 2 },
    thumbWidth: thumbW,
    pxPerPx: maxLeft / Math.max(1, cv.width - thumbW),
  };
} else {
  out.horizontal = { scrollable: false };
}
return JSON.stringify(out);
"""

# ---------------------------------------------------------------------------
# 单元格颜色明细：背景 / 文本颜色 / 可交互文本判定（含 hover 态由外层控制鼠标）
# ---------------------------------------------------------------------------
CELL_COLORS = r"""
var t = window.__vt;
var cell = t.scenegraph.getCell(arguments[0], arguments[1]);
if (!cell) return JSON.stringify({ error: 'no-cell' });
var out = {
  col: arguments[0], row: arguments[1],
  background: null,
  textColors: [],
  isInteractiveText: false,
  interactiveEvidence: [],
  cellType: null,
  value: null
};
try { out.cellType = t.getCellType ? t.getCellType(arguments[0], arguments[1]) : null; } catch (e) {}
try { out.value = (function(){ var v = t.getCellValue(arguments[0], arguments[1]); return v === null || v === undefined ? null : String(v).slice(0, 120); })(); } catch (e2) {}
var walk = function (node, depth) {
  if (!node || depth > 7) return;
  var a = node.attribute || {};
  var type = String(node.type || '').toLowerCase();
  if ((type === 'rect' || type === 'group') && out.background === null &&
      a.fill !== undefined && a.fill !== null) {
    out.background = String(a.fill);
  }
  if (type === 'text' || a.text !== undefined) {
    if (a.fill !== null && a.fill !== undefined) {
      var fs = String(a.fill);
      if (out.textColors.indexOf(fs) < 0) out.textColors.push(fs);
    }
    if (a.cursor === 'pointer') {
      out.isInteractiveText = true;
      out.interactiveEvidence.push('cursor:pointer');
    }
    if (a.underline === 1 || a.underline === true || String(a.underline) === 'underline') {
      out.isInteractiveText = true;
      out.interactiveEvidence.push('underline');
    }
  }
  var kids = node.children || (node.getChildren && node.getChildren()) || [];
  for (var i = 0; i < kids.length; i++) walk(kids[i], depth + 1);
};
walk(cell, 0);
if (out.cellType === 'link') {
  out.isInteractiveText = true;
  out.interactiveEvidence.push('getCellType:link');
}
return JSON.stringify(out);
"""

VTABLE_SCRIPTS["scrollbar_geometry"] = SCROLLBAR_GEOMETRY
VTABLE_SCRIPTS["cell_colors"] = CELL_COLORS

# ---------------------------------------------------------------------------
# 场景图深度文本遍历 + 回退提取
# 设计：scenegraph 渲染文本优先（显示真相，含格式化器/自定义渲染的产物），
# 依次回退 getCellOverflowText → getCellValue → getCellRawValue → 业务记录字段，
# 返回来源标注，解决"显示值 ≠ 数据值"的断言问题。
# ---------------------------------------------------------------------------
CELL_TEXT_DEEP = r"""
var t = window.__vt;
if (!t) return JSON.stringify({ bound: false });
var col = arguments[0], row = arguments[1];
var sgTexts = [];
var cell = null;
try { cell = t.scenegraph.getCell(col, row); } catch (e) {}
if (cell) {
  var walk = function (node, depth) {
    if (!node || depth > 10 || sgTexts.length >= 20) return;
    var a = node.attribute || {};
    if (a.text !== undefined && a.text !== null) {
      var raw = a.text;
      if (raw && typeof raw === 'object') {
        if (Array.isArray(raw)) {
          var joined = [];
          for (var i = 0; i < raw.length; i++) {
            var part = raw[i];
            if (part && typeof part === 'object' && part.text !== undefined) joined.push(String(part.text));
            else if (typeof part !== 'object') joined.push(String(part));
          }
          var j = joined.join('').trim();
          if (j) sgTexts.push(j);
        } else if (raw.text !== undefined && raw.text !== null) {
          var inner = String(raw.text).trim();
          if (inner) sgTexts.push(inner);
        }
      } else if (typeof raw === 'string' || typeof raw === 'number') {
        var s = String(raw).trim();
        if (s) sgTexts.push(s);
      }
    }
    var kids = node.children || (node.getChildren && node.getChildren()) || [];
    for (var k = 0; k < kids.length; k++) walk(kids[k], depth + 1);
  };
  walk(cell, 0);
}
var overflow = null;
try { overflow = t.getCellOverflowText ? t.getCellOverflowText(col, row) : null; } catch (e2) {}
var cellValue = null;
try { cellValue = t.getCellValue ? t.getCellValue(col, row) : null; } catch (e3) {}
if (cellValue && typeof cellValue === 'object') cellValue = JSON.stringify(cellValue).slice(0, 300);
var rawValue = null;
try { rawValue = t.getCellRawValue ? t.getCellRawValue(col, row) : null; } catch (e4) {}
if (rawValue && typeof rawValue === 'object') rawValue = JSON.stringify(rawValue).slice(0, 300);
var originValue = null, field = null;
try {
  var def = t.getBodyColumnDefine && t.getBodyColumnDefine(col, row);
  field = def ? (def.field || def.key) : null;
  var record = t.getCellOriginRecord ? t.getCellOriginRecord(col, row) : null;
  if (record && field && record[field] !== undefined && record[field] !== null && typeof record[field] !== 'object') {
    originValue = String(record[field]).slice(0, 300);
  }
} catch (e5) {}
var display = (sgTexts.length ? sgTexts.join('') : (overflow || (cellValue === null ? originValue : String(cellValue))));
var source = sgTexts.length ? 'scenegraph' : (overflow ? 'overflow' : (cellValue !== null ? 'cellValue' : (originValue !== null ? 'originRecord' : 'none')));
return JSON.stringify({
  col: col, row: row, field: field,
  display: display === null ? null : String(display).slice(0, 300),
  source: source,
  sgTexts: sgTexts,
  overflowText: overflow,
  cellValue: cellValue,
  rawValue: rawValue,
  originValue: originValue,
});
"""

VTABLE_SCRIPTS["cell_text_deep"] = CELL_TEXT_DEEP

# ---------------------------------------------------------------------------
# 可视化多粒度感知与交互锚点计算
# ---------------------------------------------------------------------------
VTABLE_INSPECT = r"""
// inspect_vtable
var t = window.__vt;
if (!t || !t.scenegraph) return JSON.stringify({ bound: false });

var num = function (v) { var n = Number(v); return Number.isFinite(n) ? n : null; };
var target_col = (arguments[0] !== null && arguments[0] !== undefined) ? num(arguments[0]) : null;
var target_row = (arguments[1] !== null && arguments[1] !== undefined) ? num(arguments[1]) : null;
var col_range = (Array.isArray(arguments[2]) && arguments[2].length >= 2) ? [num(arguments[2][0]), num(arguments[2][1])] : null;
var row_range = (Array.isArray(arguments[3]) && arguments[3].length >= 2) ? [num(arguments[3][0]), num(arguments[3][1])] : null;

var structural = { '': 1, 'group': 1, 'cell': 1, 'cell-group': 1, 'content': 1, 'text': 1, 'background': 1, 'border': 1, 'line': 1, 'rect': 1, 'shadow': 1, 'stroke': 1 };
var childrenOf = function (node) {
  if (!node) return [];
  if (Array.isArray(node.children)) return node.children;
  try { var c = node.getChildren && node.getChildren(); if (Array.isArray(c)) return c; } catch (e) {}
  var out = [];
  try { if (typeof node.forEachChildren === 'function') node.forEachChildren(function (ch) { out.push(ch); }); } catch (e2) {}
  return out;
};
var iconName = function (node) {
  var a = node.attribute || {};
  var v = node.name || a.name || a.iconName || a.funcType || '';
  return String(v || '');
};

var getCellNode = function (c, r) {
  var cell = null;
  try { cell = t.scenegraph.getCell(c, r); } catch (e) {}
  return cell;
};

var getCellBounds = function (c, r, cell) {
  var rect = null;
  try { rect = t.getCellRelativeRect(c, r); } catch (e) {}
  var left, top, right, bottom;
  if (rect) {
    var x1 = rect.left !== undefined ? rect.left : (rect.x1 !== undefined ? rect.x1 : (rect.bounds && rect.bounds.x1));
    var y1 = rect.top !== undefined ? rect.top : (rect.y1 !== undefined ? rect.y1 : (rect.bounds && rect.bounds.y1));
    var x2 = rect.right !== undefined ? rect.right : (rect.x2 !== undefined ? rect.x2 : (rect.bounds && rect.bounds.x2));
    var y2 = rect.bottom !== undefined ? rect.bottom : (rect.y2 !== undefined ? rect.y2 : (rect.bounds && rect.bounds.y2));
    if (Number.isFinite(Number(x1)) && Number.isFinite(Number(y1)) && Number.isFinite(Number(x2)) && Number.isFinite(Number(y2))) {
      left = Number(x1); top = Number(y1); right = Number(x2); bottom = Number(y2);
    }
  }
  if (left === undefined && cell && cell.globalAABBBounds) {
    var b = cell.globalAABBBounds;
    if (Number.isFinite(Number(b.x1)) && Number.isFinite(Number(b.y1)) && Number.isFinite(Number(b.x2)) && Number.isFinite(Number(b.y2))) {
      left = Number(b.x1); top = Number(b.y1); right = Number(b.x2); bottom = Number(b.y2);
    }
  }
  if (left === undefined) {
    return { box: null, center: null, blank_point: null };
  }
  var w = right - left;
  var h = bottom - top;
  var box = { x: left, y: top, width: w, height: h };
  var cx = (left + right) / 2;
  var cy = (top + bottom) / 2;
  var center = { x: cx, y: cy };
  var blank_x = w < 20 ? cx : (left + w - 8);
  var blank_point = { x: blank_x, y: cy };
  return { box: box, center: center, blank_point: blank_point };
};

var extractCellText = function (cell, c, r) {
  var sgTexts = [];
  if (cell) {
    var walk = function (node, depth) {
      if (!node || depth > 10 || sgTexts.length >= 20) return;
      var a = node.attribute || {};
      if (a.text !== undefined && a.text !== null) {
        var raw = a.text;
        if (raw && typeof raw === 'object') {
          if (Array.isArray(raw)) {
            var joined = [];
            for (var i = 0; i < raw.length; i++) {
              var part = raw[i];
              if (part && typeof part === 'object' && part.text !== undefined) joined.push(String(part.text));
              else if (typeof part !== 'object') joined.push(String(part));
            }
            var j = joined.join('').trim();
            if (j) sgTexts.push(j);
          } else if (raw.text !== undefined && raw.text !== null) {
            var inner = String(raw.text).trim();
            if (inner) sgTexts.push(inner);
          }
        } else if (typeof raw === 'string' || typeof raw === 'number') {
          var s = String(raw).trim();
          if (s) sgTexts.push(s);
        }
      }
      var kids = node.children || (node.getChildren && node.getChildren()) || [];
      for (var k = 0; k < kids.length; k++) walk(kids[k], depth + 1);
    };
    walk(cell, 0);
  }
  if (sgTexts.length > 0) {
    return sgTexts.join('');
  }
  var overflow = null;
  try { overflow = t.getCellOverflowText ? t.getCellOverflowText(c, r) : null; } catch (e) {}
  if (overflow !== null && overflow !== undefined && String(overflow).trim()) {
    return String(overflow).trim().slice(0, 300);
  }
  var cv = null;
  try { cv = t.getCellValue ? t.getCellValue(c, r) : null; } catch (e2) {}
  if (cv !== null && cv !== undefined) {
    if (typeof cv === 'object') {
      try { return JSON.stringify(cv).slice(0, 300); } catch (e3) { return String(cv).slice(0, 300); }
    }
    return String(cv).slice(0, 300);
  }
  var rawVal = null;
  try { rawVal = t.getCellRawValue ? t.getCellRawValue(c, r) : null; } catch (e4) {}
  if (rawVal !== null && rawVal !== undefined) {
    if (typeof rawVal === 'object') {
      try { return JSON.stringify(rawVal).slice(0, 300); } catch (e5) { return String(rawVal).slice(0, 300); }
    }
    return String(rawVal).slice(0, 300);
  }
  try {
    var def = t.getBodyColumnDefine && t.getBodyColumnDefine(c, r);
    var field = def ? (def.field || def.key) : null;
    var record = t.getCellOriginRecord ? t.getCellOriginRecord(c, r) : null;
    if (record && field && record[field] !== undefined && record[field] !== null && typeof record[field] !== 'object') {
      return String(record[field]).slice(0, 300);
    }
  } catch (e6) {}
  return "";
};

var extractCellStyle = function (cell, c, r) {
  var bg_color = null;
  var text_color = null;
  var interactive = false;
  if (cell) {
    var a = cell.attribute || {};
    if (a.fill !== undefined && a.fill !== null) {
      bg_color = String(a.fill);
    }
    var walk = function (node, depth) {
      if (!node || depth > 7) return;
      var na = node.attribute || {};
      var type = String(node.type || '').toLowerCase();
      if ((type === 'rect' || type === 'group') && bg_color === null && na.fill !== undefined && na.fill !== null) {
        bg_color = String(na.fill);
      }
      if (type === 'text' || na.text !== undefined) {
        if (text_color === null && na.fill !== undefined && na.fill !== null) {
          text_color = String(na.fill);
        }
        if (na.cursor === 'pointer') interactive = true;
        if (na.underline === 1 || na.underline === true || String(na.underline) === 'underline') interactive = true;
      }
      if (na.cursor === 'pointer') interactive = true;
      var kids = node.children || (node.getChildren && node.getChildren()) || [];
      for (var k = 0; k < kids.length; k++) walk(kids[k], depth + 1);
    };
    walk(cell, 0);
  }
  try {
    if (t.getCellType && t.getCellType(c, r) === 'link') interactive = true;
  } catch (e) {}
  try {
    if (t.isCellEditable && t.isCellEditable(c, r)) interactive = true;
  } catch (e2) {}
  return { bg_color: bg_color, text_color: text_color, interactive: interactive };
};

var extractCellIcons = function (cell) {
  if (!cell) return [];
  var icons = [];
  var queue = [{ node: cell, depth: 0 }];
  var seen = new Set();
  var visited = 0;
  while (queue.length && visited < 300 && icons.length < 12) {
    var cur = queue.shift();
    var node = cur.node;
    if (!node || seen.has(node) || cur.depth > 8) continue;
    seen.add(node); visited++;
    if (cur.depth > 0) {
      var a = node.attribute || {};
      var name = iconName(node);
      var isText = String(node.type || '').toLowerCase() === 'text' || a.text !== undefined;
      var b = node.globalAABBBounds;
      if (name && !isText && !structural[name.toLowerCase()] && b) {
        var x1 = Number(b.x1), y1 = Number(b.y1), x2 = Number(b.x2), y2 = Number(b.y2);
        var w = x2 - x1, h = y2 - y1;
        if ([x1, y1, x2, y2, w, h].every(Number.isFinite) && w > 0 && w < 300 && h > 0 && h < 300) {
          var fname = name.toLowerCase();
          var fn = 'custom';
          if (fname.indexOf('sort') >= 0) fn = 'sort';
          else if (fname.indexOf('filter') >= 0) fn = 'filter';
          else if (fname.indexOf('dropdown') >= 0 || fname.indexOf('downward') >= 0) fn = 'dropdown';
          else if (fname.indexOf('freeze') >= 0) fn = 'freeze';
          else if (fname.indexOf('checkbox') >= 0) fn = 'checkbox';
          else if (fname.indexOf('expand') >= 0) fn = 'expand';
          else if (fname.indexOf('collapse') >= 0) fn = 'collapse';
          icons.push({
            name: name,
            function: fn,
            box: { x: x1, y: y1, width: w, height: h },
            center: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 }
          });
        }
      }
    }
    var kids = childrenOf(node);
    for (var i = 0; i < kids.length; i++) queue.push({ node: kids[i], depth: cur.depth + 1 });
  }
  return icons;
};

// Scope 1: 单单元格感知
if (target_col !== null && target_row !== null) {
  var cell = getCellNode(target_col, target_row);
  var text = extractCellText(cell, target_col, target_row);
  var style = extractCellStyle(cell, target_col, target_row);
  var geo = getCellBounds(target_col, target_row, cell);
  var icons = extractCellIcons(cell);
  return JSON.stringify({
    bound: true,
    scope: "cell",
    col: target_col,
    row: target_row,
    text: text,
    bg_color: style.bg_color,
    text_color: style.text_color,
    interactive: style.interactive,
    bounds: geo.box,
    center: geo.center,
    blank_point: geo.blank_point,
    icons: icons
  });
}

// Scope 2: 单列感知（含表头、拖拽调宽分界点、换序锚点及视口内可见行）
if (target_col !== null && target_row === null) {
  var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
  var headerRow = Math.max(0, headerRows - 1);
  var field = '', title = '', width = 0;
  try { field = String(t.getBodyField ? t.getBodyField(target_col, headerRows) : ''); } catch (e) {}
  try {
    var def = t.getBodyColumnDefine ? t.getBodyColumnDefine(target_col, headerRows) : null;
    if (!field && def) field = String(def.field || def.key || '');
    if (def && (def.title || def.header)) title = String(def.title || def.header);
    if (def && def.width) width = num(def.width) || 0;
  } catch (e2) {}
  if (!title) {
    try { title = String(t.getCellValue ? t.getCellValue(target_col, headerRow) : ''); } catch (e3) {}
  }
  if (!width) {
    try { width = t.getColWidth ? t.getColWidth(target_col) : 0; } catch (e4) {}
  }
  var headerCell = getCellNode(target_col, headerRow);
  var headerGeo = getCellBounds(target_col, headerRow, headerCell);
  if (!width && headerGeo.box) width = headerGeo.box.width;
  var headerIcons = extractCellIcons(headerCell);
  var borderRight = headerGeo.box ? { x: headerGeo.box.x + headerGeo.box.width, y: headerGeo.center.y } : null;

  var visRange = null;
  try { visRange = t.getBodyVisibleCellRange ? t.getBodyVisibleCellRange() : null; } catch (e5) {}
  var rStart = (visRange && visRange.rowStart !== undefined) ? visRange.rowStart : headerRows;
  var rEnd = (visRange && visRange.rowEnd !== undefined) ? visRange.rowEnd : Math.min(num(t.rowCount) || 0, headerRows + 20);
  var cells = [];
  for (var r = rStart; r <= rEnd; r++) {
    var cNode = getCellNode(target_col, r);
    var cText = extractCellText(cNode, target_col, r);
    var cStyle = extractCellStyle(cNode, target_col, r);
    var cGeo = getCellBounds(target_col, r, cNode);
    cells.push({
      row: r,
      text: cText,
      bg_color: cStyle.bg_color,
      text_color: cStyle.text_color,
      interactive: cStyle.interactive,
      bounds: cGeo.box,
      center: cGeo.center,
      blank_point: cGeo.blank_point
    });
  }
  return JSON.stringify({
    bound: true,
    scope: "column",
    col: target_col,
    field: field,
    title: title,
    width: width,
    header_center: headerGeo.center,
    border_right: borderRight,
    header_icons: headerIcons,
    cells: cells
  });
}

// Scope 3: 单行感知（行背景色、行高及全列紧凑数据）
if (target_row !== null && target_col === null) {
  var colCount = num(t.colCount) || 0;
  var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
  var rowHeight = 0;
  try { rowHeight = t.getRowHeight ? t.getRowHeight(target_row) : 0; } catch (e) {}
  var rowBg = null;
  var rowCells = [];
  for (var c = 0; c < colCount; c++) {
    var cNode = getCellNode(c, target_row);
    var cText = extractCellText(cNode, c, target_row);
    var cStyle = extractCellStyle(cNode, c, target_row);
    var cGeo = getCellBounds(c, target_row, cNode);
    if (rowBg === null && cStyle.bg_color) rowBg = cStyle.bg_color;
    if (!rowHeight && cGeo.box) rowHeight = cGeo.box.height;
    var field = '', title = '';
    try {
      var def = t.getBodyColumnDefine ? t.getBodyColumnDefine(c, headerRows) : null;
      if (def) { field = String(def.field || def.key || ''); title = String(def.title || def.header || ''); }
    } catch (e2) {}
    rowCells.push({
      col: c,
      field: field,
      title: title,
      text: cText,
      bg_color: cStyle.bg_color,
      text_color: cStyle.text_color,
      interactive: cStyle.interactive,
      bounds: cGeo.box,
      center: cGeo.center,
      blank_point: cGeo.blank_point,
      icons: extractCellIcons(cNode)
    });
  }
  return JSON.stringify({
    bound: true,
    scope: "row",
    row: target_row,
    height: rowHeight,
    bg_color: rowBg,
    cells: rowCells
  });
}

// Scope 4: 区域切片感知（计算矩形矩阵与安全拖选起点/终点）
if (col_range !== null || row_range !== null) {
  var colCount = num(t.colCount) || 0;
  var rowCount = num(t.rowCount) || 0;
  var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
  var visRange = null;
  try { visRange = t.getBodyVisibleCellRange ? t.getBodyVisibleCellRange() : null; } catch (e) {}

  var c0 = col_range ? col_range[0] : (visRange && visRange.colStart !== undefined ? visRange.colStart : 0);
  var c1 = col_range ? col_range[1] : (visRange && visRange.colEnd !== undefined ? visRange.colEnd : Math.min(colCount - 1, 10));
  var r0 = row_range ? row_range[0] : (visRange && visRange.rowStart !== undefined ? visRange.rowStart : headerRows);
  var r1 = row_range ? row_range[1] : (visRange && visRange.rowEnd !== undefined ? visRange.rowEnd : Math.min(rowCount - 1, headerRows + 10));

  c0 = Math.max(0, Math.min(c0, colCount - 1));
  c1 = Math.max(0, Math.min(c1, colCount - 1));
  r0 = Math.max(0, Math.min(r0, rowCount - 1));
  r1 = Math.max(0, Math.min(r1, rowCount - 1));

  if (c0 > c1) { var tc = c0; c0 = c1; c1 = tc; }
  if (r0 > r1) { var tr = r0; r0 = r1; r1 = tr; }

  var startCell = getCellNode(c0, r0);
  var startGeo = getCellBounds(c0, r0, startCell);
  var endCell = getCellNode(c1, r1);
  var endGeo = getCellBounds(c1, r1, endCell);

  var cells = [];
  for (var r = r0; r <= r1; r++) {
    var rowList = [];
    for (var c = c0; c <= c1; c++) {
      var cNode = getCellNode(c, r);
      var cText = extractCellText(cNode, c, r);
      var cStyle = extractCellStyle(cNode, c, r);
      var cGeo = getCellBounds(c, r, cNode);
      rowList.push({
        col: c,
        row: r,
        text: cText,
        bg_color: cStyle.bg_color,
        text_color: cStyle.text_color,
        interactive: cStyle.interactive,
        bounds: cGeo.box,
        center: cGeo.center,
        blank_point: cGeo.blank_point
      });
    }
    cells.push(rowList);
  }

  return JSON.stringify({
    bound: true,
    scope: "range",
    col_range: [c0, c1],
    row_range: [r0, r1],
    drag_start: startGeo.blank_point,
    drag_end: endGeo.blank_point,
    cells: cells
  });
}

// Scope 5: 全表紧凑可视骨架（列头 + 扁平可视矩阵）
var colCount = num(t.colCount) || 0;
var rowCount = num(t.rowCount) || 0;
var headerRows = num(t.columnHeaderLevelCount) || num(t.headerRowCount) || 1;
var headerRow = Math.max(0, headerRows - 1);
var visRange = null;
try { visRange = t.getBodyVisibleCellRange ? t.getBodyVisibleCellRange() : null; } catch (e) {}

var cStart = (visRange && visRange.colStart !== undefined) ? visRange.colStart : 0;
var cEnd = (visRange && visRange.colEnd !== undefined) ? visRange.colEnd : Math.min(colCount - 1, 15);
var rStart = (visRange && visRange.rowStart !== undefined) ? visRange.rowStart : headerRows;
var rEnd = (visRange && visRange.rowEnd !== undefined) ? Math.min(visRange.rowEnd, rStart + 19) : Math.min(rowCount - 1, headerRows + 19);

var headers = [];
for (var c = 0; c < colCount; c++) {
  var field = '', title = '', width = 0;
  try {
    var def = t.getBodyColumnDefine ? t.getBodyColumnDefine(c, headerRows) : null;
    if (def) {
      field = String(def.field || def.key || '');
      title = String(def.title || def.header || '');
      if (def.width) width = num(def.width) || 0;
    }
  } catch (e1) {}
  if (!field) { try { field = String(t.getBodyField ? t.getBodyField(c, headerRows) : ''); } catch (e2) {} }
  if (!title) { try { title = String(t.getCellValue ? t.getCellValue(c, headerRow) : ''); } catch (e3) {} }
  if (!width) { try { width = t.getColWidth ? t.getColWidth(c) : 0; } catch (e4) {} }
  headers.push({ col: c, field: field, title: title, width: width });
}

var rows = [];
for (var r = rStart; r <= rEnd; r++) {
  var rowData = { row: r, cells: [] };
  var rowBg = null;
  for (var col = cStart; col <= cEnd; col++) {
    var cNode = getCellNode(col, r);
    var cText = extractCellText(cNode, col, r);
    var cStyle = extractCellStyle(cNode, col, r);
    if (rowBg === null && cStyle.bg_color && cStyle.bg_color !== '#fff' && cStyle.bg_color !== '#ffffff' && cStyle.bg_color !== 'rgb(255, 255, 255)') {
      rowBg = cStyle.bg_color;
    }
    var cellData = { col: col, text: cText };
    if (cStyle.interactive) cellData.interactive = true;
    if (cStyle.bg_color && cStyle.bg_color !== rowBg && cStyle.bg_color !== '#fff' && cStyle.bg_color !== '#ffffff' && cStyle.bg_color !== 'rgb(255, 255, 255)') {
      cellData.bg_color = cStyle.bg_color;
    }
    if (cStyle.text_color && cStyle.text_color !== '#000' && cStyle.text_color !== '#000000' && cStyle.text_color !== '#333' && cStyle.text_color !== '#333333' && cStyle.text_color !== 'rgb(0, 0, 0)') {
      cellData.text_color = cStyle.text_color;
    }
    rowData.cells.push(cellData);
  }
  if (rowBg) rowData.bg_color = rowBg;
  rows.push(rowData);
}

return JSON.stringify({
  bound: true,
  scope: "visible_all",
  visible_range: { colStart: cStart, colEnd: cEnd, rowStart: rStart, rowEnd: rEnd },
  colCount: colCount,
  rowCount: rowCount,
  headers: headers,
  rows: rows
});
"""

VTABLE_SCRIPTS["inspect"] = VTABLE_INSPECT
