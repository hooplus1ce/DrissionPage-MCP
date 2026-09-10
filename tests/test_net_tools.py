"""net_listen_* 网络监听工具用例（假 DP 对象，不启动真实浏览器）。"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from conftest import FakeDataPacket


def _packet(url="https://demo18-scm.hoolinks.com/scmpsm/aps/approvalTemplate/approverOptions?type=dept", **kwargs):
    return FakeDataPacket(
        url,
        method=kwargs.pop("method", "GET"),
        post_data=kwargs.pop("post_data", None),
        params=kwargs.pop("params", {"type": "dept"}),
        body=kwargs.pop("body", {"code": 200, "data": ["测试部门JK69093718"]}),
        **kwargs,
    )


async def test_start_applies_targets_method_and_res_type(client, seeded_manager):
    _, _, tab = seeded_manager

    r = await client.call_tool(
        "net_listen_start",
        {"urls": "approverOptions", "method": "GET", "res_type": "XHR"},
    )

    assert r.data.listening is True
    assert tab.listen._urls == ["approverOptions"]
    assert tab.listen._is_regex is False
    assert tab.listen._method == {"GET"}
    assert tab.listen._res_type == {"XHR"}
    assert r.data.targets == ["approverOptions"]
    assert r.data.methods == ["GET"]
    assert r.data.res_types == ["XHR"]


async def test_start_without_urls_listens_everything(client, seeded_manager):
    _, _, tab = seeded_manager

    r = await client.call_tool("net_listen_start", {})

    assert tab.listen._urls is True
    assert r.data.targets is None  # None 表示监听全部


async def test_wait_returns_request_payload_and_body(client, seeded_manager):
    _, _, tab = seeded_manager
    packet = _packet(
        "https://demo18-scm.hoolinks.com/scmpsm/aps/approvalTemplate/save",
        method="POST",
        post_data={"type": "role", "ids": ["10598"]},
        params={"version": "1"},
        body={"code": 200, "message": "操作成功"},
        status=201,
    )
    tab.listen.packets = [packet]
    tab.listen.wait_result = packet

    r = await client.call_tool("net_listen_wait", {"count": 1, "include_body": True})

    assert r.data.found is True
    assert r.data.count == 1
    pkt = r.data.packets[0]
    assert pkt.url.endswith("/approvalTemplate/save")
    assert pkt.method == "POST"
    assert pkt.post_data == {"type": "role", "ids": ["10598"]}
    assert pkt.params == {"version": "1"}
    assert pkt.body == {"code": 200, "message": "操作成功"}
    assert pkt.status == 201
    assert pkt.kind == "request"


async def test_wait_timeout_reports_not_found(client, seeded_manager):
    _, _, tab = seeded_manager
    tab.listen.wait_result = False

    r = await client.call_tool("net_listen_wait", {"timeout": 0.5})

    assert r.data.found is False
    assert r.data.packets == []
    assert "0.5" in (r.data.note or "")


async def test_wait_partial_result_when_fit_count_false(client, seeded_manager):
    _, _, tab = seeded_manager
    tab.listen.wait_result = [_packet()]

    r = await client.call_tool("net_listen_wait", {"count": 3, "fit_count": False})

    assert r.data.found is False
    assert r.data.count == 1
    assert "1/3" in (r.data.note or "")


async def test_snapshot_drains_queue_without_any_wait(client, seeded_manager):
    _, _, tab = seeded_manager
    tab.listen.listening = True
    tab.listen.packets = [_packet(), _packet("https://x/api/other")]
    tab.listen._refill(clear=True)

    first = await client.call_tool("net_listen_snapshot", {})

    assert first.data.count == 2
    assert [p.url for p in first.data.packets][1].endswith("/api/other")
    # 关键契约：即时快照绝不等待
    assert not [c for c in tab.listen.calls if c[0] == "wait"]

    second = await client.call_tool("net_listen_snapshot", {})
    assert second.data.count == 0
    assert second.data.found is False


async def test_snapshot_without_listening_hints_to_start_first(client, seeded_manager):
    r = await client.call_tool("net_listen_snapshot", {})

    assert r.data.found is False
    assert "net_listen_start" in (r.data.note or "")


async def test_wait_silent_requires_active_listening(client, seeded_manager):
    _, _, tab = seeded_manager

    with pytest.raises(ToolError):
        await client.call_tool("net_listen_wait_silent", {"timeout": 1})

    await client.call_tool("net_listen_start", {"urls": "api"})
    tab.listen.silent_result = False
    r = await client.call_tool("net_listen_wait_silent", {"timeout": 1, "limit": 2})

    assert r.data.quiet is False
    assert ("wait_silent", 1.0, False, 2) in tab.listen.calls


async def test_stop_clears_queue_but_keeps_targets(client, seeded_manager):
    _, _, tab = seeded_manager
    await client.call_tool("net_listen_start", {"urls": "approverOptions"})
    tab.listen.packets = [_packet()]
    tab.listen._refill(clear=True)

    r = await client.call_tool("net_listen_stop", {})

    assert r.data.listening is False
    assert r.data.targets == ["approverOptions"]
    assert r.data.pending == 0


async def test_pause_and_resume_control_listening_flag(client, seeded_manager):
    _, _, tab = seeded_manager
    await client.call_tool("net_listen_start", {})

    paused = await client.call_tool("net_listen_pause", {})
    assert paused.data.listening is False
    assert ("pause", True) in tab.listen.calls

    resumed = await client.call_tool("net_listen_resume", {})
    assert resumed.data.listening is True


async def test_invalid_method_and_res_type_are_rejected(client, seeded_manager):
    with pytest.raises(ToolError):
        await client.call_tool("net_listen_start", {"method": "FETCH"})
    with pytest.raises(ToolError):
        await client.call_tool("net_listen_start", {"res_type": "XmlHttp"})
    # 大小写不敏感：合法值按官方名称归一
    r = await client.call_tool("net_listen_start", {"res_type": "xhr"})
    assert r.data.res_types == ["XHR"]


async def test_unbounded_wait_is_rejected(client, seeded_manager):
    with pytest.raises(ToolError):
        await client.call_tool("net_listen_wait", {"timeout": None})
    with pytest.raises(ToolError):
        await client.call_tool("net_listen_wait", {"timeout": 0})
