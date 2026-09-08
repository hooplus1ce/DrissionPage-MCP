"""BrowserManager 单元测试（不经过 MCP 层）。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from drissionpage_mcp.manager import BrowserManager

from conftest import FakeChromium, FakeElement, FakeTab, BrowserSession, make_session


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
