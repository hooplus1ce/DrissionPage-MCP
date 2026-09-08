"""轻量浮层观察器（DrissionPage 版 overlay 观察）。

设计约束：**省上下文 token**。
- 观察（arm）与收集（drain）都是一次 run_js，注入幂等；
- 只捕捉已知浮层家族（AntD portal / VTable 菜单）的新增节点；
- 收集结果封顶（默认 4 条 × 60 字符，去重），**空结果整体省略**；
- 用法：动作前 arm → 动作 → 短暂等待 → drain，drain 后缓冲清零。
"""

from __future__ import annotations

import json
import time

ARM_JS = r"""
if (!window.__ovlObs) {
  window.__ovlBuf = [];
  var KNOWN = /(ant-modal|ant-message|ant-notification|ant-dropdown|ant-select-dropdown|ant-popover|ant-tooltip|ant-drawer|ant-picker-dropdown|ant-calendar|vtable__menu|vtable-filter-menu)/;
  var classify = function (el) {
    var cls = String(el.className || '');
    var m = cls.match(KNOWN);
    if (!m || cls.indexOf('hidden') >= 0) return null;
    return m[1];
  };
  var obs = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var added = muts[i].addedNodes || [];
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        var kind = classify(n);
        if (kind && window.__ovlBuf.length < 30) {
          window.__ovlBuf.push({ k: kind, t: String(n.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60) });
        }
        var kids = n.children || [];
        for (var k = 0; k < kids.length && k < 8; k++) {
          var k2 = classify(kids[k]);
          if (k2 && window.__ovlBuf.length < 30) {
            window.__ovlBuf.push({ k: k2, t: String(kids[k].textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60) });
          }
        }
      }
    }
  });
  obs.observe(document.body, { childList: true, subtree: true });
  window.__ovlObs = obs;
}
window.__ovlArmAt = Date.now();
return 'armed';
"""

DRAIN_JS = r"""
var buf = window.__ovlBuf || [];
window.__ovlBuf = [];
var seen = {};
var out = [];
for (var i = 0; i < buf.length && out.length < 10; i++) {
  var key = buf[i].k + '|' + buf[i].t;
  if (seen[key]) continue;
  seen[key] = 1;
  out.push(buf[i]);
}
return JSON.stringify(out);
"""

ARM_AT_JS = "return window.__ovlArmAt || 0;"


def arm_overlays(container) -> None:
    """在容器文档中注入/确认浮层观察器（幂等，失败静默）。"""
    try:
        container.run_js(ARM_JS)
    except Exception:
        pass


def drain_overlays(container, limit: int = 4) -> list[dict] | None:
    """收集并清空缓冲的浮层记录；空结果返回 None（响应中整体省略）。"""
    try:
        raw = container.run_js(DRAIN_JS)
        items = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(items, list):
        return None
    out = []
    seen = set()
    for item in items:
        key = (item.get("k"), item.get("t"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": item.get("k"), "text": (item.get("t") or "")[:60]})
        if len(out) >= limit:
            break
    return out or None


def observed(container, action, settle: float = 0.45, limit: int = 4) -> list[dict] | None:
    """把动作包进 观察→执行→收集 的流水线，返回紧凑浮层列表（可为 None）。"""
    arm_overlays(container)
    result = action()
    time.sleep(settle)
    overlays = drain_overlays(container, limit)
    return result, overlays
