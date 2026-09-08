"""VTable 绑定与坐标换算层（DrissionPage 版）。

坐标链（与参考实现一致，几何保持在 canvas 局部，偏移换算统一在本层完成）：
  单元格中心(canvas 局部, VTable API)
    + canvas 在 iframe 内的偏移(canvas.getBoundingClientRect())
    = iframe 内坐标
    + iframe 在页面视口中的偏移(iframe 元素 rect)
    = 页面视口绝对坐标 → 交给 action_chain 派发真实鼠标事件
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from fastmcp.exceptions import ToolError

from .manager import manager
from .overlays import drain_overlays
from .vtable_scripts import VTABLE_SCRIPTS

MAX_READ_CELLS = 2000


@dataclass
class VTableSession:
    """一次绑定后的会话状态：frame 容器与各层偏移。"""

    frame: object  # ChromiumFrame
    tab: object
    frame_offset: tuple[float, float] = (0.0, 0.0)  # iframe → 页面视口
    canvas_offset: tuple[float, float] = (0.0, 0.0)  # canvas → iframe
    meta: dict = field(default_factory=dict)

    def to_viewport(self, x: float, y: float) -> tuple[float, float]:
        """canvas 局部坐标 → 页面视口绝对坐标。"""
        return (
            x + self.canvas_offset[0] + self.frame_offset[0],
            y + self.canvas_offset[1] + self.frame_offset[1],
        )


def _run(frame, name: str, *args) -> dict:
    """执行命名 JS 片段并解析 JSON 结果。"""
    raw = frame.run_js(VTABLE_SCRIPTS[name], *args)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": str(raw)[:200]}


def _require_bound(data: dict, action: str) -> None:
    if not data.get("bound", False):
        raise ToolError(
            f"VTable 实例未绑定，无法{action}。请确认当前页面存在 VTable 表格"
            "（先调用 vtable_list 查看），或用 vtable_info 显式指定 table_index"
        )


def _refresh_offsets(session: VTableSession) -> None:
    """重取 canvas 与 iframe 的当前偏移（滚动/拖拽后位置会变）。"""
    meta = _run(session.frame, "table_meta")
    _require_bound(meta, "读取 VTable 元数据")
    canvas_box = meta.get("canvasBox") or {}
    session.canvas_offset = (float(canvas_box.get("x", 0)), float(canvas_box.get("y", 0)))
    loc = session.frame.rect.location  # iframe 元素在页面视口中的位置
    session.frame_offset = (float(loc[0]), float(loc[1]))
    session.meta = meta


def bind_vtable(tab_id: str | None, table_index: int | None) -> VTableSession:
    """定位激活 frame → 绑定 VTable 实例 → 建立会话。"""
    tab, _ = manager.get_tab(tab_id)
    if table_index is not None:
        try:
            tab.run_js("window.__vtable_target_index = arguments[0];", table_index)
        except Exception:
            pass
    else:
        try:
            tab.run_js("delete window.__vtable_target_index;")
        except Exception:
            pass
    frame = manager.resolve_frame(tab, "active")
    data = _run(frame, "bind")
    if not data.get("bound"):
        # frame 会话抖动：清缓存重建后重试一次
        fresh = manager._rebuild_frame(tab, frame, "active")
        if fresh is not None and fresh is not frame:
            frame = fresh
            data = _run(frame, "bind")
    if not data.get("bound"):
        raise ToolError(
            "未能在当前页面绑定 VTable 实例（React Fiber 扫描未命中）。"
            "请确认激活的功能模块页面中存在 VTable 表格，可用 vtable_list 查看"
        )
    session = VTableSession(frame=frame, tab=tab)
    _refresh_offsets(session)
    session.meta["bindSource"] = data.get("source")
    session.meta["instanceType"] = data.get("type")
    return session


def get_session(tab_id: str | None, table_index: int | None = None) -> VTableSession:
    """每次工具调用的入口：绑定 + 刷新偏移。"""
    session = bind_vtable(tab_id, table_index)
    return session


def cell_center(session: VTableSession, col: int, row: int) -> dict:
    """单元格几何 + 视口绝对中心点。"""
    data = _run(session.frame, "cell_geometry", col, row)
    if not data.get("found"):
        raise ToolError(f"VTable 单元格 ({col}, {row}) 不存在或无几何信息")
    cx, cy = data["center"]["x"], data["center"]["y"]
    vx, vy = session.to_viewport(cx, cy)
    return {
        "col": col,
        "row": row,
        "value": data.get("value"),
        "type": data.get("type"),
        "box_canvas": data.get("box"),
        "canvas_box": data.get("canvasBox"),
        "in_viewport": bool(data.get("inViewport")),
        "center_viewport": {"x": round(vx, 1), "y": round(vy, 1)},
    }


def _wheel_scroll(session: VTableSession, delta_y: float, delta_x: float) -> None:
    """在 canvas 中心派发真实鼠标滚轮事件（用户滚动表格的自然方式）。"""
    from .models import ActionStep
    from .tools.action import _run_step

    meta = session.meta or {}
    box = meta.get("canvasBox") or {}
    cx = session.to_viewport(
        float(box.get("x", 0)) + float(box.get("width", 800)) / 2,
        float(box.get("y", 0)) + float(box.get("height", 400)) / 2,
    )
    actions = session.tab.actions
    _run_step(actions, ActionStep(**{"action": "move_to", "x": cx[0], "y": cx[1]}))
    _run_step(
        actions,
        ActionStep(**{"action": "scroll", "delta_y": delta_y, "delta_x": delta_x}),
    )


def ensure_cell_visible(session: VTableSession, col: int, row: int, timeout: float = 8.0) -> bool:
    """用真实滚轮滚动使单元格进入视口；判定优先官方 cellIsInVisualView，
    缺省回退冻结补偿的几何判定。滚不动时退回 scrollToCell API 兜底。

    方向由目标中心与 canvas 视口的差值决定，每轮滚动 ~400px。
    """
    def _check() -> bool:
        vr = _run(session.frame, "visible_range", col, row)
        if vr.get("inVisualView") is not None:
            return bool(vr["inVisualView"])
        try:
            return bool(cell_center(session, col, row)["in_viewport"])
        except ToolError:
            return False

    if _check():
        return True
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            info = cell_center(session, col, row)
        except ToolError:
            break
        c = info["box_canvas"]
        canvas = info["canvas_box"] or {}
        cy = c["y"] + c["height"] / 2
        cx = c["x"] + c["width"] / 2
        delta_y = 0.0
        delta_x = 0.0
        if cy > canvas.get("height", 700):
            delta_y = min(400.0, cy - canvas.get("height", 700) + 60)
        elif cy < 0:
            delta_y = -min(400.0, 0 - cy + 60)
        if cx > canvas.get("width", 1600):
            delta_x = -min(400.0, cx - canvas.get("width", 1600) + 60)
        elif cx < 0:
            delta_x = min(400.0, 0 - cx + 60)
        if delta_y or delta_x:
            _wheel_scroll(session, delta_y, delta_x)
        else:
            # 已在 canvas 范围内但被冻结列遮挡等：API 精确滚动兜底
            _run(session.frame, "scroll_to_cell", col, row)
        time.sleep(0.35)
        _refresh_offsets(session)
        if _check():
            return True
    _run(session.frame, "scroll_to_cell", col, row)
    time.sleep(0.3)
    _refresh_offsets(session)
    return _check()


def cell_text_deep(session: VTableSession, col: int, row: int) -> dict:
    """场景图深度文本提取：显示值优先（scenegraph 渲染文本），多级回退。"""
    data = _run(session.frame, "cell_text_deep", col, row)
    if not data or data.get("bound") is False:
        raise ToolError("VTable 实例未绑定，无法读取单元格文本")
    if data.get("display") is None and data.get("source") == "none":
        raise ToolError(
            f"单元格 ({col}, {row}) 无可提取文本（场景图与各级 API 均为空）"
        )
    return data


def get_selection(session: VTableSession) -> dict:
    """当前选区的单元格明细（含业务记录 originData）——点击后断言的数据源。"""
    return _run(session.frame, "selection_info")


def cell_state(session: VTableSession, col: int, row: int) -> dict:
    """单元格状态：选中/复选/单选/开关 + scenegraph 视觉签名（供前后对比断言）。"""
    data = _run(session.frame, "cell_state", col, row)
    if not data or data.get("bound") is False:
        raise ToolError("VTable 实例未绑定，无法读取单元格状态")
    return data


def click_cell(
    session: VTableSession,
    col: int,
    row: int,
    double_click: bool = False,
    retry: bool = False,
    observe: bool = True,
) -> dict:
    """滚动到单元格 → 计算视口绝对坐标 → 真实鼠标点击。

    verified：点击后目标格是否进入选区（勾选/按钮类格子不改选区，
    verified=False 不代表失败）。retry=True 且未验证时自动重点一次。
    """
    if not ensure_cell_visible(session, col, row):
        raise ToolError(
            f"单元格 ({col}, {row}) 滚动后仍不在可视区域，请先检查表格滚动状态"
        )
    info = cell_center(session, col, row)
    x, y = info["center_viewport"]["x"], info["center_viewport"]["y"]
    steps = [{"action": "move_to", "x": x, "y": y}]
    if double_click:
        steps += [{"action": "click", "times": 2}]
    else:
        steps += [{"action": "click"}]
    from .tools.action import _run_step

    actions = session.tab.actions
    for step in steps:
        from .models import ActionStep

        _run_step(actions, ActionStep(**step))
    time.sleep(0.3)
    try:
        selection = get_selection(session)
    except Exception:
        selection = None
    cells = (selection or {}).get("cells") or []
    verified = any(c.get("col") == col and c.get("row") == row for c in cells)
    retries_used = 0
    if not verified and retry and not double_click:
        for step in steps:
            _run_step(actions, ActionStep(**step))
        retries_used = 1
        time.sleep(0.3)
        try:
            selection = get_selection(session)
        except Exception:
            selection = None
        cells = (selection or {}).get("cells") or []
        verified = any(c.get("col") == col and c.get("row") == row for c in cells)
    result = {
        "clicked": {"x": x, "y": y},
        "cell": info,
        "selection": selection,
        "verified": verified,
        "retries_used": retries_used,
    }
    if observe:
        time.sleep(0.3)
        overlays = drain_overlays(session.frame)
        if overlays:
            result["overlays"] = overlays
    return result


def click_icon(
    session: VTableSession, col: int, row: int, name: str | None = None, index: int = 1
) -> dict:
    """点击单元格/表头内的交互图标（排序/筛选/复选等），坐标来自 scenegraph。"""
    data = _run(session.frame, "cell_icons", col, row)
    icons = data.get("icons") or []
    if not icons:
        raise ToolError(f"单元格 ({col}, {row}) 内未发现可交互图标")
    target = None
    if name:
        for icon in icons:
            if name.lower() in str(icon.get("name", "")).lower() or name.lower() == str(
                icon.get("function", "")
            ):
                target = icon
                break
    if target is None:
        try:
            target = icons[index - 1]
        except IndexError:
            raise ToolError(
                f"图标序号 {index} 超出范围，共 {len(icons)} 个: "
                f"{[i.get('name') for i in icons]}"
            ) from None
    x, y = session.to_viewport(target["center"]["x"], target["center"]["y"])
    from .models import ActionStep
    from .tools.action import _run_step

    actions = session.tab.actions
    for step in (
        {"action": "move_to", "x": x, "y": y},
        {"action": "click"},
    ):
        _run_step(actions, ActionStep(**step))
    result = {"clicked": {"x": round(x, 1), "y": round(y, 1)}, "icon": target}
    time.sleep(0.35)
    overlays = drain_overlays(session.frame)
    if overlays:
        result["overlays"] = overlays
    return result


def drag_scrollbar(
    session: VTableSession, direction: str = "vertical", distance_px: float = 200
) -> dict:
    """拖拽 VTable 滚动条滑块（真实鼠标：按下→移动→释放）。

    滑块几何由确定性计算得出（滚动条是 canvas 绘制，不在 DOM/scenegraph）：
    条宽 14px 贴 canvas 右缘/底缘，滑块位置 = 滚动比例 × (轨道长 - 滑块长)。
    """
    geo = _run(session.frame, "scrollbar_geometry")
    if not geo.get("bound", True):
        raise ToolError("VTable 实例未绑定")
    bar = geo.get("vertical" if direction == "vertical" else "horizontal") or {}
    if not bar.get("scrollable"):
        raise ToolError(
            f"{direction} 方向内容未超出视口，无需滚动（可先检查表格数据量）"
        )
    thumb = bar["thumbCenter"]
    x, y = session.to_viewport(thumb["x"], thumb["y"])
    dx = distance_px if direction == "horizontal" else 0
    dy = distance_px if direction == "vertical" else 0
    from .models import ActionStep
    from .tools.action import _run_step

    actions = session.tab.actions
    for step in (
        {"action": "move_to", "x": x, "y": y},
        {"action": "hold"},
        {"action": "move", "offset_x": dx, "offset_y": dy, "duration": 0.5},
        {"action": "release"},
    ):
        _run_step(actions, ActionStep(**step))
    time.sleep(0.4)
    after = _run(session.frame, "scrollbar_geometry")
    return {
        "dragged": {"x": x, "y": y, "dx": dx, "dy": dy},
        "scrollBefore": geo.get("scroll"),
        "scrollAfter": after.get("scroll"),
    }


def hover_cell(session: VTableSession, col: int, row: int) -> dict:
    """真实悬停在单元格上（移动鼠标至中心），读取该格及同行邻格的颜色态
    （hover 高亮/行列高亮会反映在 scenegraph 填充上）。"""
    if not ensure_cell_visible(session, col, row):
        raise ToolError(f"单元格 ({col}, {row}) 无法滚动至可视区域")
    info = cell_center(session, col, row)
    x, y = info["center_viewport"]["x"], info["center_viewport"]["y"]
    from .models import ActionStep
    from .tools.action import _run_step

    _run_step(session.tab.actions, ActionStep(**{"action": "move_to", "x": x, "y": y}))
    time.sleep(0.6)
    colors = _run(session.frame, "cell_colors", col, row)
    if colors.get("error"):
        raise ToolError(f"读取单元格颜色失败: {colors['error']}")
    neighbor = _run(session.frame, "cell_colors", col + 1, row)
    return {
        "cell": info,
        "colors": colors,
        "rowNeighborColors": neighbor.get("error") and None or {
            "background": neighbor.get("background"),
            "textColors": neighbor.get("textColors"),
        },
    }


def _resolve_col_index(session: VTableSession, col_name: str) -> int:
    """按 field 或 title 智能解析列名，完全匹配优先，不区分大小写包含匹配回退。"""
    headers_data = _run(session.frame, "headers")
    columns = headers_data.get("columns") or []
    if not columns:
        raise ToolError("无法解析列名：表格未返回任何列头信息")

    col_clean = col_name.strip()
    col_lower = col_clean.lower()

    # 1. 完全匹配优先 (field 或 title)
    for col_info in columns:
        c_field = str(col_info.get("field") or "").strip()
        c_title = str(col_info.get("title") or "").strip()
        if col_clean == c_title or col_clean == c_field:
            return int(col_info["col"])

    # 2. 忽略大小写完全匹配
    for col_info in columns:
        c_field = str(col_info.get("field") or "").strip().lower()
        c_title = str(col_info.get("title") or "").strip().lower()
        if col_lower == c_title or col_lower == c_field:
            return int(col_info["col"])

    # 3. 包含匹配回退
    for col_info in columns:
        c_field = str(col_info.get("field") or "").strip().lower()
        c_title = str(col_info.get("title") or "").strip().lower()
        if col_lower in c_title or col_lower in c_field:
            return int(col_info["col"])

    available = [
        f"{c.get('title') or c.get('field')}(col={c.get('col')})"
        for c in columns
        if c.get("title") or c.get("field")
    ]
    raise ToolError(
        f"未找到匹配的列 '{col_name}'。可用列清单: {', '.join(available)}"
    )


def _transform_inspect_result(data: dict, session: VTableSession) -> dict:
    """将 canvas 局部坐标转换为视口绝对坐标，并为 bounds 添加 viewport 映射。"""
    def _pt(p: dict | None) -> dict | None:
        if isinstance(p, dict) and "x" in p and "y" in p and p["x"] is not None and p["y"] is not None:
            vx, vy = session.to_viewport(float(p["x"]), float(p["y"]))
            return {"x": round(vx, 1), "y": round(vy, 1)}
        return p

    def _box(b: dict | None) -> dict | None:
        if isinstance(b, dict) and "x" in b and "y" in b and b["x"] is not None and b["y"] is not None:
            vx, vy = session.to_viewport(float(b["x"]), float(b["y"]))
            res = dict(b)
            res["viewport_x"] = round(vx, 1)
            res["viewport_y"] = round(vy, 1)
            return res
        return b

    def _cell(c: dict) -> dict:
        if "center" in c:
            c["center"] = _pt(c.get("center"))
        if "blank_point" in c:
            c["blank_point"] = _pt(c.get("blank_point"))
        if "bounds" in c:
            c["bounds"] = _box(c.get("bounds"))
        if "icons" in c and isinstance(c["icons"], list):
            c["icons"] = [_icon(ic) for ic in c["icons"]]
        return c

    def _icon(ic: dict) -> dict:
        if "center" in ic:
            ic["center"] = _pt(ic.get("center"))
        if "box" in ic:
            ic["box"] = _box(ic.get("box"))
        return ic

    for k in ("center", "blank_point", "border_right", "header_center", "drag_start", "drag_end"):
        if k in data:
            data[k] = _pt(data.get(k))
    if "bounds" in data:
        data["bounds"] = _box(data.get("bounds"))
    if "icons" in data and isinstance(data["icons"], list):
        data["icons"] = [_icon(ic) for ic in data["icons"]]
    if "header_icons" in data and isinstance(data["header_icons"], list):
        data["header_icons"] = [_icon(ic) for ic in data["header_icons"]]
    if "cells" in data and isinstance(data["cells"], list):
        if data["cells"] and isinstance(data["cells"][0], list):
            data["cells"] = [[_cell(c) for c in row] for row in data["cells"]]
        else:
            data["cells"] = [_cell(c) for c in data["cells"]]
    return data


def inspect_vtable(
    session: VTableSession,
    col: int | str | None = None,
    row: int | None = None,
    col_range: list[int] | None = None,
    row_range: list[int] | None = None,
) -> dict:
    """VTable 可视化多粒度快照与交互锚点计算。"""
    target_col_idx = None
    if isinstance(col, str):
        target_col_idx = _resolve_col_index(session, col)
    elif col is not None:
        target_col_idx = int(col)

    data = _run(session.frame, "inspect", target_col_idx, row, col_range, row_range)
    if not data or data.get("bound") is False:
        _require_bound(data, "感知 VTable 状态")

    return _transform_inspect_result(data, session)
