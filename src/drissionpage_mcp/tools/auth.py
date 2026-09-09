"""登录引导与多账号档案工具（D1 + D4）。

设计要点：
- **验证码不经过任何 OCR 组件**：``auth_captcha`` 返回图片，多模态模型读出字符后
  由 ``auth_login`` 提交，服务端不引入 ddddocr 等重依赖；
- **凭据不出服务端**：工具只接受档案名，返回值永不含密码与访问令牌；
- **登录态复用**：内存 + 磁盘缓存（``HL_SESSION_PERSIST`` 可关），``profile_open``
  为每个档案开独立 BrowserContext，支持多角色并行登录。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from ..login import Challenge, LoginEngine
from ..manager import manager
from ..models import AuthResult, MessageResult, ProfileInfo, ProfileSession
from ..profiles import Profile, get_profile, load_profiles

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Auth")

DEFAULT_PROFILE = "default"
SESSION_FILE_SUFFIX = ".json"
LOGIN_URL_HINTS = ("/login", "signin")


# ---------- 运行时配置（每次调用读取，便于 .env / 测试即时生效） ----------


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _captcha_ttl() -> float:
    return _env_float("HL_CAPTCHA_TTL", 180.0)


def _session_ttl() -> float:
    return _env_float("HL_SESSION_TTL", 12 * 3600.0)


def _session_persist() -> bool:
    return os.environ.get("HL_SESSION_PERSIST", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _session_dir() -> Path:
    return Path(os.environ.get("HL_SESSION_DIR") or ".dpmcp/sessions").expanduser()


# ---------- 档案与引擎 ----------


def _profiles() -> dict[str, Profile]:
    return load_profiles()


def _get(name: str) -> Profile:
    return get_profile(name, _profiles())


def _engine(profile: Profile) -> LoginEngine:
    """登录引擎工厂（测试可替换）。"""
    return LoginEngine(profile)


class _ChallengeStore:
    """验证码挑战的内存暂存（TTL + 容量上限，线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Challenge] = {}

    def put(self, challenge: Challenge) -> None:
        with self._lock:
            self._prune_locked()
            self._items[challenge.captcha_id] = challenge
            while len(self._items) > 8:
                oldest = min(self._items.values(), key=lambda c: c.created_at)
                self._items.pop(oldest.captcha_id, None)

    def pop(self, captcha_id: str) -> Challenge | None:
        with self._lock:
            self._prune_locked()
            return self._items.pop(captcha_id, None)

    def latest(self, profile: str) -> Challenge | None:
        with self._lock:
            self._prune_locked()
            candidates = [c for c in self._items.values() if c.profile == profile]
            return max(candidates, key=lambda c: c.created_at) if candidates else None

    def pop_latest(self, profile: str) -> Challenge | None:
        """取出并消费该档案最近一次挑战（提交登录后不得复用）。"""
        with self._lock:
            self._prune_locked()
            candidates = [c for c in self._items.values() if c.profile == profile]
            if not candidates:
                return None
            newest = max(candidates, key=lambda c: c.created_at)
            return self._items.pop(newest.captcha_id, None)

    def _prune_locked(self) -> None:
        deadline = time.time() - _captcha_ttl()
        for cid in [c.captcha_id for c in self._items.values() if c.created_at < deadline]:
            self._items.pop(cid, None)


_CHALLENGES = _ChallengeStore()


@dataclass
class _LoginState:
    cookies: list[dict]
    origin: str
    obtained_at: float
    token: str | None = None


_LOGINS: dict[str, _LoginState] = {}
_LOGIN_LOCK = threading.Lock()
_OPEN_SESSIONS: dict[str, dict] = {}
_OPEN_LOCK = threading.Lock()


def _cache_login(profile: Profile, cookies: list[dict], token: str | None = None) -> None:
    with _LOGIN_LOCK:
        _LOGINS[profile.name] = _LoginState(cookies, profile.origin, time.time(), token)
    _write_session(profile, cookies, token)


def _cached_login(profile: Profile) -> _LoginState | None:
    with _LOGIN_LOCK:
        state = _LOGINS.get(profile.name)
    if state is not None:
        fresh = state.origin == profile.origin and time.time() - state.obtained_at < _session_ttl()
        if fresh:
            return state
        with _LOGIN_LOCK:
            _LOGINS.pop(profile.name, None)

    stored = _read_session(profile)
    if not stored:
        return None
    cookies, token = stored
    with _LOGIN_LOCK:
        _LOGINS[profile.name] = _LoginState(cookies, profile.origin, time.time(), token)
        return _LOGINS[profile.name]


def _session_path(profile: Profile) -> Path:
    return _session_dir() / f"{profile.name}{SESSION_FILE_SUFFIX}"


def _write_session(profile: Profile, cookies: list[dict], token: str | None = None) -> None:
    if not _session_persist():
        return
    try:
        path = _session_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "profile": profile.name,
                    "origin": profile.origin,
                    "saved_at": time.time(),
                    "cookies": cookies,
                    "token": token,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # 落盘失败不影响本次登录
        pass


def _read_session(profile: Profile) -> tuple[list[dict], str | None] | None:
    if not _session_persist():
        return None
    path = _session_path(profile)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("origin") != profile.origin:
        return None
    if time.time() - float(data.get("saved_at") or 0) > _session_ttl():
        return None
    cookies = data.get("cookies")
    token = data.get("token")
    cookies = cookies if isinstance(cookies, list) else []
    token = token if isinstance(token, str) and token else None
    if not cookies and not token:
        return None
    return cookies, token


def _clear_session(profile: Profile) -> None:
    with _LOGIN_LOCK:
        _LOGINS.pop(profile.name, None)
    try:
        _session_path(profile).unlink(missing_ok=True)
    except OSError:
        pass


# ---------- 登录内核 ----------


def _cookie_names(cookies: list[dict]) -> list[str]:
    return [str(c.get("name")) for c in cookies if c.get("name")]


def _do_login(
    profile: Profile,
    *,
    captcha_code: str | None = None,
    captcha_id: str | None = None,
    force: bool = False,
) -> AuthResult:
    """两段式登录内核：无 code 时只准备验证码，有 code 时提交。"""
    if not force and captcha_code is None:
        state = _cached_login(profile)
        if state is not None:
            return AuthResult(
                ok=True,
                profile=profile.name,
                source="session",
                cookie_count=len(state.cookies),
                cookie_names=_cookie_names(state.cookies),
                has_token=bool(state.token),
                message="已复用有效登录态（force=true 可强制重新登录）",
            )

    if captcha_code is None:
        challenge = _CHALLENGES.latest(profile.name) or _engine(profile).fetch_captcha()
        _CHALLENGES.put(challenge)
        return AuthResult(
            ok=False,
            profile=profile.name,
            captcha_required=True,
            captcha_id=challenge.captcha_id,
            message=(
                f'需要验证码：调用 auth_captcha(profile="{profile.name}") 查看图片，'
                f'再用 auth_login(profile="{profile.name}", captcha_code="<识别结果>") 登录'
            ),
        )

    challenge = (
        _CHALLENGES.pop(captcha_id) if captcha_id else _CHALLENGES.pop_latest(profile.name)
    )
    if challenge is None:
        challenge = _engine(profile).fetch_captcha()
        _CHALLENGES.put(challenge)
        return AuthResult(
            ok=False,
            profile=profile.name,
            captcha_required=True,
            captcha_id=challenge.captcha_id,
            message="验证码已过期，请重新调用 auth_captcha 识别图片",
        )

    outcome = _engine(profile).submit(challenge, captcha_code)
    if not outcome.ok:
        fresh = _engine(profile).fetch_captcha()
        _CHALLENGES.put(fresh)
        detail = outcome.message or "登录被拒绝"
        hint = "（验证码可能识别有误，请重新识别）" if outcome.captcha_error else ""
        return AuthResult(
            ok=False,
            profile=profile.name,
            captcha_required=True,
            captcha_id=fresh.captcha_id,
            message=f"{detail}{hint}",
        )

    _cache_login(profile, outcome.cookies, outcome.token)
    message = "登录成功"
    if outcome.update_pwd:
        message += "；服务端要求修改初始密码"
    return AuthResult(
        ok=True,
        profile=profile.name,
        cookie_count=len(outcome.cookies),
        cookie_names=_cookie_names(outcome.cookies),
        has_token=bool(outcome.token),
        update_pwd=outcome.update_pwd,
        message=message,
    )


def _landed_on_login(url: str | None) -> bool:
    if not url:
        return False
    lowered = str(url).lower()
    return any(hint in lowered for hint in LOGIN_URL_HINTS)


def _inject(
    tab, profile: Profile, state: _LoginState, target_url: str, source: str
) -> AuthResult:
    """把登录态注入标签页并导航到目标页，返回落点状态。

    先落到同源登录页，写入 localStorage[token_store] + cookies（含令牌 cookie），
    再进入目标页——与前端登录后的真实状态一致。
    """
    if not state.token:
        raise ToolError(f"档案 [{profile.name}] 缺少访问令牌，请先 auth_login")
    cookies = list(state.cookies)
    if not any(c.get("name") == profile.token_store for c in cookies):
        cookies.append(
            {
                "name": profile.token_store,
                "value": state.token,
                "domain": profile.access_domain,
                "path": "/",
            }
        )
    tab.get(profile.login_page)
    if cookies:
        tab.set.cookies(cookies)
    tab.run_js(
        f"localStorage.setItem({json.dumps(profile.token_store)}, "
        f"{json.dumps(state.token)});"
    )

    tab.get(target_url)
    url = getattr(tab, "url", None)
    ready = None
    try:
        ready = str(tab.states.ready_state)
    except Exception:
        pass
    landed_login = _landed_on_login(url)
    return AuthResult(
        ok=not landed_login,
        profile=profile.name,
        source=source,
        cookie_count=len(cookies),
        cookie_names=_cookie_names(cookies),
        has_token=bool(state.token),
        tab_id=getattr(tab, "tab_id", None),
        url=url,
        ready_state=ready,
        message="已注入登录态并打开目标页" if not landed_login else "登录态未生效：仍落在登录页",
    )


# ---------- MCP 工具 ----------


@mcp.tool(
    tags={"auth", "profile"},
    annotations={"title": "列出账号档案", "readOnlyHint": True},
)
def profile_list() -> list[ProfileInfo]:
    """列出服务端已配置的账号档案（不含密码），供 profile_open / auth_login 选用。

    配置来源：`.env` 的 HL_* 环境变量，或 `HL_PROFILES_FILE` 指向的 TOML（多档案）。
    """
    return [
        ProfileInfo(
            name=p.name,
            username=p.username or None,
            role=p.role,
            admin_url=p.admin_url,
            login_page=p.login_page,
            cookie_domain=p.cookie_domain,
            has_password=bool(p.password),
            source=p.source,
        )
        for p in sorted(_profiles().values(), key=lambda x: x.name)
    ]


@mcp.tool(
    tags={"auth"},
    annotations={"title": "获取登录验证码", "readOnlyHint": False},
)
def auth_captcha(profile: str = DEFAULT_PROFILE) -> list:
    """获取登录验证码图片，**直接交给多模态模型识别**（服务端不做 OCR）。

    返回 [提示文本, 图片内容块]。识别后调用
    `auth_login(profile=..., captcha_id=..., captcha_code="<识别结果>")` 完成登录。
    """
    p = _get(profile)
    challenge = _engine(p).fetch_captcha()
    _CHALLENGES.put(challenge)
    fmt = "png" if "png" in challenge.content_type else "jpeg"
    hint = (
        f"验证码已就绪：profile={p.name} captcha_id={challenge.captcha_id} "
        f"（约 {int(_captcha_ttl())}s 内有效）。请识别图中字符，然后调用 "
        f'auth_login(profile="{p.name}", captcha_id="{challenge.captcha_id}", '
        f'captcha_code="<识别结果>")'
    )
    return [hint, Image(data=challenge.image, format=fmt)]


@mcp.tool(
    tags={"auth"},
    annotations={"title": "登录档案账号", "readOnlyHint": False},
)
def auth_login(
    profile: str = DEFAULT_PROFILE,
    captcha_code: str | None = None,
    captcha_id: str | None = None,
    force: bool = False,
) -> AuthResult:
    """登录档案账号（HTTP 两段式，不依赖 OCR）。

    流程：
    1. 不带 captcha_code 调用 → 若已有有效登录态直接复用；否则返回
       `captcha_required=true` 与 `captcha_id`；
    2. `auth_captcha(profile=...)` 查看验证码图片（交给模型识别）；
    3. 带 `captcha_code` 再调用 → 提交登录，成功后登录态缓存在服务端
       （`HL_SESSION_PERSIST=true` 时同时落盘，供后续复用）。

    Args:
        profile: 档案名（见 profile_list）
        captcha_code: 模型识别出的验证码字符
        captcha_id: auth_captcha 返回的挑战 id（省略时用该档案最近一次挑战）
        force: 忽略缓存登录态，强制重新登录
    """
    return _do_login(_get(profile), captcha_code=captcha_code, captcha_id=captcha_id, force=force)


@mcp.tool(
    tags={"auth", "profile"},
    annotations={"title": "打开档案会话", "readOnlyHint": False},
)
def profile_open(
    profile: str = DEFAULT_PROFILE,
    url: str | None = None,
    isolated: bool = True,
    captcha_code: str | None = None,
    captcha_id: str | None = None,
    browser_id: str | None = None,
    reuse: bool = True,
) -> ProfileSession:
    """打开一个档案会话：独立 BrowserContext + 标签页 + 自动登录 + 打开目标页。

    多角色并行（如审批流或签/会签、权限测试）用不同 profile 各开一套，cookies/令牌互不干扰。
    首次调用若需要验证码，会先建好标签页并返回 `login.captcha_required=true`；
    按提示走 `auth_captcha` → `auth_login` 后，再次调用本工具（reuse=true）即可拿到
    同一套 context_id / tab_id 并完成注入。

    Args:
        profile: 档案名（见 profile_list）
        url: 目标页，省略时用档案的 admin_url
        isolated: true=新建独立 BrowserContext（cookies 隔离），false=用主上下文
        captcha_code / captcha_id: 同 auth_login，用于一次调用直接完成登录
        browser_id: 浏览器会话 id，省略时用当前唯一会话
        reuse: 复用已打开的同档案会话
    """
    p = _get(profile)
    target = url or p.admin_url

    record = _open_record(p) if reuse else None
    tab = _find_tab(record.get("tab_id")) if record else None
    if tab is None:
        record = None

    if record is not None:
        login = _do_login(p, captcha_code=captcha_code, captcha_id=captcha_id)
        if login.ok:
            state = _cached_login(p)
            login = _inject(tab, p, state, target, login.source) if state else login
        return _session_result(p, record, tab, reused=True, login=login)

    session_obj = None
    if isolated:
        context = manager.new_context(browser_id)
        ctx, session_obj = manager.get_context(context.context_id, browser_id)
        context_id = context.context_id
        tab = ctx.new_tab(url=target)
    else:
        session_obj = manager.get_session(browser_id)
        context_id = ""
        tab = session_obj.chromium.new_tab(url=target)

    login = _do_login(p, captcha_code=captcha_code, captcha_id=captcha_id)
    if login.ok:
        state = _cached_login(p)
        login = _inject(tab, p, state, target, login.source) if state else login

    record = {
        "context_id": context_id,
        "tab_id": tab.tab_id,
        "browser_id": getattr(session_obj, "browser_id", browser_id),
    }
    with _OPEN_LOCK:
        _OPEN_SESSIONS[p.name] = record
    return _session_result(p, record, tab, reused=False, login=login)


@mcp.tool(
    tags={"auth", "profile"},
    annotations={"title": "关闭档案会话", "destructiveHint": True},
)
def profile_close(profile: str = DEFAULT_PROFILE) -> MessageResult:
    """关闭档案会话的独立上下文（其标签页与 cookies 一并清除）；缓存登录态保留。"""
    p = _get(profile)
    with _OPEN_LOCK:
        record = _OPEN_SESSIONS.pop(p.name, None)
    context_id = (record or {}).get("context_id")
    if context_id:
        manager.close_context(context_id)
        return MessageResult(ok=True, message=f"已关闭档案 [{p.name}] 的上下文 {context_id}")
    return MessageResult(ok=True, message=f"档案 [{p.name}] 没有打开的独立上下文")


@mcp.tool(
    tags={"auth"},
    annotations={"title": "清除登录态缓存", "destructiveHint": True},
)
def auth_session_clear(profile: str = DEFAULT_PROFILE) -> MessageResult:
    """清除档案的登录态缓存（内存 + 落盘），下次登录需要重新识别验证码。"""
    p = _get(profile)
    _clear_session(p)
    return MessageResult(ok=True, message=f"已清除档案 [{p.name}] 的登录态缓存")


# ---------- 辅助 ----------


def _open_record(profile: Profile) -> dict | None:
    with _OPEN_LOCK:
        record = _OPEN_SESSIONS.get(profile.name)
    if not record:
        return None
    if record.get("context_id") and not _context_alive(record["context_id"]):
        with _OPEN_LOCK:
            _OPEN_SESSIONS.pop(profile.name, None)
        return None
    return dict(record)


def _context_alive(context_id: str | None) -> bool:
    if not context_id:
        return False
    try:
        manager.get_context(context_id)
        return True
    except ToolError:
        return False


def _find_tab(tab_id: str | None):
    if not tab_id:
        return None
    try:
        tab, _ = manager.get_tab(tab_id)
        return tab
    except ToolError:
        return None


def _session_result(
    profile: Profile,
    record: dict,
    tab,
    *,
    reused: bool,
    login: AuthResult | None = None,
) -> ProfileSession:
    url = getattr(tab, "url", None)
    title = None
    try:
        title = tab.title
    except Exception:
        pass
    if login is not None:
        logged_in = bool(login.ok)
    else:
        logged_in = _cached_login(profile) is not None and not _landed_on_login(url)
    return ProfileSession(
        profile=profile.name,
        browser_id=str(record.get("browser_id") or ""),
        context_id=str(record.get("context_id") or ""),
        tab_id=str(record.get("tab_id") or ""),
        url=url,
        title=title,
        logged_in=logged_in,
        reused=reused,
        login=login,
    )


__all__ = ["mcp"]
