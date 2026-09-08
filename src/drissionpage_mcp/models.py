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


class ElementListResult(BaseModel):
    count: int
    elements: list[ElementSummary]


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
