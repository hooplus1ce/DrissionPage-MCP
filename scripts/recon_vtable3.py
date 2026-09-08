"""VTable 滚动条与颜色态深度侦察（只读 + 受控交互）。

1. scenegraph 顶层结构 → 找滚动条节点（横向/纵向）与其几何
2. 横向滚轮实验：actions.scroll(delta_x) 是否驱动 scrollLeft
3. 颜色态实验：同一单元格 hover 前后 / 点击前后的背景与文本颜色变化
4. 可交互文本判定依据：getCellType + scenegraph 文本节点 cursor/underline
"""

import json
import time

from DrissionPage import Chromium

b = Chromium("127.0.0.1:9222")
t = b.latest_tab
f = None
for fr in t.eles("tag:iframe"):
    if fr.states.is_displayed:
        f = fr
        break
print("frame:", str(f.attr("src"))[:80])

# 绑定
f.run_js("""
var el = null;
var roots = document.querySelectorAll('.vtable');
for (var i = 0; i < roots.length; i++) { var r = roots[i].getBoundingClientRect(); if (r.width > 0) { el = roots[i]; break; } }
var fk = Object.keys(el.parentElement || el).find(function(k){ return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; });
var queue = [{node: (el.parentElement || el)[fk], depth: 0}]; var seen = new Set();
while (queue.length) { var cur = queue.shift(); var node = cur.node;
  if (!node || seen.has(node) || cur.depth > 30) continue; seen.add(node);
  var st = node.stateNode; var inst = st ? (st.vtableInstance || st.tableInstance) : null;
  if (inst && typeof inst.getCellRelativeRect === 'function') { window.__vt = inst; break; }
  if (node.child) queue.push({node: node.child, depth: cur.depth + 1});
  if (node.sibling) queue.push({node: node.sibling, depth: cur.depth + 1});
  if (node.return) queue.push({node: node.return, depth: cur.depth + 1}); }
return window.__vt ? 'bound' : 'fail';
""")

# 1. scenegraph 顶层结构（找滚动条）
print("\n=== scenegraph 顶层结构 ===")
print(f.run_js("""
var sg = window.__vt.scenegraph;
var out = [];
var root = sg.root || sg.tableGroup || sg;
var kids = root.children || (root.getChildren && root.getChildren()) || [];
for (var i = 0; i < kids.length; i++) {
  var k = kids[i];
  out.push({name: k.name || k.type, type: k.type, bounds: k.globalAABBBounds ? {
    x1: Math.round(k.globalAABBBounds.x1), y1: Math.round(k.globalAABBBounds.y1),
    x2: Math.round(k.globalAABBBounds.x2), y2: Math.round(k.globalAABBBounds.y2)
  } : null});
}
return JSON.stringify(out);
"""))

# 2. 滚动条节点探测（按名字含 scroll 深搜 4 层）
print("\n=== 滚动条节点搜索 ===")
print(f.run_js("""
var sg = window.__vt.scenegraph;
var hits = [];
var walk = function(node, depth, path) {
  if (!node || depth > 4 || hits.length >= 20) return;
  var name = String(node.name || node.type || '');
  if (name.toLowerCase().indexOf('scroll') >= 0 || name.toLowerCase().indexOf('bar') >= 0) {
    var b = node.globalAABBBounds;
    hits.push({path: path, name: name, type: node.type,
      bounds: b ? {x1: Math.round(b.x1), y1: Math.round(b.y1), x2: Math.round(b.x2), y2: Math.round(b.y2)} : null,
      children: (node.children || []).length});
  }
  var kids = node.children || (node.getChildren && node.getChildren()) || [];
  for (var i = 0; i < kids.length; i++) walk(kids[i], depth + 1, path + '/' + (kids[i].name || kids[i].type));
};
walk(sg.root || sg.tableGroup || sg, 0, '');
return JSON.stringify(hits);
"""))

# 3. 横向滚轮实验
print("\n=== 横向滚轮实验 ===")
def scroll_state():
    return json.loads(f.run_js("var t = window.__vt; return JSON.stringify({top: t.scrollTop, left: t.scrollLeft});"))
print("初始:", scroll_state())
t.actions.move_to((850, 550)).scroll(delta_y=0, delta_x=300)
time.sleep(0.6)
print("delta_x=300 后:", scroll_state())
t.actions.move_to((850, 550)).scroll(delta_y=0, delta_x=-300)
time.sleep(0.6)
print("delta_x=-300 后:", scroll_state())

# 4. 颜色态实验：hover 前后 / 点击前后
print("\n=== 颜色态实验 (col=3,row=5) ===")
CELL_PAINTS = """
var t = window.__vt;
var cell = t.scenegraph.getCell(arguments[0], arguments[1]);
if (!cell) return JSON.stringify({error: 'no-cell'});
var out = {bg: null, text: [], cursor: [], underline: []};
var walk = function(node, depth) {
  if (!node || depth > 6) return;
  var a = node.attribute || {};
  var type = String(node.type || '').toLowerCase();
  if (type === 'rect' || type === 'group') {
    if (a.fill !== undefined && a.fill !== null && out.bg === null) out.bg = String(a.fill);
  }
  if (type === 'text' || a.text !== undefined) {
    if (a.fill !== null && a.fill !== undefined) out.text.push(String(a.fill));
    if (a.cursor) out.cursor.push(String(a.cursor));
    if (a.underline !== undefined && a.underline !== null) out.underline.push(String(a.underline));
  }
  var kids = node.children || (node.getChildren && node.getChildren()) || [];
  for (var i = 0; i < kids.length; i++) walk(kids[i], depth + 1);
};
walk(cell, 0);
return JSON.stringify(out);
"""
# 目标格视口坐标
pt = json.loads(f.run_js("""
var t = window.__vt;
var r = t.getCellRelativeRect(arguments[0], arguments[1]);
var cv = (t.canvas || document.querySelector('.vtable canvas')).getBoundingClientRect();
var fr = window.frameElement ? window.frameElement.getBoundingClientRect() : {left: 0, top: 0};
return JSON.stringify({x: fr.left + cv.x + (r.bounds.x1 + r.bounds.x2) / 2, y: fr.top + cv.y + (r.bounds.y1 + r.bounds.y2) / 2});
""", 3, 5))
before = json.loads(f.run_js(CELL_PAINTS, 3, 5))
print("hover前:", before)

# hover（真实鼠标移上去，不点击）
t.actions.move_to((pt["x"], pt["y"]))
time.sleep(0.8)
hover = json.loads(f.run_js(CELL_PAINTS, 3, 5))
print("hover后:", hover)

# 同行相邻格的背景（行高亮检测）
sibling = json.loads(f.run_js(CELL_PAINTS, 4, 5))
print("同行邻格(4,5) hover中:", sibling)

# 点击后
t.actions.move_to((pt["x"], pt["y"])).click()
time.sleep(0.6)
clicked = json.loads(f.run_js(CELL_PAINTS, 3, 5))
print("点击后:", clicked)

# 5. 可交互文本：getCellType + 全表 link 列
print("\n=== 可交互文本判定 ===")
print(f.run_js("""
var t = window.__vt;
var out = [];
for (var col = 0; col < t.colCount; col++) {
  var type = '';
  try { type = String(t.getCellType(col, 3)); } catch (e) {}
  if (type === 'link' || type === 'button') out.push({col: col, type: type, field: (function(){ try { return t.getBodyField(col, 1); } catch (e) { return ''; } })()});
}
return JSON.stringify(out);
"""))
