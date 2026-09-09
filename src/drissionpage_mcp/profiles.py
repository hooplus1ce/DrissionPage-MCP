"""账号档案（Profile）注册表。

凭据只驻留服务端：工具按档案名取用，任何返回值都不含密码。

配置来源（后者覆盖前者）：
1. ``HL_HOST_PREFIX`` 推导：``demo18`` → ``demo18-scm.hoolinks.com`` 及 APS URL 模板
2. ``HL_*`` 单档环境变量（与 .env 注释中的契约一致）
3. ``HL_PROFILES_FILE`` 指向的 TOML（多档案，供多角色并行登录）

TOML 示例::

    [profiles.aps]
    host_prefix = "demo18"
    username = "hooplus1ce"
    password = "..."
    role = "APS 管理员"

    [profiles.aps_approver]
    base_url = "https://demo18-scm.hoolinks.com"
    admin_url = "https://demo18-scm.hoolinks.com/static/admin/"
    username = "hooplus1cer"
    password = "..."
    role = "审批人"

扁平写法（无 ``[profiles]`` 外层表）同样支持。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastmcp.exceptions import ToolError

# 默认 UA（与真实浏览器一致）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HOST_TEMPLATE = "{prefix}-scm.hoolinks.com"
ADMIN_URL_TEMPLATE = "https://{host}/static/admin/"
LOGIN_PAGE_TEMPLATE = "https://{host}/static/admin/login"
DEFAULT_PROFILES_FILE = "profiles.toml"

# 工具接受的档案字段（用于 TOML 与环境变量）
_FIELDS = (
    "host_prefix",
    "base_url",
    "admin_url",
    "login_page",
    "cookie_domain",
    "access_domain",
    "api_prefix",
    "username",
    "password",
    "role",
    "captcha_path",
    "captcha_key",
    "login_path",
    "username_field",
    "password_field",
    "captcha_field",
    "success_field",
    "message_field",
    "token_field",
    "token_store",
    "user_agent",
    "timeout",
    "verify_ssl",
)


@dataclass(frozen=True)
class Profile:
    """一个可登录的账号档案（含密码，禁止序列化给客户端）。"""

    name: str
    username: str
    password: str
    origin: str
    admin_url: str
    login_page: str
    cookie_domain: str
    access_domain: str
    api_prefix: str = ""
    role: str | None = None
    captcha_path: str = "/scmpsm/login/validateCode"
    captcha_key: str = "regValidateCode"
    login_path: str = "/scmpsm/login/signin"
    username_field: str = "userName"
    password_field: str = "userPwd"
    captcha_field: str = "vcode"
    success_field: str = "ok"
    message_field: str = "msg"
    token_field: str = "data"
    token_store: str = "HL-Access-Token"
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 20.0
    verify_ssl: bool = True
    source: str = "env"

    def url(self, path: str) -> str:
        """拼接站点根 + 可选的 API 前缀 + 路径。"""
        return f"{self.origin}{self.api_prefix}{path}"


def _as_bool(value: str | bool | None, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _derive(prefix: str) -> dict[str, str]:
    """由环境前缀推导一套 APS URL 模板（每一项都可被显式 HL_* 覆盖）。"""
    host = prefix if "." in prefix else HOST_TEMPLATE.format(prefix=prefix)
    return {
        "origin": f"https://{host}",
        "admin_url": ADMIN_URL_TEMPLATE.format(host=host),
        "login_page": LOGIN_PAGE_TEMPLATE.format(host=host),
        "cookie_domain": f".{host}",
        "access_domain": host,
    }


def _split_base(base_url: str | None) -> tuple[str | None, str | None]:
    """把 HL_BASE_URL 拆成 origin 与 API 前缀（带路径时视为前缀）。"""
    if not base_url:
        return None, None
    parts = urlsplit(base_url.strip())
    if not parts.scheme or not parts.netloc:
        raise ToolError(f"HL_BASE_URL 不是合法 URL: {base_url!r}")
    origin = f"{parts.scheme}://{parts.netloc}"
    prefix = parts.path.rstrip("/")
    return origin, prefix or None


def _merge(base: dict, overlay: dict) -> dict:
    """overlay 中显式提供的字段覆盖 base。"""
    out = dict(base)
    for key, value in overlay.items():
        if value is not None:
            out[key] = value
    return out


def _build(name: str, raw: dict, source: str) -> Profile:
    """把原始字段（环境变量或 TOML）补齐为完整档案。"""
    raw = {k: v for k, v in raw.items() if v not in (None, "")}
    prefix = str(raw.get("host_prefix") or "").strip()
    derived = _derive(prefix) if prefix else {}

    origin, base_prefix = _split_base(raw.get("base_url"))
    origin = origin or derived.get("origin")
    if not origin:
        raise ToolError(
            f"档案 [{name}] 缺少站点地址：请设置 HL_BASE_URL / HL_HOST_PREFIX（或 TOML 的 base_url）"
        )

    host = urlsplit(origin).hostname or ""
    admin_url = str(raw.get("admin_url") or derived.get("admin_url") or f"{origin}/")
    login_page = str(raw.get("login_page") or derived.get("login_page") or f"{origin}/")
    cookie_domain = str(raw.get("cookie_domain") or derived.get("cookie_domain") or f".{host}")
    access_domain = str(raw.get("access_domain") or derived.get("access_domain") or host)

    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    if not username or not password:
        raise ToolError(
            f"档案 [{name}] 缺少凭据：请设置 HL_USERNAME / HL_USERPWD（或 TOML 的 username / password）"
        )

    try:
        timeout = float(raw.get("timeout") or 0) or 20.0
    except (TypeError, ValueError):
        raise ToolError(f"档案 [{name}] 的 timeout 不是数字: {raw.get('timeout')!r}") from None

    return Profile(
        name=name,
        username=username,
        password=password,
        origin=origin,
        admin_url=admin_url,
        login_page=login_page,
        cookie_domain=cookie_domain,
        access_domain=access_domain,
        api_prefix=str(raw.get("api_prefix") or base_prefix or ""),
        role=str(raw["role"]) if raw.get("role") else None,
        captcha_path=str(raw.get("captcha_path") or "/scmpsm/login/validateCode"),
        captcha_key=str(raw.get("captcha_key") or "regValidateCode"),
        login_path=str(raw.get("login_path") or "/scmpsm/login/signin"),
        username_field=str(raw.get("username_field") or "userName"),
        password_field=str(raw.get("password_field") or "userPwd"),
        captcha_field=str(raw.get("captcha_field") or "vcode"),
        success_field=str(raw.get("success_field") or "ok"),
        message_field=str(raw.get("message_field") or "msg"),
        token_field=str(raw.get("token_field") or "data"),
        token_store=str(raw.get("token_store") or "HL-Access-Token"),
        user_agent=str(raw.get("user_agent") or DEFAULT_USER_AGENT),
        timeout=timeout,
        verify_ssl=_as_bool(raw.get("verify_ssl"), True),
        source=source,
    )


# .env 契约中的历史命名（HL_USERPWD / HL_URL）优先于字段名派生的键
_ENV_ALIASES = {
    "password": ("HL_USERPWD", "HL_PASSWORD"),
    "admin_url": ("HL_URL", "HL_ADMIN_URL"),
}


def _from_env(env: dict[str, str]) -> dict[str, dict]:
    """读取 HL_* 单档环境变量；无任何 HL_ 配置时返回空。"""
    raw = {f: env.get(f"HL_{f.upper()}") for f in _FIELDS}
    for field, keys in _ENV_ALIASES.items():
        raw[field] = next((env[k] for k in keys if env.get(k)), raw.get(field))
    if not any(v for v in raw.values()):
        return {}
    name = env.get("HL_PROFILE") or "default"
    raw["timeout"] = env.get("HL_REFRESH_HTTP_TIMEOUT") or raw.get("timeout")
    return {name: raw}


def _from_file(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"档案文件解析失败 {path}: {exc}") from None
    table = data.get("profiles", data)
    if not isinstance(table, dict):
        raise ToolError(f"档案文件格式错误 {path}: 顶层应为表")
    out: dict[str, dict] = {}
    for name, entry in table.items():
        if isinstance(entry, dict):
            out[str(name)] = {k: v for k, v in entry.items() if k in _FIELDS}
    return out


def profiles_file() -> Path | None:
    """档案文件路径：HL_PROFILES_FILE 优先，否则回退当前目录 profiles.toml。"""
    explicit = os.environ.get("HL_PROFILES_FILE")
    if explicit:
        return Path(explicit).expanduser()
    default = Path(DEFAULT_PROFILES_FILE)
    return default if default.is_file() else None


def load_profiles(env: dict[str, str] | None = None) -> dict[str, Profile]:
    """加载全部档案（TOML 为基础，HL_* 环境变量覆盖同名档案）。"""
    env = dict(os.environ if env is None else env)
    merged: dict[str, dict] = {}
    sources: dict[str, str] = {}

    path = profiles_file()
    if path is not None:
        for name, entry in _from_file(path).items():
            merged[name] = dict(entry)
            sources[name] = f"file:{path.name}"

    for name, entry in _from_env(env).items():
        if name in merged:
            # 环境变量优先级更高：仅覆盖显式提供的字段
            merged[name] = _merge(merged[name], entry)
            sources[name] = f"{sources[name]}+env"
        else:
            merged[name] = entry
            sources[name] = "env"

    return {name: _build(name, raw, sources[name]) for name, raw in merged.items()}


def list_profile_names(profiles: dict[str, Profile]) -> list[str]:
    return sorted(profiles)


def get_profile(name: str, profiles: dict[str, Profile]) -> Profile:
    if name not in profiles:
        available = "、".join(list_profile_names(profiles)) or "（无）"
        raise ToolError(f"未找到档案 [{name}]，可用档案：{available}（见 profile_list）")
    return profiles[name]


__all__ = [
    "DEFAULT_USER_AGENT",
    "Profile",
    "get_profile",
    "list_profile_names",
    "load_profiles",
    "profiles_file",
]
