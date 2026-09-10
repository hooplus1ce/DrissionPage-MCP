"""基于 DrissionPage Actions 的真实鼠标键盘交互工具。

所有操作通过 CDP Input 事件派发真实鼠标移动/点击/键盘输入，
适合 UI 自动化功能测试（触发真实 hover/焦点/事件链）。
"""

from __future__ import annotations

import random
import time

from fastmcp.exceptions import ToolError

from ..cursor import act_cursor, glide_cursor
from ..manager import manager, vp_to_page
from ..models import ActionChainResult, ActionStep, MessageResult
from fastmcp import FastMCP
# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Actions")

MAX_STEPS = 30

# 常用键名别名 → DrissionPage Keys 类的合法名称
KEY_ALIASES = {
    "ESC": "ESCAPE",
    "BACK_SPACE": "BACKSPACE",
    "CMD": "COMMAND",
    "INS": "INSERT",
    "RETURN": "ENTER",
    "SPACEBAR": "SPACE",
}


def _normalize_key(key: str) -> str:
    return KEY_ALIASES.get(key.strip().upper(), key.strip().upper())


def _resolve_tab(actions):
    """从 Actions 对象反查顶层 Tab（假对象无 owner 时返回 None）。"""
    owner = getattr(actions, "owner", None)
    if owner is None:
        return None
    return getattr(owner, "tab", owner)


def _page_point(actions, x: float, y: float) -> tuple[float, float]:
    """ActionStep 的 x/y 是顶层视口坐标，Actions.move_to(元组) 需要页面坐标。"""
    tab = _resolve_tab(actions)
    if tab is None:
        return (x, y)
    return vp_to_page(tab, x, y)


def _run_step(actions, step: ActionStep) -> None:
    act = step.action
    ele = manager.get_element(step.element_id) if step.element_id else None

    if act == "move_to":
        duration = step.duration if step.duration is not None else 0.5
        duration_ms = int(duration * 1000)
        if ele is not None:
            try:
                mid_point = (step.offset_x is None and step.offset_y is None)
                loc = ele.rect.viewport_midpoint if mid_point else ele.rect.viewport_location
                glide_cursor(actions, loc[0] + (step.offset_x or 0), loc[1] + (step.offset_y or 0), duration_ms)
            except Exception:
                pass
            actions.move_to(ele, offset_x=step.offset_x, offset_y=step.offset_y, duration=duration)
        elif step.x is not None and step.y is not None:
            glide_cursor(actions, step.x, step.y, duration_ms)
            actions.move_to(_page_point(actions, step.x, step.y), duration=duration)
        else:
            raise ToolError("move_to 需要 element_id 或 x/y 坐标")
    elif act == "move":
        duration = step.duration if step.duration is not None else 0.5
        duration_ms = int(duration * 1000)
        curr_x = getattr(actions, "curr_x", 0) + (step.offset_x or 0)
        curr_y = getattr(actions, "curr_y", 0) + (step.offset_y or 0)
        glide_cursor(actions, curr_x, curr_y, duration_ms)
        actions.move(step.offset_x or 0, step.offset_y or 0, duration=duration)
    elif act in ("click", "r_click", "m_click"):
        if ele is not None:
            try:
                pt = getattr(ele.rect, "viewport_midpoint", None) or getattr(ele.rect, "midpoint", None)
                if pt:
                    glide_cursor(actions, pt[0], pt[1], 150)
                    time.sleep(0.05)
            except Exception:
                pass
        act_cursor(actions, "click")
        fn = getattr(actions, act)
        if ele is not None:
            fn(ele, times=step.times or 1)
        else:
            fn(times=step.times or 1)
    elif act == "hold":
        act_cursor(actions, "down")
        actions.hold(ele)
    elif act == "release":
        act_cursor(actions, "up")
        actions.release(ele)
    elif act == "scroll":
        actions.scroll(step.delta_y or 0, step.delta_x or 0, on_ele=ele)
    elif act == "type":
        if step.text is None:
            raise ToolError("type 需要 text 参数")
        # 拟人键入：未显式给 interval 时用 30~90ms 随机键间隔（零输出成本）
        interval = step.interval if step.interval is not None else random.uniform(0.03, 0.09)
        actions.type(step.text, interval=interval)
    elif act in ("key_down", "key_up"):
        if not step.key:
            raise ToolError(f"{act} 需要 key 参数，如 ENTER / ESC / TAB / CTRL / SHIFT")
        getattr(actions, act)(_normalize_key(step.key))
    elif act == "wait":
        actions.wait(step.seconds if step.seconds is not None else 1)
    else:
        raise ToolError(
            f"未知操作: {act!r}。支持: move_to, move, click, r_click, m_click, "
            "hold, release, scroll, type, key_down, key_up, wait"
        )


@mcp.tool(
    tags={"action", "interaction"},
    annotations={"title": "动作链(真实鼠标键盘)", "readOnlyHint": False},
)
def action_chain(tab_id: str | None = None, steps: list[ActionStep] | None = None) -> ActionChainResult:
    """按顺序执行一串真实鼠标/键盘操作（CDP Input 事件级别，非 JS 模拟），最多 30 步。

    典型用法：move_to 元素再 click；hold+move+release 拖拽；type 前先点击输入框聚焦。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        steps: 操作步骤列表，每步含 action 及所需参数，按顺序执行：
            - move_to: element_id 或 x/y 坐标（视口绝对坐标，页面滚动时无需自行加偏移），
              可带 offset_x/offset_y/duration
            - move: 相对移动 offset_x/offset_y
            - click / r_click / m_click: 可选 element_id 与 times
            - hold / release: 按下/松开鼠标（配合 move 实现拖拽）
            - scroll: delta_y/delta_x，可带 element_id
            - type: 输入文本 text（省略 interval 时自动拟人节奏 30~90ms）
            - key_down / key_up: key 如 ENTER、ESC、TAB、CTRL、SHIFT、BACKSPACE
            - wait: seconds 等待秒数
    """
    if not steps:
        raise ToolError("steps 不能为空")
    if len(steps) > MAX_STEPS:
        raise ToolError(f"单条动作链最多 {MAX_STEPS} 步，复杂流程请拆分多次调用")
    tab, _ = manager.get_tab(tab_id)
    actions = tab.actions
    executed: list[str] = []
    for i, step in enumerate(steps, start=1):
        try:
            _run_step(actions, step)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"动作链第 {i} 步 ({step.action}) 执行失败: {e}") from e
        executed.append(step.action)
    return ActionChainResult(ok=True, steps_executed=len(executed), actions=executed)


@mcp.tool(
    tags={"action", "interaction"},
    annotations={"title": "按键", "readOnlyHint": False},
)
def press_key(key: str, tab_id: str | None = None) -> MessageResult:
    """按下并松开一个键盘按键（真实键盘事件），如 ENTER、ESC、TAB、BACKSPACE、DELETE、箭头键。

    Args:
        key: 按键名，如 ENTER / ESC / TAB / BACKSPACE / DELETE / DOWN / UP / LEFT / RIGHT
        tab_id: 标签页 id，省略时用最新标签页
    """
    tab, _ = manager.get_tab(tab_id)
    real = _normalize_key(key)
    try:
        tab.actions.key_down(real).key_up(real)
    except ValueError as e:
        raise ToolError(f"无效按键名 {key!r}：{e}。常用按键: ENTER/ESCAPE/TAB/BACKSPACE/DELETE/CTRL/SHIFT/ALT/SPACE/方向键") from e
    return MessageResult(ok=True, message=f"已按键 {key}")
