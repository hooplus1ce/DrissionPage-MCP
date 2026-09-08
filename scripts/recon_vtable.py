"""侦察 APS 页面 VTable 实例的可达性与 API 面（只读）。"""

from DrissionPage import Chromium

BIND_PROBE = """
var out = {containers: 0, visible: 0, nativeBind: false, canvasBind: false, fiberBind: false, instanceType: null};
var roots = document.querySelectorAll('.vtable');
out.containers = roots.length;
var el = null;
for (var i = 0; i < roots.length; i++) {
  var r = roots[i].getBoundingClientRect();
  if (r.width > 0 && r.height > 0) { el = roots[i]; out.visible++; }
}
if (el) {
  out.elClass = el.className.slice(0, 60);
  var canvas = el.querySelector('canvas');
  var native = el.__vtable__ || (canvas && canvas.__vtable__);
  if (native) { out.nativeBind = true; out.instanceType = native.constructor ? native.constructor.name : 'unknown'; }
  var fk = null;
  var probe = el.parentElement || el;
  var keys = Object.keys(probe);
  for (var i = 0; i < keys.length; i++) {
    if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternalInstance') === 0) { fk = keys[i]; break; }
  }
  if (fk) { out.fiberKey = fk.slice(0, 30); }
  window.__probe_el = el;
}
return JSON.stringify(out);
"""

FIBER_FIND = """
var el = window.__probe_el;
var fk = null;
var probe = el.parentElement || el;
var keys = Object.keys(probe);
for (var i = 0; i < keys.length; i++) {
  if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternalInstance') === 0) { fk = keys[i]; break; }
}
if (!fk) return 'no-fiber-key';
var queue = [{node: probe[fk], path: 'root', depth: 0}];
var seen = new Set();
var visited = 0;
while (queue.length && visited < 3000) {
  var cur = queue.shift();
  var node = cur.node;
  if (!node || seen.has(node)) continue;
  seen.add(node); visited++;
  var st = node.stateNode;
  if (st) {
    var inst = st.vtableInstance || st.tableInstance || st.table;
    if (inst && typeof inst.getCellRelativeRect === 'function') {
      window.__vt = inst;
      return 'FOUND:' + cur.path + ' depth=' + cur.depth + ' type=' + (inst.constructor ? inst.constructor.name : '?');
    }
  }
  if (cur.depth < 25) {
    if (node.child) queue.push({node: node.child, path: cur.path + '.child', depth: cur.depth + 1});
    if (node.sibling) queue.push({node: node.sibling, path: cur.path + '.sibling', depth: cur.depth + 1});
    if (node.return) queue.push({node: node.return, path: cur.path + '.return', depth: cur.depth + 1});
  }
}
return 'NOT-FOUND visited=' + visited;
"""

API_PROBE = """
var t = window.__vt;
if (!t) return JSON.stringify({error: 'not-bound'});
var api = ['getCellRelativeRect','getCellValue','getCellType','scenegraph','scrollToCell',
  'getCellAddrByFieldRecord','getCellIcons','frozenColCount','frozenRowCount','rowCount','colCount',
  'headerRowCount','canvas','getHeaderField','getBodyField','getCellRawRecord','setScrollLeft',
  'editorManager','selectCells','getSelectedCellRanges','getCopyValue','getBodyColumnDefine','render'];
var have = {};
for (var i = 0; i < api.length; i++) {
  var k = api[i];
  have[k] = typeof t[k] !== 'undefined';
}
var geom = null;
try {
  var r = t.getCellRelativeRect(0, 0);
  geom = r ? Object.keys(r) : null;
} catch (e) { geom = ['ERR:' + e]; }
var sample = null;
try { sample = {c0r0: String(t.getCellValue(0, 0)).slice(0, 20), rowCount: t.rowCount, colCount: t.colCount, headerRowCount: t.headerRowCount, frozenCol: t.frozenColCount, frozenRow: t.frozenRowCount}; } catch (e) { sample = String(e); }
var canvasRect = null;
try { var cv = t.canvas || document.querySelector('.vtable canvas'); var cr = cv.getBoundingClientRect(); canvasRect = {x: cr.x, y: cr.y, w: cr.width, h: cr.height}; } catch (e) { canvasRect = String(e); }
return JSON.stringify({have: have, geomKeys: geom, sample: sample, canvasRect: canvasRect, type: t.constructor ? t.constructor.name : '?'});
"""


def main() -> None:
    b = Chromium("127.0.0.1:9222")
    t = b.latest_tab
    f = None
    for fr in t.eles("tag:iframe"):
        if fr.states.is_displayed:
            f = fr
            break
    if f is None:
        print("无激活 iframe（可能当前页面无模块）")
        return
    print("frame:", str(f.attr("src"))[:80])
    print("1. 容器探测:", f.run_js(BIND_PROBE))
    r = f.run_js(FIBER_FIND)
    print("2. Fiber 寻找:", r)
    if "FOUND" in str(r):
        print("3. API 面:", f.run_js(API_PROBE))


if __name__ == "__main__":
    main()
