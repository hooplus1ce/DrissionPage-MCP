"""基于 DrissionPage Actions 的真实鼠标键盘交互工具。

所有操作通过 CDP Input 事件派发真实鼠标移动/点击/键盘输入，
适合 UI 自动化功能测试（触发真实 hover/焦点/事件链）。
"""

from __future__ import annotations

import random

from fastmcp.exceptions import ToolError

from ..manager import manager
from ..models import ActionChainResult, ActionStep, MessageResult
from ..server import mcp

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


def _run_step(actions, step: ActionStep) -> None:
    act = step.action
    ele = manager.get_element(step.element_id) if step.element_id else None

    if act == "move_to":
        if ele is not None:
            actions.move_to(ele, offset_x=step.offset_x, offset_y=step.offset_y,
                            duration=step.duration if step.duration is not None else 0.5)
        elif step.x is not None and step.y is not None:
            actions.move_to((step.x, step.y), duration=step.duration if step.duration is not None else 0.5)
        else:
            raise ToolError("move_to 需要 element_id 或 x/y 坐标")
    elif act == "move":
        actions.move(step.offset_x or 0, step.offset_y or 0,
                     duration=step.duration if step.duration is not None else 0.5)
    elif act in ("click", "r_click", "m_click"):
        fn = getattr(actions, act)
        if ele is not None:
            fn(ele, times=step.times or 1)
        else:
            fn(times=step.times or 1)
    elif act == "hold":
        actions.hold(ele)
    elif act == "release":
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
    """按顺序执行一串真实鼠标/键盘操作（CDP Input 事件级别，非 JS 模拟）。

    典型用法：先 move_to 元素再 click；或 hold+move+release 拖拽；
    type 输入文本前先 move_to/click 输入框获得焦点。

    Args:
        tab_id: 标签页 id，省略时用最新标签页
        steps: 操作步骤列表，每步含 action 及所需参数，按顺序执行：
            - move_to: element_id 或 x/y 坐标，可带 offset_x/offset_y/duration
            - move: 相对移动 offset_x/offset_y
            - click / r_click / m_click: 可选 element_id 与 times
            - hold / release: 按下/松开鼠标（配合 move 实现拖拽）
            - scroll: delta_y/delta_x，可带 element_id
            - type: 输入文本 text（可带 interval 按键间隔秒数；省略时自动拟人节奏 30~90ms）
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
