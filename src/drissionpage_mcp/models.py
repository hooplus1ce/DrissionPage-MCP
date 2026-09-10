"""MCP 工具的输出模型。

省 token 约定：Field description 只保留「模型无法从字段名推断」的语义
（枚举取值、空值语义、令牌/密码不下发、截断可恢复等），不复述字段名。
这些描述会逐字进入每个引用它的工具的 outputSchema，重复成本很高。
"""

from typing import Any

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
        description="被截断的字段名（默认截断省 token；null 表示无截断，full=True 可恢复）",
    )


class ElementListResult(BaseModel):
    count: int
    elements: list[ElementSummary]
    total: int | None = Field(default=None, description="匹配总数；大于 count 即被 limit 截断")
    truncated: bool | None = Field(default=None, description="是否被 limit 截断；未截断为 null")


class ContextInfo(BaseModel):
    context_id: str
    browser_id: str
    tab_ids: list[str] = []


class CookieList(BaseModel):
    count: int
    cookies: list[dict]


class ToastResult(BaseModel):
    message_texts: list[str] = Field(description="message 全局提示气泡文本")
    notification_texts: list[str] = Field(description="notification 通知卡片文本")


class WaitResult(BaseModel):
    found: bool
    tab_id: str
    locator: str
    elapsed_hint: str | None = None


class MessageResult(BaseModel):
    ok: bool
    message: str


class FrameInfo(BaseModel):
    frame_index: int = Field(description="序号，从 1 开始，可作 frame 参数")
    iframe_id: str | None = None
    name: str | None = None
    src: str | None = None
    displayed: bool = Field(description="可见的 iframe 即激活态功能模块")


class ActionStep(BaseModel):
    """action_chain 的单步操作。"""

    action: str
    element_id: str | None = None
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
    role: str | None = Field(default=None, description="业务角色标签（TOML 可选）")
    admin_url: str | None = None
    login_page: str | None = None
    cookie_domain: str | None = None
    has_password: bool = Field(description="是否已配置密码（密码本身不下发）")
    source: str = Field(description="配置来源：env / 文件")


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
        default=False, description="需验证码：先 auth_captcha 看图，再带 captcha_code 调用"
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


class MessageMatchResult(BaseModel):
    """全局消息/弹层断言结果。"""

    found: bool
    pattern: str
    matched_text: str | None = None
    source: str | None = Field(default=None, description="message / notification / layer")
    level: str | None = Field(default=None, description="success / error / warning / info")
    elapsed_seconds: float = 0.0
    all_messages: list[str] = Field(default_factory=list, description="轮询期间捕捉到的全部消息文本")


class ScenarioStepResult(BaseModel):
    """场景执行的单步结果。"""

    index: int
    step: str
    tool: str
    ok: bool
    elapsed_seconds: float = 0.0
    output_summary: str | None = None
    error: str | None = None


class ScenarioRunResult(BaseModel):
    """声明式场景回归运行结果。"""

    ok: bool
    name: str
    total_steps: int
    passed_steps: int
    elapsed_seconds: float = 0.0
    failed_step: str | None = None
    steps: list[ScenarioStepResult] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)


class PacketInfo(BaseModel):
    """监听到的一条数据包（HTTP / WebSocket / SSE）。"""

    index: int
    kind: str = Field(description="request=HTTP 请求；ws=WebSocket 帧；sse=服务器推送事件")
    tab_id: str | None = None
    method: str | None = None
    url: str | None = None
    status: int | str | None = Field(default=None, description="HTTP 状态；ws/sse 无此字段")
    failed: bool = False
    fail_reason: str | None = None
    resource_type: str | None = None
    direction: str | None = Field(default=None, description="ws 帧方向：sent=上行 / received=下行")
    params: dict | None = Field(default=None, description="url 查询参数")
    post_data: Any = Field(default=None, description="POST 载荷（json 自动转 dict）")
    body: Any = Field(default=None, description="响应体（include_body=True 时返回）")
    headers: dict | None = Field(default=None, description="响应头（include_headers=True 时返回）")


class NetListenState(BaseModel):
    """监听器状态快照。"""

    tab_id: str
    listening: bool
    targets: list[str] | None = Field(default=None, description="url 监听特征；null 表示全部")
    methods: list[str] | None = Field(default=None, description="监听的请求方法；null 表示全部")
    res_types: list[str] | None = Field(default=None, description="监听的 ResourceType；null 表示全部")
    pending: int | None = Field(default=None, description="队列中尚未取走的数据包数")
    note: str | None = None


class NetPackets(BaseModel):
    """数据包读取结果。"""

    found: bool = Field(description="是否满足本次读取期望（未超时且取到包）")
    count: int = 0
    elapsed: float = 0.0
    packets: list[PacketInfo] = Field(default_factory=list)
    pending: int | None = Field(default=None, description="读取后队列剩余数据包数")
    note: str | None = None


class NetIdleResult(BaseModel):
    """网络静默等待结果。"""

    quiet: bool = Field(description="是否在超时前等到网络静默")
    elapsed: float = 0.0
    pending: int | None = None
    note: str | None = None
