"""测试夹具：用假对象替代 DrissionPage，避免测试时启动真实浏览器。"""

from __future__ import annotations

import itertools
import os

import pytest
from fastmcp import Client

from drissionpage_mcp.manager import BrowserManager, BrowserSession
from drissionpage_mcp.server import mcp

# 跨 FakeChromium 实例唯一的 tab id 序列，模拟真实环境的 tab_id 全局唯一性
_tab_seq = itertools.count(1)


class FakeStates:
    def __init__(self, owner):
        self._owner = owner

    @property
    def is_alive(self):
        return self._owner.alive

    @property
    def is_headless(self):
        return self._owner.headless

    @property
    def ready_state(self):
        return self._owner.ready_state

    # 元素状态（tab 复用此类时不会用到）
    @property
    def is_displayed(self):
        return getattr(self._owner, "displayed", True)

    @property
    def is_enabled(self):
        return True

    @property
    def is_checked(self):
        return False

    @property
    def is_in_viewport(self):
        return True


class FakeWait:
    def __init__(self, tab: "FakeTab"):
        self._tab = tab

    def eles_loaded(self, locator, timeout=None, **kwargs):
        self._tab.steps.append(("wait_eles_loaded", locator))
        return self._tab.wait_result

    def __call__(self, seconds=0, **kwargs):
        self._tab.steps.append(("wait", seconds))
        return self


class FakeSet:
    """模拟 tab.set 命名空间中与 cookies 相关的部分。

    真实 API 中 set.cookies 既是可调用对象又有 .clear() 方法，这里保持一致。
    """

    class _CookieSetter:
        def __init__(self, tab: "FakeTab"):
            self._tab = tab

        def __call__(self, cookies):
            self._tab.steps.append(("set_cookies", cookies))
            self._tab.cookies_value.extend(cookies)

        def clear(self):
            self._tab.steps.append(("clear_cookies",))
            self._tab.cookies_value.clear()

    def __init__(self, tab: "FakeTab"):
        self.cookies = FakeSet._CookieSetter(tab)


class FakeTab:
    def __init__(self, tab_id: str, url: str = "about:blank", title: str = ""):
        self.tab_id = tab_id
        self.url = url
        self.title = title
        self.alive = True
        self.headless = False
        self.ready_state = "complete"
        self.states = FakeStates(self)
        self.user_agent = "fake-ua"
        self.html = "<html><body>fake</body></html>"
        self.cookies_value: list[dict] = []
        self.ele_result = None
        self.ele_queue: list = []
        self.ele_results: dict = {}
        self.eles_result: list = []
        self.eles_results: dict = {}
        self.steps: list[tuple] = []
        self.wait_result = True
        self.wait = FakeWait(self)
        self.set = FakeSet(self)
        self.iframes: list[FakeFrame] = []
        self._action_chain = FakeActions(self)

    @property
    def actions(self):
        return self._action_chain

    def get(self, url, timeout=None, **kwargs):
        self.steps.append(("get", url))
        self.url = url
        return FakeNav(url)

    def run_js(self, script, *args, **kwargs):
        self.steps.append(("run_js", script))
        return None

    def back(self, steps=1):
        self.steps.append(("back", steps))

    def forward(self, steps=1):
        self.steps.append(("forward", steps))

    def refresh(self, ignore_cache=False):
        self.steps.append(("refresh", ignore_cache))

    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None, full_page=False, **kwargs):
        self.steps.append(("get_screenshot", full_page))
        data = b"\x89PNG-fake-tab-screenshot"
        if path:
            from pathlib import Path
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        if as_bytes:
            return data
        if as_base64:
            import base64
            return base64.b64encode(data).decode()
        return str(path)

    def ele(self, locator, index=1, timeout=None):
        self.steps.append(("ele", locator))
        if isinstance(getattr(self, "ele_results", None), dict) and locator in self.ele_results:
            return self.ele_results[locator]
        if self.ele_queue:
            return self.ele_queue.pop(0)
        return self.ele_result

    def eles(self, locator, timeout=None):
        self.steps.append(("eles", locator))
        if locator == "tag:iframe":
            return list(self.iframes)
        if isinstance(self.eles_results, dict) and locator in self.eles_results:
            return list(self.eles_results[locator])
        if self.ele_result:
            return [self.ele_result]
        return list(self.eles_result) if self.eles_result else []

    def close(self, others=False):
        self.steps.append(("close", others))

    def run_js(self, script, *args, as_expr=False, **kwargs):
        self.steps.append(("run_js", script))
        return "js-ok"

    def cookies(self, all_info=False, **kwargs):
        return list(self.cookies_value)


class FakeNav:
    def __init__(self, url: str, ok: bool = True, status: int = 200):
        self.url = url
        self.status = status
        self.ok = ok
        self.headers = {}
        self.request = {}


class FakeElement:
    """足够覆盖 element 工具的假元素。"""

    def __init__(self, tag: str = "button", text: str = "确定"):
        self.tag = tag
        self.text = text
        self.inner_html = f"<{tag}>{text}</{tag}>"
        self.attrs = {"id": "btn1", "class": "btn"}
        self.value = None
        self.link = None
        self.alive = True
        self.states = FakeStates(self)
        self._rect_loc = (100, 200)
        self._rect_size = (80, 30)
        self.select_result = True
        self.actions: list[tuple] = []
        self.ele_result = None
        self.ele_queue = []
        self.eles_results = {}

    def attr(self, name):
        return self.attrs.get(name)

    def ele(self, locator, index=1, timeout=None):
        self.actions.append(("ele", locator))
        if self.ele_queue:
            return self.ele_queue.pop(0)
        return self.ele_result

    def eles(self, locator, timeout=None):
        self.actions.append(("eles", locator))
        if locator in self.eles_results:
            return list(self.eles_results[locator])
        if self.ele_result:
            return [self.ele_result]
        return []

    @property
    def css_selector(self):
        return "css-path"

    @property
    def xpath(self):
        return "/html/body/button"

    @property
    def rect(self):
        return self

    @property
    def location(self):
        return self._rect_loc

    @property
    def size(self):
        return self._rect_size

    @property
    def midpoint(self):
        return (self._rect_loc[0] + self._rect_size[0] / 2, self._rect_loc[1] + self._rect_size[1] / 2)
    @property
    def viewport_location(self):
        return self.location

    @property
    def viewport_midpoint(self):
        return self.midpoint

    def click(self, by_js=False, **kwargs):
        self.actions.append(("click", by_js))

    def input(self, vals, clear=False, by_js=False, **kwargs):
        self.actions.append(("input", vals, clear, by_js))

    def hover(self, **kwargs):
        self.actions.append(("hover",))

    def check(self, checked=True, **kwargs):
        self.actions.append(("check", checked))

    @property
    def scroll(self):
        return self

    def down(self, pixel):
        self.actions.append(("scroll_down", pixel))

    def up(self, pixel):
        self.actions.append(("scroll_up", pixel))

    def to_top(self):
        self.actions.append(("scroll_to_top",))

    def to_bottom(self):
        self.actions.append(("scroll_to_bottom",))

    def to_see(self, center=None):
        self.actions.append(("scroll_to_see",))

    @property
    def select(self):
        return self

    def by_text(self, text):
        return self.select_result

    def by_value(self, value):
        return self.select_result

    def by_index(self, index):
        return self.select_result

    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None, **kwargs):
        self.actions.append(("get_screenshot",))
        data = b"\x89PNG-fake-ele-screenshot"
        if path:
            from pathlib import Path
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        if as_bytes:
            return data
        if as_base64:
            import base64
            return base64.b64encode(data).decode()
        return str(path)


class FakeActions:
    """记录 tab.actions 调用序列。"""

    def __init__(self, tab: "FakeTab"):
        self._tab = tab
        self.calls: list[tuple] = []

    def move_to(self, ele_or_loc, offset_x=None, offset_y=None, duration=0.5):
        target = ele_or_loc if isinstance(ele_or_loc, (tuple, list)) else getattr(ele_or_loc, "tag", "?")
        self.calls.append(("move_to", target, offset_x, offset_y))
        return self

    def move(self, offset_x=0, offset_y=0, duration=0.5):
        self.calls.append(("move", offset_x, offset_y))
        return self

    def click(self, on_ele=None, times=1):
        self.calls.append(("click", on_ele, times))
        return self

    def r_click(self, on_ele=None, times=1):
        self.calls.append(("r_click", on_ele, times))
        return self

    def m_click(self, on_ele=None, times=1):
        self.calls.append(("m_click", on_ele, times))
        return self

    def hold(self, on_ele=None):
        self.calls.append(("hold", on_ele))
        return self

    def release(self, on_ele=None):
        self.calls.append(("release", on_ele))
        return self

    def scroll(self, delta_y=0, delta_x=0, on_ele=None):
        self.calls.append(("scroll", delta_y, delta_x))
        return self

    def type(self, keys, interval=0):
        self.calls.append(("type", keys, interval))
        return self

    def key_down(self, key):
        self.calls.append(("key_down", key))
        return self

    def key_up(self, key):
        self.calls.append(("key_up", key))
        return self

    def wait(self, second, scope=None):
        self.calls.append(("wait", second))
        return self


class FakeFrame:
    """模拟 ChromiumFrame：可搜索、可见性、id/name 属性。"""

    def __init__(self, iframe_id: str, name: str = "", displayed: bool = True):
        self.iframe_id = iframe_id
        self._name = name
        self.displayed = displayed
        self.alive = True
        self.states = FakeStates(self)
        self.ele_result = None
        self.eles_results: dict[str, list] = {}
        self.actions: list[tuple] = []

    def attr(self, name):
        if name == "id":
            return self.iframe_id
        if name == "name":
            return self._name
        if name == "src":
            return f"https://frame.local/{self.iframe_id}"
        return None

    @property
    def rect(self):
        return self

    @property
    def location(self):
        return (0, 0)

    @property
    def size(self):
        return (100, 100)

    def ele(self, locator, index=1, timeout=None):
        self.actions.append(("ele", locator))
        return self.ele_result

    def eles(self, locator, timeout=None):
        self.actions.append(("eles", locator))
        if locator in self.eles_results:
            return list(self.eles_results[locator])
        return [self.ele_result] if self.ele_result else []

    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None, **kwargs):
        self.actions.append(("get_screenshot",))
        data = b"\x89PNG-fake-frame-screenshot"
        if path:
            from pathlib import Path
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        if as_bytes:
            return data
        if as_base64:
            import base64
            return base64.b64encode(data).decode()
        return str(path)


def _install_wait(tab: FakeTab, wait_result: bool = True):
    tab.wait_result = wait_result
    tab.wait = FakeWait(tab)
    tab.set = FakeSet(tab)


class FakeContext:
    def __init__(self, browser: "FakeChromium"):
        self._browser = browser
        self.tab_ids = [f"ctx-tab-{next(_tab_seq)}"]
        browser.context_tabs.update(dict.fromkeys(self.tab_ids))

    def new_tab(self, url=None, background=False, **kwargs):
        tab = FakeTab(self.tab_ids[0], url=url or "about:blank")
        self._browser.tabs[self.tab_ids[0]] = tab
        return tab

    def close(self):
        for tid in self.tab_ids:
            self._browser.context_tabs.pop(tid, None)
            self._browser.tabs.pop(tid, None)


class FakeChromium:
    def __init__(self, address: str = "127.0.0.1:9333"):
        self.address = address
        self.alive = True
        self.headless = False
        self.tabs: dict[str, FakeTab] = {}
        self.context_tabs: dict[str, None] = {}
        self.states = FakeStates(self)
        self.actions: list[tuple] = []
        self._n = 0

    @property
    def tab_ids(self):
        return list(self.tabs) + list(self.context_tabs)

    @property
    def latest_tab(self):
        if not self.tabs:
            self.new_tab()
        return list(self.tabs.values())[-1]

    def get_tab(self, tab_id=None, **kwargs):
        return self.tabs[tab_id]

    def new_tab(self, url=None, background=False, **kwargs):
        tab = FakeTab(f"tab-{next(_tab_seq)}", url=url or "about:blank")
        self.tabs[tab.tab_id] = tab
        return tab

    def new_context(self, **kwargs):
        return FakeContext(self)

    def quit(self, timeout=5, force=False, **kwargs):
        self.actions.append(("quit", force))
        self.alive = False

    def disconnect(self):
        self.actions.append(("disconnect",))


def make_session(kind: str = "launched") -> tuple[BrowserSession, FakeChromium]:
    chromium = FakeChromium()
    session = BrowserSession(chromium, kind)
    return session, chromium


@pytest.fixture
def fresh_manager():
    """独立的 BrowserManager，不污染服务端单例。"""
    return BrowserManager()


@pytest.fixture
def seeded_manager():
    """向服务端全局 manager 实例注入一个假浏览器会话，测试结束后清空。"""
    session, chromium = make_session()
    tab = chromium.new_tab(url="https://example.com/")
    tab.title = "Example"
    _install_wait(tab)
    # 模拟 APS 的 iframe 功能模块结构：一个隐藏(旧模块) + 一个激活态
    tab.iframes = [
        FakeFrame("react_iframe_111", name="111", displayed=False),
        FakeFrame("react_iframe_222", name="222", displayed=True),
    ]
    from drissionpage_mcp.manager import manager as global_manager

    global_manager._sessions[session.browser_id] = session
    yield session, chromium, tab
    global_manager._sessions.clear()
    global_manager._elements.clear()
    global_manager._element_order.clear()


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


@pytest.fixture
def run_smoke():
    """真浏览器冒烟测试门控：设置环境变量 DPMCP_SMOKE=1 才执行。"""
    if os.environ.get("DPMCP_SMOKE") != "1":
        pytest.skip("设置 DPMCP_SMOKE=1 以运行真浏览器冒烟测试")
    return True
