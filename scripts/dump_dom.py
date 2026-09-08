"""APS 页面全量 DOM 快照与前端组件框架分析。

输出：
- dom_snapshot/page.html          激活 iframe 的完整 HTML
- dom_snapshot/top_page.html      顶层文档完整 HTML
- dom_snapshot/analysis.json      组件类名统计、控件清单、UI 库版本特征
"""

import json
import time
from pathlib import Path

from DrissionPage import Chromium

OUT = Path(__file__).parent.parent / "dom_snapshot"
OUT.mkdir(exist_ok=True)


def main() -> None:
    b = Chromium("127.0.0.1:9222")
    t = b.latest_tab
    f = None
    for fr in t.eles("tag:iframe"):
        try:
            if fr.states.is_displayed:
                f = fr
                break
        except Exception:
            continue
    if f is None:
        print("未找到激活 iframe")
        return

    print(f"目标 iframe: {f.attr('src')}")

    # ---------- 1. 全量 HTML ----------
    html = f.run_js("return document.documentElement.outerHTML")
    (OUT / "page.html").write_text(html, encoding="utf-8")
    print(f"iframe HTML: {len(html) / 1024:.0f} KB -> page.html")

    top_html = t.run_js("return document.documentElement.outerHTML")
    (OUT / "top_page.html").write_text(top_html, encoding="utf-8")
    print(f"顶层 HTML: {len(top_html) / 1024:.0f} KB -> top_page.html")

    # ---------- 2. 结构化分析（JS 独立文件，避免内嵌转义问题） ----------
    js = (Path(__file__).parent / "dump_dom_analysis.js").read_text(encoding="utf-8")
    analysis = f.run_js(js)
    data = json.loads(analysis)
    (OUT / "analysis.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- 3. 摘要打印 ----------
    print(f"\n元素总数: {data['totalElements']} | 类名种类: {data['classTokenCount']}")
    print(f"AntD 组件类: {', '.join(data['antComponents'])}")
    print(f"legions 自定义类: {len(data['legionsClasses'])} 个 -> {', '.join(c for c, _ in data['legionsClasses'][:15])}")
    print(f"\n表单控件: {data['inputs']}")
    print(f"\nUI 库: react={data['libs']['reactVersion']} antd={data['libs']['antdVersion']}")
    for s in data["libs"]["scripts"][:8]:
        print(f"  script: {s[-70:]}")
    print(f"\n表格: {data['tables']}")
    print(f"\n弹窗: {data['modals']}")
    print(f"\n下拉浮层: {data['dropdowns']}")
    print(f"\nbody 直接子节点:")
    for c in data["bodyChildren"]:
        print(f"  <{c['tag']}> id={c['id']} cls={c['cls']}")
    print(f"\n表单字段明细:")
    for c in data["formItems"]:
        print(f"  [{c['i']}] {c['label']} -> {c['kind']} (enabled={c['enabled']})")
    print("\n完整数据已写入 dom_snapshot/analysis.json")


if __name__ == "__main__":
    main()
