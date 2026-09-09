"""MCP 工具的输出模型。"""

from pydantic import BaseModel, Field


class BrowserInfo(BaseModel):
    browser_id: str
    address: str
    kind: str = Field(description="launched=本服务启动, connected=接管已有浏览器")
    is_alive: bool
    is_headless: bool | None = None
    tab_ids: list[str] = []
    context_ids: list[str] = []


class TabInfo(BaseModel):
    tab_id: str
    browser_id: str | None = None
    context_id: str | None = None
    url: str | None = None
    title: str | None = None
    ready_state: str | None = None


class NavInfo(BaseModel):
    url: str | None = None
    status: int | str | None = None
    ok: bool
    message: str | None = None


class PageInfo(BaseModel):
    tab_id: str
    url: str | None = None
    title: str | None = None
    ready_state: str | None = None
    user_agent: str | None = None
    breadcrumb: str | None = None
    breadcrumb_items: list[str] = Field(default_factory=list)
    active_frame: dict | None = None

class HtmlResult(BaseModel):
    tab_id: str
    url: str | None = None
    html: str
    truncated: bool = False


class ElementSummary(BaseModel):
    element_id: str
    tag: str | None = None
    text: str | None = None
    css_selector: str | None = None
    xpath: str | None = None


class ElementDetail(ElementSummary):
    inner_html: str | None = None
    attrs: dict[str, str] = {}
    value: str | None = None
    link: str | None = None
    rect: dict[str, float] | None = None
    # 非复选框元素的 is_checked 等状态为 None，因此值允许为空
    states: dict[str, bool | None] | None = None
    truncated_fields: list[str] | None = Field(
        default=None,
        description="被截断的字段名列表（省 token 默认截断；无截断时为 null，full=True 可恢复）",
    )


class ElementListResult(BaseModel):
    count: int
    elements: list[ElementSummary]
    total: int | None = Field(
        default=None, description="匹配元素总数；大于 count 时说明被 limit 截断"
    )
    truncated: bool | None = Field(
        default=None, description="是否因 limit 截断；未截断时为 null"
    )


class ContextInfo(BaseModel):
    context_id: str
    browser_id: str
    tab_ids: list[str] = []


class CookieList(BaseModel):
    count: int
    cookies: list[dict]


class ToastResult(BaseModel):
    message_texts: list[str] = Field(description="ant message 全局提示气泡文本")
    notification_texts: list[str] = Field(description="ant notification 通知提醒框文本")


class WaitResult(BaseModel):
    found: bool
    tab_id: str
    locator: str
    elapsed_hint: str | None = None


class MessageResult(BaseModel):
    ok: bool
    message: str


class FrameInfo(BaseModel):
    frame_index: int = Field(description="序号，从 1 开始，可用于 frame 参数")
    iframe_id: str | None = None
    name: str | None = None
    src: str | None = None
    displayed: bool = Field(description="可见的 iframe 即激活态功能模块")


class ActionStep(BaseModel):
    """action_chain 的单步操作。"""

    action: str = Field(
        description=(
            "move_to(移到元素/坐标), move(相对移动), click, r_click, m_click, hold(按下), "
            "release(松开), scroll(滚动), type(输入文本), key_down, key_up, wait(等待)"
        )
    )
    element_id: str | None = Field(default=None, description="move_to/click/r_click/m_click/hold/release/scroll 的目标元素")
    x: float | None = None
    y: float | None = None
    offset_x: float | None = None
    offset_y: float | None = None
    times: int | None = None
    delta_y: float | None = None
    delta_x: float | None = None
    text: str | None = None
    interval: float | None = None
    key: str | None = None
    duration: float | None = None
    seconds: float | None = None


class ActionChainResult(BaseModel):
    ok: bool
    steps_executed: int
    actions: list[str]


class ProfileInfo(BaseModel):
    """档案元信息（永不含密码）。"""

    name: str
    username: str | None = None
    role: str | None = Field(default=None, description="TOML 可选的业务角色标签")
    admin_url: str | None = None
    login_page: str | None = None
    cookie_domain: str | None = None
    has_password: bool = Field(description="是否已配置密码（密码本身不下发）")
    source: str = Field(description="配置来源：env / file:profiles.toml / file+env")


class AuthResult(BaseModel):
    ok: bool
    profile: str
    source: str = Field(
        default="http",
        description="http=本次实时登录, session=复用了缓存的登录态",
    )
    message: str | None = None
    cookie_count: int = 0
    cookie_names: list[str] = Field(default_factory=list)
    has_token: bool = Field(default=False, description="是否已获取访问令牌（令牌值不下发）")
    captcha_required: bool = Field(
        default=False, description="需要验证码：先 auth_captcha 看图，再带 captcha_code 调用"
    )
    captcha_id: str | None = None
    update_pwd: bool = Field(default=False, description="服务端要求修改初始密码")
    tab_id: str | None = None
    url: str | None = None
    ready_state: str | None = None


class ProfileSession(BaseModel):
    """一个已打开的多账号档案会话（独立 BrowserContext）。"""

    profile: str
    browser_id: str
    context_id: str
    tab_id: str
    url: str | None = None
    title: str | None = None
    logged_in: bool = False
    reused: bool = Field(default=False, description="是否复用了已打开的档案会话")
    login: AuthResult | None = None


class NavMenuResult(BaseModel):
    """模块菜单导航结果。"""

    ok: bool
    menu_name: str
    tab_id: str
    breadcrumb: str | None = None
    breadcrumb_items: list[str] = Field(default_factory=list)
    active_frame: dict | None = None
    reused_tab: bool = False
    message: str | None = None
