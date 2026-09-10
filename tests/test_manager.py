"""BrowserManager 单元测试（不经过 MCP 层）。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from drissionpage_mcp.manager import (
    BrowserManager,
    _rect_center_in_page,
    page_scroll,
    vp_to_page,
)

from conftest import FakeChromium, FakeElement, FakeFrame, FakeTab, BrowserSession, make_session


def test_get_session_none_raises(fresh_manager):
    with pytest.raises(ToolError, match="没有可用的浏览器"):
        fresh_manager.get_session(None)


def test_get_session_single_resolves(fresh_manager):
    session, _ = make_session()
    fresh_manager._sessions[session.browser_id] = session
    assert fresh_manager.get_session(None) is session


def test_get_session_ambiguous_requires_id(fresh_manager):
    s1, _ = make_session()
    s2, _ = make_session()
    fresh_manager._sessions[s1.browser_id] = s1
    fresh_manager._sessions[s2.browser_id] = s2
    with pytest.raises(ToolError, match="browser_id"):
        fresh_manager.get_session(None)
    assert fresh_manager.get_session(s2.browser_id) is s2


def test_get_tab_by_id_across_sessions(fresh_manager):
    s1, c1 = make_session()
    s2, c2 = make_session()
    tab1 = c1.new_tab()
    tab2 = c2.new_tab()
    fresh_manager._sessions[s1.browser_id] = s1
    fresh_manager._sessions[s2.browser_id] = s2
    found, session = fresh_manager.get_tab(tab2.tab_id)
    assert found is tab2
    assert session is s2


def test_get_tab_latest_fallback(fresh_manager):
    session, chromium = make_session()
    fresh_manager._sessions[session.browser_id] = session
    tab, resolved = fresh_manager.get_tab(None)
    assert resolved is session
    assert tab is chromium.latest_tab


def test_close_browser_quits_launched(fresh_manager):
    session, chromium = make_session("launched")
    fresh_manager._sessions[session.browser_id] = session
    fresh_manager.close_browser(session.browser_id)
    assert ("quit", False) in chromium.actions
    assert session.browser_id not in fresh_manager._sessions


def test_close_browser_disconnects_connected(fresh_manager):
    session, chromium = make_session("connected")
    fresh_manager._sessions[session.browser_id] = session
    fresh_manager.close_browser(session.browser_id)
    assert ("disconnect",) in chromium.actions
    assert ("quit",) not in [a for a in chromium.actions if a[0] == "quit"]


def test_context_lifecycle(fresh_manager):
    session, chromium = make_session()
    fresh_manager._sessions[session.browser_id] = session
    info = fresh_manager.new_context()
    assert info.browser_id == session.browser_id
    assert info.context_id in session.contexts
    assert fresh_manager.list_contexts()[0].context_id == info.context_id
    fresh_manager.close_context(info.context_id)
    assert info.context_id not in session.contexts
    with pytest.raises(ToolError):
        fresh_manager.get_context(info.context_id)


def test_element_registry_roundtrip(fresh_manager):
    session, chromium = make_session()
    fresh_manager._sessions[session.browser_id] = session
    tab = chromium.new_tab()
    ele = FakeElement()
    eid = fresh_manager.register_element(ele, tab, session.browser_id)
    assert fresh_manager.get_element(eid) is ele
    ele.alive = False
    with pytest.raises(ToolError, match="已失效"):
        fresh_manager.get_element(eid)


class _ScrolledFrame(FakeFrame):
    """iframe 的页面坐标与视口坐标不同（顶层文档已向下滚动 300）。"""

    def run_js(self, script, *args, **kwargs):
        return '{"x": 100, "y": 50, "w": 40, "h": 20}'

    @property
    def viewport_location(self):
        return (0, 0)

    @property
    def location(self):
        return (0, 300)


class _LegacyFrame(FakeFrame):
    """缺少 viewport_location 的旧版矩形对象（应回退 location）。"""

    def run_js(self, script, *args, **kwargs):
        return '{"x": 100, "y": 50, "w": 40, "h": 20}'

    @property
    def location(self):
        return (0, 300)


def test_page_scroll_reads_document_scroll():
    tab = FakeTab("tab-scroll")
    tab.run_js = lambda script, *a, **k: "12 300"
    assert page_scroll(tab) == (12.0, 300.0)


def test_vp_to_page_adds_scroll():
    tab = FakeTab("tab-scroll")
    tab.run_js = lambda script, *a, **k: "12 300"
    assert vp_to_page(tab, 100, 200) == (112.0, 500.0)


def test_vp_to_page_no_scroll_when_unavailable():
    tab = FakeTab("tab-noscript")  # 假 run_js 返回非滚动串
    assert vp_to_page(tab, 100, 200) == (100.0, 200.0)


def test_rect_center_uses_iframe_viewport_location():
    """iframe 偏移必须取视口坐标：页面滚动 300 时结果不应偏移 300。"""
    tab = FakeTab("tab-main")
    frame = _ScrolledFrame("f1")
    assert _rect_center_in_page(tab, FakeElement(), frame) == (100.0, 50.0)


def test_rect_center_falls_back_to_location():
    tab = FakeTab("tab-main")
    frame = _LegacyFrame("f1")
    assert _rect_center_in_page(tab, FakeElement(), frame) == (100.0, 350.0)


def test_element_registry_eviction():
    from drissionpage_mcp import manager as m

    old_limit = m.MAX_ELEMENTS
    m.MAX_ELEMENTS = 3
    bm = BrowserManager()
    try:
        ids = [bm.register_element(FakeElement(), FakeTab(f"t{i}"), "b") for i in range(5)]
        with pytest.raises(ToolError, match="未找到元素"):
            bm.get_element(ids[0])
        bm.get_element(ids[-1])
    finally:
        m.MAX_ELEMENTS = old_limit
