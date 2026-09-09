"""APS 平台登录 HTTP 引擎（纯标准库，不引入 OCR 依赖）。

验证码识别交给多模态模型：``fetch_captcha`` 只负责取图并保留会话 cookie，
模型读出字符后由 ``submit`` 提交登录（JSON 体，返回访问令牌 + 会话 cookies）。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from time import time

from fastmcp.exceptions import ToolError

from .profiles import Profile

# 登录失败且提示语包含这些词时，判定为验证码识别错误（可换一张重试）
_CAPTCHA_HINTS = ("验证码", "校验码", "captcha", "vcode", "code")


@dataclass
class Challenge:
    """一次验证码挑战：图片 + 与之绑定的登录会话。"""

    profile: str
    captcha_id: str
    image: bytes
    content_type: str
    created_at: float
    jar: http.cookiejar.CookieJar = field(repr=False)


@dataclass
class LoginOutcome:
    ok: bool
    message: str = ""
    update_pwd: bool = False
    cookies: list[dict] = field(default_factory=list)
    token: str | None = None
    raw: dict = field(default_factory=dict)
    captcha_error: bool = False


def cookies_from_jar(
    jar: http.cookiejar.CookieJar, default_domain: str | None = None
) -> list[dict]:
    """把 cookie jar 转成 DrissionPage ``tab.set.cookies`` 可用的字典列表。"""
    out: list[dict] = []
    for cookie in jar:
        if not cookie.name or cookie.value is None:
            continue
        domain = cookie.domain or default_domain
        if not domain:
            continue
        item: dict = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": domain,
            "path": cookie.path or "/",
        }
        if cookie.secure:
            item["secure"] = True
        if cookie.expires:
            item["expires"] = int(cookie.expires)
        if cookie.has_nonstandard_attr("HttpOnly"):
            item["httpOnly"] = True
        out.append(item)
    return out


class LoginEngine:
    """按档案配置执行「取验证码 → 提交登录」两段式 HTTP 流程。"""

    def __init__(self, profile: Profile):
        self.profile = profile

    # ---------- 传输层 ----------

    def _opener(self, jar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
        handlers: list = [urllib.request.HTTPCookieProcessor(jar)]
        proxy = urllib.parse.urlsplit(_proxy() or "")
        if proxy.scheme and proxy.netloc:
            handlers.append(
                urllib.request.ProxyHandler({"http": _proxy(), "https": _proxy()})
            )
        if self.profile.origin.startswith("https") and not self.profile.verify_ssl:
            handlers.append(
                urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
            )
        return urllib.request.build_opener(*handlers)

    def _request(
        self,
        opener: urllib.request.OpenerDirector,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        request = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with opener.open(request, timeout=self.profile.timeout) as resp:
                return resp.status, resp.read(), resp.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            raise ToolError(
                f"登录请求失败 HTTP {exc.code}: {url} | {body[:200].decode('utf-8', 'replace')}"
            ) from None
        except urllib.error.URLError as exc:
            raise ToolError(f"登录请求无法连接 {url}: {exc.reason}") from None

    # ---------- 两段式流程 ----------

    def fetch_captcha(self) -> Challenge:
        """取验证码图片，并保留与之绑定的登录会话（cookie jar）。"""
        profile = self.profile
        jar = http.cookiejar.CookieJar()
        opener = self._opener(jar)
        url = profile.url(profile.captcha_path) + "?" + urllib.parse.urlencode(
            {"key": profile.captcha_key}
        )
        status, body, content_type = self._request(
            opener,
            url,
            headers={
                "User-Agent": profile.user_agent,
                "Referer": profile.login_page,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        if status != 200 or not body:
            raise ToolError(f"获取验证码失败 HTTP {status}（{content_type}）: {url}")
        if not content_type.startswith("image/"):
            raise ToolError(f"验证码接口未返回图片（{content_type}）: {url}")
        return Challenge(
            profile=profile.name,
            captcha_id=uuid.uuid4().hex[:12],
            image=body,
            content_type=content_type,
            created_at=time(),
            jar=jar,
        )

    def submit(self, challenge: Challenge, code: str) -> LoginOutcome:
        """用模型识别出的验证码提交登录，返回访问令牌与会话 cookies。"""
        profile = self.profile
        code = (code or "").strip()
        if not code:
            raise ToolError("验证码不能为空")

        payload = json.dumps(
            {
                profile.username_field: profile.username,
                profile.password_field: profile.password,
                profile.captcha_field: code,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        opener = self._opener(challenge.jar)
        status, body, content_type = self._request(
            opener,
            profile.url(profile.login_path),
            data=payload,
            headers={
                "User-Agent": profile.user_agent,
                "Referer": profile.login_page,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json;charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if status != 200:
            raise ToolError(f"登录接口返回 HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise ToolError(
                f"登录接口未返回 JSON（{content_type}）: {body[:200].decode('utf-8', 'replace')}"
            ) from None
        if not isinstance(data, dict):
            raise ToolError(f"登录接口返回结构异常: {str(data)[:200]}")

        ok = bool(data.get(profile.success_field))
        message = str(data.get(profile.message_field) or "").strip()
        cookies = cookies_from_jar(challenge.jar, profile.access_domain) if ok else []
        token = None
        if ok:
            value = data.get(profile.token_field)
            token = str(value).strip() if value is not None else ""
            if not token:
                raise ToolError(
                    f"登录成功但未在响应字段 [{profile.token_field}] 取到访问令牌: "
                    f"{str(data)[:200]}"
                )
        return LoginOutcome(
            ok=ok,
            message=message,
            update_pwd=bool(data.get("updatePwd")),
            cookies=cookies,
            token=token,
            raw={k: v for k, v in data.items() if k not in (profile.token_field,)},
            captcha_error=(not ok) and _looks_like_captcha_error(message),
        )


def _looks_like_captcha_error(message: str) -> bool:
    if not message:
        # 空提示语通常是验证码错误（服务端不回显）
        return True
    lowered = message.lower()
    return any(hint in message or hint in lowered for hint in _CAPTCHA_HINTS)


def _proxy() -> str | None:
    value = os.environ.get("HL_PROXY", "").strip()
    return value or None


__all__ = ["Challenge", "LoginEngine", "LoginOutcome", "cookies_from_jar"]
