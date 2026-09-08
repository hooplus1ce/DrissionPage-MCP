"""测试 Windows 11 Dark HD 虚拟光标与平滑轨迹模块。"""

from __future__ import annotations

import pytest

from drissionpage_mcp.cursor import (
    CURSOR_HELPER_JS,
    _CURSOR_DATA_URL,
    act_cursor,
    ensure_cursor_installed,
    glide_cursor,
    hide_cursor,
    is_cursor_enabled,
    set_cursor_enabled,
)
from conftest import FakeTab


def test_cursor_env_and_override(monkeypatch):
    """测试环境变量 SHOW_CURSOR 与动态覆盖控制。"""
    set_cursor_enabled(None)  # 恢复读取环境

    monkeypatch.setenv("SHOW_CURSOR", "true")
    assert is_cursor_enabled() is True

    monkeypatch.setenv("SHOW_CURSOR", "1")
    assert is_cursor_enabled() is True

    monkeypatch.setenv("SHOW_CURSOR", "false")
    assert is_cursor_enabled() is False

    monkeypatch.setenv("SHOW_CURSOR", "0")
    assert is_cursor_enabled() is False

    # 动态覆盖测试
    set_cursor_enabled(True)
    assert is_cursor_enabled() is True
    set_cursor_enabled(False)
    assert is_cursor_enabled() is False

    set_cursor_enabled(None)  # 还原


def test_cursor_script_invariants():
    """断言光标脚本满足 4 大核心关键设计：
    1. 60FPS 与三次减速缓动插值
    2. 全局穿透与顶层悬浮 (z-index: 2147483647, pointer-events: none)
    3. 生命周期定时器 (hideTimer / safetyTimer)
    4. 全局连贯坐标记忆 (window.__dp_last_x, window.__dp_last_y)
    """
    assert "2147483647 !important" in CURSOR_HELPER_JS
    assert "pointer-events: none !important" in CURSOR_HELPER_JS
    assert "requestAnimationFrame(tick)" in CURSOR_HELPER_JS
    assert "1 - Math.pow(1 - progress, 3)" in CURSOR_HELPER_JS
    assert "window.__dp_last_x" in CURSOR_HELPER_JS
    assert "window.__dp_last_y" in CURSOR_HELPER_JS
    assert "hideTimer" in CURSOR_HELPER_JS
    assert "safetyTimer" in CURSOR_HELPER_JS
    assert "data:image/png;base64," in _CURSOR_DATA_URL


def test_cursor_tab_helpers():
    """测试 Python 辅助函数在启用与禁用时的调用行为。"""
    tab = FakeTab("test-tab")
    tab.steps.clear()

    # 1. 禁用状态下不触发 JS 执行
    set_cursor_enabled(False)
    assert ensure_cursor_installed(tab) is False
    glide_cursor(tab, 100, 200)
    act_cursor(tab, "click")
    assert len(tab.steps) == 0

    # 2. 启用状态下正确下发 JS
    set_cursor_enabled(True)
    assert ensure_cursor_installed(tab) is True
    assert any("CURSOR_HELPER" in str(s) or "installed" in str(s) or "__dp_cursor" in str(s) for s in tab.steps)

    tab.steps.clear()
    glide_cursor(tab, 350, 450, 300)
    assert any("__dp_cursor_glide" in str(s) for s in tab.steps)

    tab.steps.clear()
    act_cursor(tab, "click", 350, 450)
    assert any("__dp_cursor_act" in str(s) for s in tab.steps)

    tab.steps.clear()
    hide_cursor(tab)
    assert any("__dp_cursor_hide" in str(s) for s in tab.steps)

    set_cursor_enabled(None)  # 还原
