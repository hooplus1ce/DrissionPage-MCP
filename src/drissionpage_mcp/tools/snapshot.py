"""页面控制物清单工具（省 token 版 ui_snapshot）。

单次 run_js 采集当前容器的可交互控件（按钮/输入框/下拉/链接），
总量封顶 40 项、文本截断 24 字符，一次调用即让 LLM 看清页面可用操作。
"""

from __future__ import annotations

import json

from fastmcp.exceptions import ToolError

from ..manager import manager
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Snapshot")

PAGE_CONTROLS_JS = r"""
var __pcap = 1;
var cap = 40;
var out = [];
var visible = function (el) {
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};
var clean = function (s) { return String(s || '').replace(/\s+/g, ' ').trim().slice(0, 24); };
var btns = document.querySelectorAll('button, [role="button"]');
var bc = 0;
for (var i = 0; i < btns.length && bc < 15 && out.length < cap; i++) {
  if (!visible(btns[i])) continue;
  var disabled = btns[i].disabled || String(btns[i].className).indexOf('disabled') >= 0;
  out.push({ kind: 'button', text: clean(btns[i].textContent), enabled: !disabled });
  bc++;
}
var ins = document.querySelectorAll('input:not([type=hidden]), textarea');
var ic = 0;
for (var i = 0; i < ins.length && ic < 12 && out.length < cap; i++) {
  if (!visible(ins[i])) continue;
  out.push({ kind: 'input', text: clean(ins[i].placeholder || ins[i].getAttribute('aria-label') || ins[i].name || ins[i].type), enabled: !ins[i].disabled });
  ic++;
}
var sels = document.querySelectorAll('.ant-select');
var sc = 0;
for (var i = 0; i < sels.length && sc < 8 && out.length < cap; i++) {
  if (!visible(sels[i])) continue;
  var selText = '';
  var selIn = sels[i].querySelector('.ant-select-selection-item, .ant-select-selection-selected-value');
  if (selIn) selText = clean(selIn.textContent || selIn.getAttribute('title'));
  out.push({ kind: 'select', text: selText, enabled: String(sels[i].className).indexOf('ant-select-enabled') >= 0 });
  sc++;
}
var links = document.querySelectorAll('a[href]');
var lc = 0;
for (var i = 0; i < links.length && lc < 5 && out.length < cap; i++) {
  if (!visible(links[i])) continue;
  var lt = clean(links[i].textContent);
  if (!lt) continue;
  out.push({ kind: 'link', text: lt, enabled: true });
  lc++;
}
var counts = { button: bc, input: ic, select: sc, link: lc };
return JSON.stringify({ counts: counts, controls: out, truncated: (bc + ic + sc + lc) >= cap });
"""


@mcp.tool(
    tags={"snapshot"},
    annotations={"title": "页面控制物清单", "readOnlyHint": True},
)
def page_controls(
    tab_id: str | None = None, frame: str | None = None
) -> dict:
    """一次性列出当前功能页面的可交互控件（按钮/输入框/下拉/链接，封顶 40 项，
    文本截断 24 字符）。用于首次进入页面时快速掌握可用操作，之后用
    find_element + element_click 精确交互。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        frame: 搜索范围：省略=激活态 iframe 优先（主文档兜底）；'main'=主文档；
            也可用序号或 iframe 的 id/name（见 frame_list）
    """
    tab, _ = manager.get_tab(tab_id)
    container = manager.resolve_frame(tab, frame) if frame else None
    if container is None:
        try:
            container = manager.resolve_frame(tab, "active")
        except ToolError:
            container = tab
    data = None
    for runner in (container, tab):
        try:
            raw = runner.run_js(PAGE_CONTROLS_JS)
            data = json.loads(raw)
            if data.get("controls"):
                break
        except Exception:
            continue
    if data is None:
        raise ToolError("控件清单采集失败")

    # 提取面包屑模块路径（主文档 .ant-breadcrumb 为准，防止凭 URL 臆测）
    try:
        bc_data = tab.run_js(
            r"""
            var container = document.querySelector('.ant-breadcrumb, [class*="breadcrumb"]');
            if (!container) return null;
            var links = container.querySelectorAll('.ant-breadcrumb-link');
            var items = [];
            if (links.length > 0) {
                for (var i = 0; i < links.length; i++) {
                    var t = links[i].innerText ? links[i].innerText.trim() : '';
                    if (t) items.push(t);
                }
            } else {
                var spans = container.querySelectorAll('span');
                for (var j = 0; j < spans.length; j++) {
                    var st = spans[j].innerText ? spans[j].innerText.trim() : '';
                    if (st && st !== '>' && st !== '/') items.push(st);
                }
            }
            return items.length > 0 ? items : null;
            """
        )
        if isinstance(bc_data, list):
            items = [str(x) for x in bc_data if str(x).strip()]
            if items:
                data["breadcrumb"] = items
                data["module_path"] = " > ".join(items)
    except Exception:
        pass

    return data
