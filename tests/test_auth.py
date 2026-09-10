"""D1 认证引导 + D4 多账号档案会话测试（APS 契约，全部离线，不触网）。"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
import uuid
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from drissionpage_mcp import profiles as profiles_mod
from drissionpage_mcp.login import Challenge, LoginEngine, LoginOutcome
from drissionpage_mcp.profiles import load_profiles
from drissionpage_mcp.tools import auth as auth_mod

HOST = "demo18-scm.hoolinks.com"
ADMIN_URL = f"https://{HOST}/static/admin/"
LOGIN_PAGE = f"https://{HOST}/static/admin/login"
TOKEN = "e3af4285-3817-42dd-af4a-eabcaedf74be"


def _profile_env(monkeypatch, tmp_path, **extra):
    """最小可用的 HL_* 环境变量（凭据只在这里出现）。"""
    monkeypatch.setenv("HL_HOST_PREFIX", "demo18")
    monkeypatch.setenv("HL_USERNAME", "tester")
    monkeypatch.setenv("HL_USERPWD", "secret-pwd")
    monkeypatch.setenv("HL_PROFILES_FILE", str(tmp_path / "no-such.toml"))
    monkeypatch.setenv("HL_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("HL_PROFILE", raising=False)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _clean_auth_state(tmp_path, monkeypatch):
    """隔离模块级缓存与落盘目录，避免用例互相污染。"""
    monkeypatch.setenv("HL_SESSION_DIR", str(tmp_path / "sessions"))
    auth_mod._LOGINS.clear()
    auth_mod._OPEN_SESSIONS.clear()
    auth_mod._CHALLENGES._items.clear()
    yield
    auth_mod._LOGINS.clear()
    auth_mod._OPEN_SESSIONS.clear()
    auth_mod._CHALLENGES._items.clear()


def _aps_toml(tmp_path, *profiles: tuple[str, str, str]) -> Path:
    """生成 APS 档案文件；profiles = [(name, username, role), ...]。"""
    lines: list[str] = []
    for name, username, role in profiles:
        lines += [
            f"[profiles.{name}]",
            'host_prefix = "demo18"',
            f'username = "{username}"',
            'password = "pwd-x"',
            f'role = "{role}"',
            "",
        ]
    toml = tmp_path / "profiles.toml"
    toml.write_text("\n".join(lines), encoding="utf-8")
    return toml


class FakeEngine:
    """登录引擎替身：按脚本返回挑战与结果，不发起任何 HTTP 请求。"""

    def __init__(self, profile, *, outcomes=None):
        self.profile = profile
        self.outcomes = list(outcomes or [])
        self.fetched = 0
        self.submitted: list[tuple[str, str]] = []

    def fetch_captcha(self) -> Challenge:
        self.fetched += 1
        return Challenge(
            profile=self.profile.name,
            captcha_id=uuid.uuid4().hex[:8],
            image=b"\x89PNG-fake",
            content_type="image/png",
            created_at=time.time(),
            jar=http.cookiejar.CookieJar(),
        )

    def submit(self, challenge: Challenge, code: str) -> LoginOutcome:
        self.submitted.append((challenge.captcha_id, code))
        if self.outcomes:
            return self.outcomes.pop(0)
        return LoginOutcome(
            ok=True,
            cookies=[
                {"name": "UCTOKEN", "value": TOKEN, "domain": HOST, "path": "/"},
                {"name": "SESSION", "value": "sess-1", "domain": HOST, "path": "/"},
            ],
            token=TOKEN,
        )


def _install_engine(monkeypatch, engine: FakeEngine):
    monkeypatch.setattr(auth_mod, "_engine", lambda profile: engine)
    return engine


# ---------- 档案配置 ----------


def test_profile_from_env_prefix_derives_aps_urls(monkeypatch, tmp_path):
    _profile_env(monkeypatch, tmp_path)
    profile = load_profiles()["default"]
    assert profile.username == "tester"
    assert profile.password == "secret-pwd"
    assert profile.origin == f"https://{HOST}"
    assert profile.admin_url == ADMIN_URL
    assert profile.login_page == LOGIN_PAGE
    assert profile.cookie_domain == f".{HOST}"
    # APS 默认契约
    assert profile.login_path == "/scmpsm/login/signin"
    assert profile.captcha_path == "/scmpsm/login/validateCode"
    assert profile.username_field == "userName"
    assert profile.password_field == "userPwd"
    assert profile.success_field == "ok"
    assert profile.message_field == "msg"
    assert profile.token_field == "data"
    assert profile.token_store == "HL-Access-Token"


def test_profile_explicit_env_overrides_derivation(monkeypatch, tmp_path):
    _profile_env(
        monkeypatch,
        tmp_path,
        HL_BASE_URL="https://other.example.com/api",
        HL_ADMIN_URL="https://other.example.com/app/#/",
        HL_LOGIN_PATH="/api/login.json",
    )
    profile = load_profiles()["default"]
    assert profile.origin == "https://other.example.com"
    assert profile.api_prefix == "/api"
    assert profile.admin_url == "https://other.example.com/app/#/"
    assert profile.url("/api/login.json") == "https://other.example.com/api/api/login.json"


def test_profile_file_multi_and_env_override(monkeypatch, tmp_path):
    toml = _aps_toml(
        tmp_path,
        ("admin", "admin_user", "申请人"),
        ("approver", "approver_user", "审批人"),
    )
    _profile_env(monkeypatch, tmp_path)
    monkeypatch.setenv("HL_PROFILES_FILE", str(toml))
    monkeypatch.setenv("HL_PROFILE", "approver")
    monkeypatch.setenv("HL_USERNAME", "env_override_user")

    profiles = load_profiles()
    # HL_PROFILE=approver 使环境变量档案名为 approver（与 TOML 同名合并），不产生 default
    assert sorted(profiles) == ["admin", "approver"]
    assert profiles["admin"].role == "申请人"
    assert profiles["admin"].username == "admin_user"
    assert profiles["admin"].password == "pwd-x"
    # 环境变量只覆盖同名档案（HL_PROFILE=approver）
    assert profiles["approver"].username == "env_override_user"
    assert profiles["approver"].password == "secret-pwd"
    assert profiles["approver"].source.endswith("+env")


def test_profile_missing_credentials_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("HL_HOST_PREFIX", "demo18")
    monkeypatch.setenv("HL_PROFILES_FILE", str(tmp_path / "none.toml"))
    monkeypatch.delenv("HL_USERNAME", raising=False)
    monkeypatch.delenv("HL_USERPWD", raising=False)
    with pytest.raises(ToolError, match="缺少凭据"):
        load_profiles()


# ---------- 登录引擎（注入 _request，验证真实解析逻辑） ----------


def _profile(monkeypatch, tmp_path):
    _profile_env(monkeypatch, tmp_path)
    return load_profiles()["default"]


def _challenge() -> Challenge:
    return Challenge(
        "default", "cid", b"img", "image/jpeg", time.time(), http.cookiejar.CookieJar()
    )


def test_engine_fetch_captcha_requires_image(monkeypatch, tmp_path):
    engine = LoginEngine(_profile(monkeypatch, tmp_path))
    monkeypatch.setattr(
        LoginEngine, "_request", lambda self, *a, **k: (200, b"not-image", "text/html")
    )
    with pytest.raises(ToolError, match="未返回图片"):
        engine.fetch_captcha()

    monkeypatch.setattr(LoginEngine, "_request", lambda self, *a, **k: (200, b"img", "image/jpeg"))
    challenge = engine.fetch_captcha()
    assert challenge.image == b"img"
    assert challenge.profile == "default"
    assert challenge.captcha_id


def test_engine_login_posts_json_and_extracts_token(monkeypatch, tmp_path):
    engine = LoginEngine(_profile(monkeypatch, tmp_path))
    captured: dict = {}

    def fake_request(self, opener, url, *, data=None, headers=None):
        captured.update(url=url, data=data, headers=headers)
        body = json.dumps({"msg": "登录成功！", "ok": True, "status": 0, "data": TOKEN})
        return 200, body.encode(), "application/json"

    monkeypatch.setattr(LoginEngine, "_request", fake_request)
    outcome = engine.submit(_challenge(), "4710")

    assert outcome.ok and outcome.token == TOKEN
    assert captured["url"].endswith("/scmpsm/login/signin")
    assert json.loads(captured["data"]) == {
        "userName": "tester",
        "userPwd": "secret-pwd",
        "vcode": "4710",
    }
    assert captured["headers"]["Content-Type"].startswith("application/json")
    # 令牌值不进入 raw（避免经工具回显）
    assert TOKEN not in json.dumps(outcome.raw)


def test_engine_login_failure_marks_captcha_error(monkeypatch, tmp_path):
    engine = LoginEngine(_profile(monkeypatch, tmp_path))
    body = json.dumps({"msg": "验证码错误", "ok": False}).encode()
    monkeypatch.setattr(LoginEngine, "_request", lambda self, *a, **k: (200, body, "application/json"))
    outcome = engine.submit(_challenge(), "0000")
    assert not outcome.ok and outcome.captcha_error and outcome.token is None


def test_engine_missing_token_raises(monkeypatch, tmp_path):
    engine = LoginEngine(_profile(monkeypatch, tmp_path))
    body = json.dumps({"msg": "登录成功！", "ok": True, "data": None}).encode()
    monkeypatch.setattr(LoginEngine, "_request", lambda self, *a, **k: (200, body, "application/json"))
    with pytest.raises(ToolError, match="未在响应字段"):
        engine.submit(_challenge(), "4710")


def test_engine_collects_session_cookies(monkeypatch, tmp_path):
    """成功登录时把 jar 里的 cookie 转成 DP 可用的字典（含域/路径）。"""
    engine = LoginEngine(_profile(monkeypatch, tmp_path))
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0,
            name="cookie_token",
            value="tk",
            port=None,
            port_specified=False,
            domain=HOST,
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
    )
    challenge = Challenge("default", "cid", b"img", "image/jpeg", time.time(), jar)
    body = json.dumps({"ok": True, "msg": "登录成功！", "data": TOKEN}).encode()
    monkeypatch.setattr(LoginEngine, "_request", lambda self, *a, **k: (200, body, "application/json"))
    outcome = engine.submit(challenge, "1234")
    assert outcome.ok and outcome.token == TOKEN
    assert outcome.cookies[0]["name"] == "cookie_token"
    assert outcome.cookies[0]["domain"] == HOST
    assert outcome.cookies[0]["path"] == "/"


# ---------- auth_login 两段式工具 ----------


async def test_auth_login_two_step_flow(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    engine = _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))

    first = await client.call_tool("auth_login", {})
    assert first.data.captcha_required is True
    assert first.data.captcha_id

    second = await client.call_tool("auth_login", {"captcha_code": "4710"})
    assert second.data.ok is True
    assert second.data.has_token is True
    assert set(second.data.cookie_names) == {"UCTOKEN", "SESSION"}
    assert engine.submitted == [(first.data.captcha_id, "4710")]
    assert TOKEN not in str(second.data)

    # 第三次：复用缓存，不再取验证码
    fetched_before = engine.fetched
    third = await client.call_tool("auth_login", {})
    assert third.data.ok is True
    assert third.data.source == "session"
    assert engine.fetched == fetched_before


async def test_auth_login_wrong_captcha_refreshes_challenge(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    profile = load_profiles()["default"]
    engine = _install_engine(
        monkeypatch,
        FakeEngine(profile, outcomes=[LoginOutcome(ok=False, message="验证码错误", captcha_error=True)]),
    )

    await client.call_tool("auth_login", {})
    failed = await client.call_tool("auth_login", {"captcha_code": "0000"})
    assert failed.data.ok is False
    assert failed.data.captcha_required is True
    assert "验证码" in (failed.data.message or "")
    # 失败后重新取图，并给出新的 captcha_id
    assert engine.fetched == 2
    assert failed.data.captcha_id


async def test_auth_captcha_returns_image_block(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))

    result = await client.call_tool("auth_captcha", {})
    kinds = [type(block).__name__ for block in result.content]
    assert kinds == ["TextContent", "ImageContent"]
    assert "captcha_id" in result.content[0].text
    assert "auth_login" in result.content[0].text


async def test_profile_list_hides_password(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    result = await client.call_tool("profile_list", {})
    info = result.data[0]
    assert info.name == "default"
    assert info.username == "tester"
    assert info.has_password is True
    text = "".join(getattr(block, "text", "") for block in result.content)
    assert "secret-pwd" not in text
    assert not hasattr(info, "password")


# ---------- profile_open（D4 多账号会话） ----------


async def test_profile_open_creates_isolated_context_and_injects(
    client, monkeypatch, tmp_path, seeded_manager
):
    _profile_env(monkeypatch, tmp_path)
    _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))
    session, chromium, _tab = seeded_manager

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})

    result = await client.call_tool("profile_open", {"profile": "default"})
    data = result.data
    assert data.logged_in is True
    assert data.context_id and data.tab_id
    assert data.url == ADMIN_URL
    assert data.context_id in session.contexts

    tab = chromium.tabs[data.tab_id]
    assert ("get", LOGIN_PAGE) in tab.steps
    assert ("get", ADMIN_URL) in tab.steps
    js = next(step[1] for step in tab.steps if step[0] == "run_js")
    assert f'localStorage.setItem("HL-Access-Token", "{TOKEN}")' in js
    injected = {c["name"]: c["value"] for c in tab.cookies_value}
    assert injected["HL-Access-Token"] == TOKEN
    assert injected["UCTOKEN"] == TOKEN

    closed = await client.call_tool("profile_close", {"profile": "default"})
    assert closed.data.ok is True
    assert data.context_id not in session.contexts


async def test_profile_open_requires_captcha_then_reuses_session(
    client, monkeypatch, tmp_path, seeded_manager
):
    _profile_env(monkeypatch, tmp_path)
    engine = _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))

    first = await client.call_tool("profile_open", {"profile": "default"})
    assert first.data.logged_in is False
    assert first.data.login.captcha_required is True
    assert first.data.tab_id  # 标签页先建好，id 稳定
    context_id, tab_id = first.data.context_id, first.data.tab_id

    await client.call_tool("auth_login", {"captcha_code": "4710"})

    second = await client.call_tool("profile_open", {"profile": "default"})
    assert second.data.logged_in is True
    assert second.data.reused is True
    assert (second.data.context_id, second.data.tab_id) == (context_id, tab_id)
    assert engine.submitted == [(first.data.login.captcha_id, "4710")]

    await client.call_tool("profile_close", {"profile": "default"})


async def test_profile_open_multi_role_isolation(client, monkeypatch, tmp_path, seeded_manager):
    """两个角色档案各自独立上下文（审批/权限并行场景）。"""
    toml = _aps_toml(
        tmp_path,
        ("role_a", "hooplus1ce", "管理员"),
        ("role_b", "hooplus1cer", "审批人"),
    )
    monkeypatch.setenv("HL_PROFILES_FILE", str(toml))
    for key in ("HL_HOST_PREFIX", "HL_USERNAME", "HL_USERPWD", "HL_PROFILE"):
        monkeypatch.delenv(key, raising=False)

    profiles = load_profiles()
    monkeypatch.setattr(auth_mod, "_engine", lambda profile: FakeEngine(profile))
    for name in ("role_a", "role_b"):
        await client.call_tool("auth_login", {"profile": name})
        await client.call_tool("auth_login", {"profile": name, "captcha_code": "1234"})

    a = (await client.call_tool("profile_open", {"profile": "role_a"})).data
    b = (await client.call_tool("profile_open", {"profile": "role_b"})).data
    assert a.context_id != b.context_id
    assert a.logged_in and b.logged_in
    assert set(profiles) == {"role_a", "role_b"}
    assert profiles["role_b"].username == "hooplus1cer"

    await client.call_tool("profile_close", {"profile": "role_a"})
    await client.call_tool("profile_close", {"profile": "role_b"})


# ---------- 登录态缓存与落盘 ----------


async def test_session_persisted_and_restored_from_disk(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})

    session_file = tmp_path / "sessions" / "default.json"
    assert session_file.is_file()
    saved = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved["origin"] == f"https://{HOST}"
    assert saved["token"] == TOKEN
    assert [c["name"] for c in saved["cookies"]] == ["UCTOKEN", "SESSION"]

    # 模拟服务重启：清内存缓存，从磁盘恢复登录态
    auth_mod._LOGINS.clear()
    again = await client.call_tool("auth_login", {})
    assert again.data.ok is True
    assert again.data.source == "session"

    cleared = await client.call_tool("auth_session_clear", {})
    assert cleared.data.ok is True
    assert not session_file.exists()


async def test_stale_session_file_ignored(client, monkeypatch, tmp_path, seeded_manager):
    _profile_env(monkeypatch, tmp_path)
    engine = _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))
    monkeypatch.setenv("HL_SESSION_TTL", "0")

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})
    auth_mod._LOGINS.clear()

    result = await client.call_tool("auth_login", {})
    assert result.data.captcha_required is True
    assert engine.fetched >= 2


def test_challenge_store_prunes_expired(monkeypatch, tmp_path):
    monkeypatch.setenv("HL_CAPTCHA_TTL", "1")
    old = Challenge("default", "old", b"img", "image/jpeg", time.time() - 5, http.cookiejar.CookieJar())
    fresh = Challenge("default", "new", b"img", "image/jpeg", time.time(), http.cookiejar.CookieJar())
    auth_mod._CHALLENGES.put(old)
    auth_mod._CHALLENGES.put(fresh)
    assert auth_mod._CHALLENGES.latest("default").captcha_id == "new"
    assert auth_mod._CHALLENGES.pop("old") is None


def test_profiles_file_helper_prefers_explicit_path(monkeypatch, tmp_path):
    explicit = tmp_path / "custom.toml"
    monkeypatch.setenv("HL_PROFILES_FILE", str(explicit))
    assert profiles_mod.profiles_file() == explicit


# ---------- 登录态探测（O14）与导航去重（O16） ----------


def _engine_with_probe(monkeypatch, probe_result):
    """安装一个可编程探测结果的引擎替身。"""
    engine = FakeEngine(load_profiles()["default"])
    engine.probe_calls: list = []

    def probe(cookies, url=None):
        engine.probe_calls.append(url)
        return probe_result

    engine.probe = probe
    _install_engine(monkeypatch, engine)
    return engine


async def test_cached_session_rejected_when_probe_fails(client, monkeypatch, tmp_path):
    """服务端已判定失效时，缓存登录态不得再被乐观复用。"""
    _profile_env(monkeypatch, tmp_path)
    engine = _engine_with_probe(monkeypatch, False)

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})
    assert auth_mod._LOGINS  # 登录态已缓存

    again = await client.call_tool("auth_login", {})
    assert engine.probe_calls  # 复用前确实探测过
    assert again.data.captcha_required is True  # 探测判定失效 → 回到验证码流程
    assert again.data.source != "session"
    assert not auth_mod._LOGINS  # 失效缓存被清除


async def test_cached_session_kept_when_probe_accepts(client, monkeypatch, tmp_path):
    _profile_env(monkeypatch, tmp_path)
    engine = _engine_with_probe(monkeypatch, True)

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})

    again = await client.call_tool("auth_login", {})
    assert engine.probe_calls
    assert again.data.ok is True
    assert again.data.source == "session"


def _captcha_id_from(result) -> str:
    text = "".join(getattr(block, "text", "") for block in result.content)
    match = re.search(r"captcha_id=(\w+)", text)
    assert match, f"响应中未包含 captcha_id: {text[:200]}"
    return match.group(1)


async def test_auth_captcha_reuses_pending_challenge(client, monkeypatch, tmp_path):
    """重复取图必须复用同一挑战，否则 captcha_id 与已识别图片会错配。"""
    _profile_env(monkeypatch, tmp_path)
    engine = _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))

    first = await client.call_tool("auth_captcha", {})
    second = await client.call_tool("auth_captcha", {})
    assert engine.fetched == 1
    assert _captcha_id_from(first) == _captcha_id_from(second)

    refreshed = await client.call_tool("auth_captcha", {"refresh": True})
    assert engine.fetched == 2
    assert _captcha_id_from(refreshed) != _captcha_id_from(first)


def test_url_helpers():
    assert auth_mod._same_url(LOGIN_PAGE + "/", LOGIN_PAGE)
    assert auth_mod._same_url(ADMIN_URL + "?a=1", ADMIN_URL)
    assert not auth_mod._same_url(ADMIN_URL, LOGIN_PAGE)
    # admin_url 是 login_page 的前缀，停在登录页时绝不能判为「已到目标页」
    assert not auth_mod._on_target_page(LOGIN_PAGE, ADMIN_URL)
    assert auth_mod._on_target_page(ADMIN_URL, ADMIN_URL)
    assert auth_mod._on_target_page(ADMIN_URL + "?tab=1", ADMIN_URL)


async def test_profile_open_reuse_skips_extra_navigation(
    client, monkeypatch, tmp_path, seeded_manager
):
    """已停在目标页的复用不应重跑 login_page → target 整页导航。"""
    _profile_env(monkeypatch, tmp_path)
    _install_engine(monkeypatch, FakeEngine(load_profiles()["default"]))
    _session, chromium, _tab = seeded_manager

    await client.call_tool("auth_login", {})
    await client.call_tool("auth_login", {"captcha_code": "4710"})
    first = (await client.call_tool("profile_open", {"profile": "default"})).data
    tab = chromium.tabs[first.tab_id]
    gets_before = [s for s in tab.steps if s[0] == "get"]

    second = (await client.call_tool("profile_open", {"profile": "default"})).data
    assert second.reused is True
    assert second.logged_in is True
    assert [s for s in tab.steps if s[0] == "get"] == gets_before  # 未再发生整页导航
    assert "未重复导航" in (second.login.message or "")
