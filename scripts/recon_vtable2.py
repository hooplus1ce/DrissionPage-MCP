"""VTable 深度侦察：API 全集枚举 + 浮层位置模式 + 选区高亮实测定证（只读+一次点击）。"""

import json

from DrissionPage import Chromium

API_ENUM = """
var t = window.__vt;
if (!t) return JSON.stringify({error: 'not-bound'});
var names = new Set();
var obj = t;
var depth = 0;
while (obj && depth < 5) {
  Object.getOwnPropertyNames(obj).forEach(function(n){ names.add(n); });
  obj = Object.getPrototypeOf(obj);
  depth++;
}
var fns = [];
names.forEach(function(n){
  try { if (typeof t[n] === 'function') fns.push(n); } catch(e) {}
});
fns.sort();
var props = [];
names.forEach(function(n){
  try { if (typeof t[n] !== 'function') props.push(n + '=' + String(JSON.stringify(t[n])).slice(0, 40)); } catch(e) {}
});
return JSON.stringify({functions: fns, propSample: props.slice(0, 60)});
"""

FLOATING_LAYERS = """
var canvas = (window.__vt && window.__vt.canvas) || document.querySelector('.vtable canvas');
var cr = canvas.getBoundingClientRect();
var candidates = document.querySelectorAll(
  '.vtable-filter-menu, .ant-tooltip, .ant-dropdown, .ant-select-dropdown, ' +
  '.ant-popover, .ant-modal, .ant-message, .vtable-context-menu, [class*="menu"], [class*="popup"], [class*="float"]'
);
var out = [];
candidates.forEach(function(el) {
  var r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return;
  var style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return;
  var near = !(r.right < cr.left - 50 || r.left > cr.right + 50 || r.bottom < cr.top - 50 || r.top > cr.bottom + 50);
  var cls = String(el.className || '').slice(0, 70);
  out.push({
    cls: cls,
    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    nearCanvas: near,
    inCanvasArea: r.top >= cr.top - 10 && r.bottom <= cr.bottom + 10 && r.left >= cr.left - 10 && r.right <= cr.right + 10,
    zIndex: style.zIndex,
    position: style.position,
    parentIsBody: el.parentElement === document.body
  });
});
return JSON.stringify({canvasRect: {x: cr.x, y: cr.y, w: cr.width, h: cr.height}, layers: out.slice(0, 30)});
"""

CELL_STATE = """
var t = window.__vt;
if (!t || !t.scenegraph) return JSON.stringify({error: 'no-scenegraph'});
var col = arguments[0], row = arguments[1];
var cell = null;
try { cell = t.scenegraph.getCell(col, row); } catch (e) {}
if (!cell) return JSON.stringify({error: 'no-cell'});
var nodes = [], paints = [];
var walk = function(node, depth) {
  if (!node || depth > 8 || nodes.length >= 80) return;
  var a = node.attribute || {};
  var visual = {};
  ['fill', 'background', 'backgroundColor', 'stroke', 'lineWidth', 'opacity', 'cornerRadius'].forEach(function(key){
    if (a[key] !== undefined && a[key] !== null) visual[key] = a[key];
  });
  if (Object.keys(visual).length) {
    nodes.push({type: String(node.type || node.name || 'node'), visual: visual});
    ['fill', 'background', 'backgroundColor', 'stroke'].forEach(function(key){
      if (visual[key] !== undefined) paints.push(String(visual[key]));
    });
  }
  var children = node.children || (node.getChildren && node.getChildren());
  if (Array.isArray(children)) children.forEach(function(ch){ walk(ch, depth + 1); });
  else if (node.forEachChildren) node.forEachChildren(function(ch){ walk(ch, depth + 1); });
};
walk(cell, 0);
return JSON.stringify({signature: JSON.stringify(nodes).slice(0, 800), paints: paints.slice(0, 12), nodeCount: nodes.length});
"""


def main() -> None:
    b = Chromium("127.0.0.1:9222")
    t = b.latest_tab
    f = None
    for fr in t.eles("tag:iframe"):
        if fr.states.is_displayed:
            f = fr
            break
    print("frame:", str(f.attr("src"))[:80])

    # 1. VTable 实例绑定 + API 全集
    f.run_js(open("scripts/vtable_probe_bind.js", encoding="utf-8").read()) if False else None
    bind = """
var el = null;
var roots = document.querySelectorAll('.vtable');
for (var i = 0; i < roots.length; i++) {
  var r = roots[i].getBoundingClientRect();
  if (r.width > 0 && r.height > 0) { el = roots[i]; break; }
}
if (!el) return 'no-vtable';
var fk = null;
var keys = Object.keys(el.parentElement || el);
for (var i = 0; i < keys.length; i++) {
  if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternalInstance') === 0) { fk = keys[i]; break; }
}
var queue = [{node: (el.parentElement || el)[fk], depth: 0}];
var seen = new Set();
while (queue.length) {
  var cur = queue.shift();
  var node = cur.node;
  if (!node || seen.has(node) || cur.depth > 30) continue;
  seen.add(node);
  var st = node.stateNode;
  var inst = st ? (st.vtableInstance || st.tableInstance) : null;
  if (inst && typeof inst.getCellRelativeRect === 'function') { window.__vt = inst; break; }
  if (node.child) queue.push({node: node.child, depth: cur.depth + 1});
  if (node.sibling) queue.push({node: node.sibling, depth: cur.depth + 1});
  if (node.return) queue.push({node: node.return, depth: cur.depth + 1});
}
return window.__vt ? 'bound' : 'bind-failed';
"""
    print("绑定:", f.run_js(bind))
    api = json.loads(f.run_js(API_ENUM))
    print(f"\n=== VTable API 全集：{len(api['functions'])} 个方法 ===")
    interesting = [n for n in api["functions"] if any(
        k in n.lower() for k in (
            "cell", "scroll", "select", "icon", "record", "header", "freeze",
            "editor", "render", "resize", "hover", "highlight", "state", "range",
            "copy", "checked", "checkbox", "sort", "filter", "menu", "tooltip",
        )
    )]
    print("交互相关方法（分类关键词命中）:")
    for n in interesting:
        print(f"  {n}")
    print("\n其余方法:")
    rest = [n for n in api["functions"] if n not in interesting]
    print("  " + ", ".join(rest[:60]))

    # 2. 浮层位置模式
    layers = json.loads(f.run_js(FLOATING_LAYERS))
    print(f"\n=== 浮层清单（canvas {layers['canvasRect']}）===")
    for l in layers["layers"]:
        print(f"  {'NEAR' if l['nearCanvas'] else 'FAR '} {l['cls'][:50]:52} "
              f"rect={l['rect']} z={l['zIndex']} pos={l['position']} body={l['parentIsBody']}")

    # 3. 选区高亮实测定证：点击前 → 真实点击 → 点击后，对比 scenegraph 视觉签名
    col, row = 3, 5
    before = json.loads(f.run_js(CELL_STATE, col, row))
    print(f"\n=== 选区高亮定证 (col={col}, row={row}) ===")
    print("点击前 paints:", before.get("paints"))
    # 计算目标格视口坐标
    geo = f.run_js("""
var t = window.__vt;
var r = t.getCellRelativeRect(arguments[0], arguments[1]);
var cv = (t.canvas || document.querySelector('.vtable canvas')).getBoundingClientRect();
var fr = window.frameElement ? window.frameElement.getBoundingClientRect() : {left: 0, top: 0};
return JSON.stringify({x: fr.left + cv.x + (r.bounds.x1 + r.bounds.x2) / 2, y: fr.top + cv.y + (r.bounds.y1 + r.bounds.y2) / 2});
""", col, row)
    pt = json.loads(geo)
    print("目标格视口坐标:", pt)
    t.actions.move_to((pt["x"], pt["y"])).click()
    import time
    time.sleep(0.5)
    after = json.loads(f.run_js(CELL_STATE, col, row))
    print("点击后 paints:", after.get("paints"))
    same = before.get("signature") == after.get("signature")
    print("签名一致:", same, "（不一致=有高亮变化，可作断言依据）")


if __name__ == "__main__":
    main()
