"""浏览器会话与元素注册表。

DrissionPage 的 Chromium / Tab / 元素对象都依赖长驻的 CDP 连接，
MCP 工具之间通过 browser_id / context_id / tab_id / element_id 引用它们。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from fastmcp.exceptions import ToolError

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage._browsers.chromium import Chromium as _DPChromium

from .models import BrowserInfo, ContextInfo, FrameInfo, TabInfo


def _patch_dp_tab_ids() -> None:
    """修复 DrissionPage 5.0.0b1 的 _tab_ids 缺陷。

    原实现对 Target.getTargets 返回的每个 target 先取 i['browserContextId']
    再判断 type，而 service worker / browser 等类型的 target 没有该字段，
    会抛 KeyError（真实浏览器带 PWA/service worker 时必现）。
    这里重排过滤条件并用 .get() 防御取值。上游修复后可移除本补丁。
    """

    def _tab_ids(self, context_id):
        infos = self._run_cdp("Target.getTargets")["targetInfos"]
        tabs = [
            i["targetId"]
            for i in infos
            if i.get("type") in ("page", "webview")
            and i.get("browserContextId") == context_id
            and not str(i.get("url", "")).startswith(
                ("chrome-extension://", "devtools://", "chrome://newtab-footer")
            )
        ]
        if self._ws_only:
            return tabs
        return [
            i["id"]
            for i in self._driver.get(f"http://{self.address}/json").json()
            if i["id"] in tabs
        ]

    _DPChromium._tab_ids = _tab_ids


_patch_dp_tab_ids()

def _patch_dp_convert_argument() -> None:
    """修复 DrissionPage 5.0.0b1 的 convert_argument 缺陷。

    原实现仅支持 (int, float, str, bool, dict)，未处理 None 与 list/tuple/set，
    导致向 run_js 传递 None 或数组参数时抛 TypeError。上游修复后可移除本补丁。
    """
    import DrissionPage._elements.chromium_element as ce

    orig = ce.convert_argument

    def _convert_argument(arg):
        if arg is None:
            return {"value": None}
        if isinstance(arg, (list, tuple, set)):
            return {"value": [_convert_argument(i)["value"] for i in arg]}
        return orig(arg)

    ce.convert_argument = _convert_argument


_patch_dp_convert_argument()

def _patch_dp_show_trail() -> None:
    """将 DrissionPage 的 show_trail 升级为 Windows 11 Dark HD 60FPS 虚拟光标。"""
    try:
        from DrissionPage._units.setter import ChromiumBaseSetter
        from .cursor import ensure_cursor_installed, set_cursor_enabled, hide_cursor

        def show_trail(self, on_off=True):
            tab = getattr(self._owner, "tab", self._owner)
            set_cursor_enabled(on_off)
            if on_off:
                ensure_cursor_installed(tab)
            else:
                hide_cursor(tab)
            return self

        ChromiumBaseSetter.show_trail = show_trail
    except Exception:
        pass


_patch_dp_show_trail()

MAX_ELEMENTS = 1000


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class BrowserSession:
    """一个浏览器实例及其关联的上下文。"""

    def __init__(self, chromium: Chromium, kind: str):
        self.chromium = chromium
        self.kind = kind  # launched / connected
        self.browser_id = _short_id()
        self.contexts: dict[str, object] = {}

    @property
    def address(self) -> str:
        addr = getattr(self.chromium, "address", None)
        return str(addr) if addr else "unknown"

    def info(self) -> BrowserInfo:
        alive = False
        is_headless = None
        try:
            alive = self.chromium.states.is_alive
            is_headless = self.chromium.states.is_headless
        except Exception:
            pass
        return BrowserInfo(
            browser_id=self.browser_id,
            address=self.address,
            kind=self.kind,
            is_alive=bool(alive),
            is_headless=is_headless,
            tab_ids=list(self.chromium.tab_ids),
            context_ids=list(self.contexts),
        )


class ElementRecord:
    def __init__(self, element, tab_id: str, browser_id: str, container=None):
        self.element = element
        self.tab_id = tab_id
        self.browser_id = browser_id
        self.container = container  # 元素所在文档容器：tab(主文档) 或 ChromiumFrame


class BrowserManager:
    """持有所有浏览器会话与元素注册表，线程安全。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, BrowserSession] = {}
        self._elements: dict[str, ElementRecord] = {}
        self._element_order: list[str] = []

    # ---------- 浏览器 ----------

    def launch(
        self,
        browser_path: str | None = None,
        headless: bool = False,
        arguments: list[str] | None = None,
        user_data_path: str | None = None,
        incognito: bool = False,
    ) -> BrowserSession:
        co = ChromiumOptions()
        co.auto_port()
        if browser_path:
            co.set_browser_path(browser_path)
        if user_data_path:
            co.set_user_data_path(user_data_path)
        if headless:
            co.headless(True)
        if incognito:
            co.incognito()
        for arg in arguments or []:
            co.set_argument(arg)
        browser = Chromium(co)
        with self._lock:
            session = BrowserSession(browser, "launched")
            self._sessions[session.browser_id] = session
            return session

    def connect(self, address: str = "127.0.0.1:9222") -> BrowserSession:
        browser = Chromium(address)
        with self._lock:
            # DrissionPage 同地址返回同一 Chromium 对象，避免重复登记
            for s in self._sessions.values():
                if s.chromium is browser:
                    return s
            session = BrowserSession(browser, "connected")
            self._sessions[session.browser_id] = session
            return session

    def get_session(self, browser_id: str | None) -> BrowserSession:
        with self._lock:
            if browser_id is not None:
                session = self._sessions.get(browser_id)
                if session is None:
                    raise ToolError(f"未找到浏览器会话: {browser_id}，可用 browser_status 查看现有会话")
                return session
            alive = [s for s in self._sessions.values() if s.chromium.states.is_alive]
            if len(alive) == 1:
                return alive[0]
            if not alive:
                raise ToolError("当前没有可用的浏览器会话，请先调用 browser_launch 或 browser_connect")
            raise ToolError(
                "存在多个浏览器会话，请通过 browser_id 指定。可用 browser_status 查看会话列表"
            )

    def close_browser(self, browser_id: str, force: bool = False) -> str:
        with self._lock:
            session = self._sessions.pop(browser_id, None)
        if session is None:
            raise ToolError(f"未找到浏览器会话: {browser_id}")
        try:
            if session.kind == "launched":
                session.chromium.quit(force=force)
            else:
                # 接管的浏览器只断开连接，不关闭用户自己的浏览器
                session.chromium.disconnect()
        except Exception:
            pass
        for cid in list(session.contexts):
            session.contexts.pop(cid, None)
        return session.browser_id

    def status(self) -> list[BrowserInfo]:
        with self._lock:
            return [s.info() for s in self._sessions.values()]

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._elements.clear()
            self._element_order.clear()
        for s in sessions:
            try:
                if s.kind == "launched":
                    s.chromium.quit(force=True)
                else:
                    s.chromium.disconnect()
            except Exception:
                pass

    # ---------- Tab ----------

    def get_tab(self, tab_id: str | None, browser_id: str | None = None):
        if tab_id is not None:
            with self._lock:
                if browser_id is not None:
                    sessions = [self._sessions.get(browser_id)]
                else:
                    # 未指定 browser_id 时跨会话查找该标签页
                    sessions = list(self._sessions.values())
            for s in sessions:
                if s is None:
                    continue
                tab = self._find_tab_in(s, tab_id)
                if tab is not None:
                    return tab, s
            raise ToolError(f"未找到标签页: {tab_id}，可用 tab_list 查看现有标签页")
        session = self.get_session(browser_id)
        return session.chromium.latest_tab, session

    def _find_tab_in(self, session: BrowserSession, tab_id: str):
        try:
            if tab_id in session.chromium.tab_ids:
                return session.chromium.get_tab(tab_id)
        except Exception:
            pass
        # BrowserContext 内的标签页不在浏览器级 tab_ids 中，需要单独查找
        for ctx in session.contexts.values():
            try:
                if tab_id in ctx.tab_ids:
                    return ctx.get_tab(tab_id)
            except Exception:
                continue
        return None

    def list_tabs(self, browser_id: str | None = None) -> list[TabInfo]:
        session = self.get_session(browser_id)
        result = []
        for tid in session.chromium.tab_ids:
            try:
                tab = session.chromium.get_tab(tid)
                result.append(
                    TabInfo(
                        tab_id=tid,
                        browser_id=session.browser_id,
                        url=tab.url,
                        title=tab.title,
                        ready_state=str(tab.states.ready_state),
                    )
                )
            except Exception:
                result.append(TabInfo(tab_id=tid, browser_id=session.browser_id))
        return result

    def tab_info(self, tab_id: str | None, browser_id: str | None = None) -> TabInfo:
        tab, session = self.get_tab(tab_id, browser_id)
        context_id = self._context_of(session, tab)
        return TabInfo(
            tab_id=tab.tab_id,
            browser_id=session.browser_id,
            context_id=context_id,
            url=tab.url,
            title=tab.title,
            ready_state=str(tab.states.ready_state),
        )

    def _context_of(self, session: BrowserSession, tab) -> str | None:
        for cid, ctx in session.contexts.items():
            try:
                if tab.tab_id in ctx.tab_ids:
                    return cid
            except Exception:
                continue
        return None

    def _rebuild_frame(self, tab, old_frame, spec: str):
        """清除 ChromiumFrame 类级缓存并重建 frame 对象，获取新检索会话。"""
        try:
            from DrissionPage._pages.chromium_frame import ChromiumFrame

            fid = getattr(old_frame, "_frame_id", None)
            if fid:
                ChromiumFrame._Frames.pop(fid, None)
        except Exception:
            pass
        try:
            return self.resolve_frame(tab, spec)
        except ToolError:
            return None

    def search(self, tab, locator: str, *, many: bool = False, index: int = 1,
               timeout: float = 10, frame: str | None = None, frame_obj=None):
        """带韧性的元素检索，返回 (结果, 实际使用的容器)。

        DP 5.0.0b1 实测：tab 穿透返回 iframe 历史文档的幽灵节点（盒模型
        全部损坏，不可交互）；ChromiumFrame 会话才是当前文档的可信视图，
        且其对象按 frame_id 缓存、文档更新后需清缓存重建。

        策略：
        - frame 指定（'active'/序号/id）：只走 frame 会话，空结果清缓存重建重试；
        - frame='main'：主文档检索；
        - frame 未指定：激活 frame 优先（功能模块内容），主文档兜底（菜单/外壳）。
        交互类操作必须基于返回的容器。
        """
        norm = normalize_locator(locator)

        def _in(container):
            return container.eles(norm, timeout=timeout) if many else container.ele(norm, index=index, timeout=timeout)

        if frame_obj is not None:
            res = _in(frame_obj)
            if res:
                return res, frame_obj
            return _in(tab), frame_obj

        if frame == "main":
            return _in(tab), tab

        if frame is not None:
            container = self.resolve_frame(tab, frame)
            res = _in(container)
            if res:
                return res, container
            # frame 会话偶发返回空结果：清缓存重建，最多重试 3 次
            import time as _time

            for _ in range(3):
                fresh = self._rebuild_frame(tab, container, frame)
                if fresh is None:
                    break
                if fresh is not container:
                    container = fresh
                _time.sleep(0.4)
                res = _in(container)
                if res:
                    return res, container
            return res, container

        # frame 未指定：激活 frame 优先
        try:
            active = self.resolve_frame(tab, "active")
        except ToolError:
            active = None
        if active is not None:
            res = _in(active)
            if res:
                return res, active
            import time as _time

            for _ in range(3):
                fresh = self._rebuild_frame(tab, active, "active")
                if fresh is None:
                    break
                if fresh is not active:
                    active = fresh
                _time.sleep(0.4)
                res = _in(active)
                if res:
                    return res, active
        # 主文档兜底（幽灵节点由 prefer_visible 过滤）
        return _in(tab), tab

    # ---------- BrowserContext（多账号） ----------

    def new_context(self, browser_id: str | None = None) -> ContextInfo:
        session = self.get_session(browser_id)
        ctx = session.chromium.new_context()
        cid = _short_id()
        with self._lock:
            session.contexts[cid] = ctx
        return ContextInfo(context_id=cid, browser_id=session.browser_id)

    def get_context(self, context_id: str, browser_id: str | None = None):
        with self._lock:
            if browser_id is not None:
                session = self._sessions.get(browser_id)
                if session and context_id in session.contexts:
                    return session.contexts[context_id], session
            else:
                for session in self._sessions.values():
                    if context_id in session.contexts:
                        return session.contexts[context_id], session
        raise ToolError(f"未找到浏览器上下文: {context_id}，可用 context_list 查看")

    def close_context(self, context_id: str) -> str:
        with self._lock:
            for session in self._sessions.values():
                ctx = session.contexts.pop(context_id, None)
                if ctx is not None:
                    break
        if ctx is None:
            raise ToolError(f"未找到浏览器上下文: {context_id}")
        try:
            ctx.close()
        except Exception:
            pass
        return context_id

    def list_contexts(self, browser_id: str | None = None) -> list[ContextInfo]:
        with self._lock:
            sessions = (
                [self.get_session(browser_id)]
                if browser_id
                else list(self._sessions.values())
            )
        result = []
        for s in sessions:
            for cid, ctx in s.contexts.items():
                try:
                    tids = list(ctx.tab_ids)
                except Exception:
                    tids = []
                result.append(ContextInfo(context_id=cid, browser_id=s.browser_id, tab_ids=tids))
        return result

    # ---------- iframe frame ----------

    def list_frames(self, tab_id: str | None, browser_id: str | None = None) -> list[FrameInfo]:
        tab, _ = self.get_tab(tab_id, browser_id)
        result = []
        for i, f in enumerate(tab.eles("tag:iframe"), start=1):
            try:
                fid = f.attr("id")
            except Exception:
                fid = None
            try:
                name = f.attr("name")
            except Exception:
                name = None
            try:
                src = f.attr("src")
            except Exception:
                src = None
            try:
                displayed = bool(f.states.is_displayed)
            except Exception:
                displayed = False
            result.append(
                FrameInfo(
                    frame_index=i,
                    iframe_id=fid,
                    name=name,
                    src=str(src) if src else None,
                    displayed=displayed,
                )
            )
        return result

    def resolve_frame(self, tab, frame: str | None):
        """把 frame 参数解析为可搜索容器（tab=主文档 或 ChromiumFrame）。

        语义：None/'main'=主文档；'active'=激活态(可见) iframe；
        数字=frame_list 中的序号；其余按 iframe 的 id 或 name 属性匹配。
        """
        if frame is None or frame == "main":
            return tab
        iframes = tab.eles("tag:iframe")
        if frame == "active":
            for f in iframes:
                try:
                    if f.states.is_displayed:
                        return f
                except Exception:
                    continue
            raise ToolError("未找到激活态(可见)的 iframe，可用 frame_list 查看当前列表")
        if str(frame).isdigit():
            idx = int(frame)
            if 1 <= idx <= len(iframes):
                return iframes[idx - 1]
            raise ToolError(f"iframe 序号超范围: {frame}，共 {len(iframes)} 个，可用 frame_list 查看")
        for f in iframes:
            try:
                if f.attr("id") == frame or f.attr("name") == frame:
                    return f
            except Exception:
                continue
        raise ToolError(f"未找到 iframe: {frame!r}，可用 frame_list 查看现有 iframe")

    # ---------- 元素注册表 ----------

    def register_element(self, element, tab, browser_id: str, container=None) -> str:
        eid = _short_id()
        with self._lock:
            self._elements[eid] = ElementRecord(element, tab.tab_id, browser_id, container)
            self._element_order.append(eid)
            while len(self._element_order) > MAX_ELEMENTS:
                old = self._element_order.pop(0)
                self._elements.pop(old, None)
        return eid

    def get_element(self, element_id: str):
        record = self.get_record(element_id)
        if record is None:
            raise ToolError(f"未找到元素: {element_id}，元素可能已被清理，请重新用 find_element 定位")
        try:
            alive = record.element.states.is_alive
        except Exception:
            alive = False
        if not alive:
            raise ToolError(
                f"元素 {element_id} 已失效（页面可能已刷新或元素被移除），请重新用 find_element 定位"
            )
        return record.element

    def get_record(self, element_id: str) -> ElementRecord | None:
        with self._lock:
            return self._elements.get(element_id)


manager = BrowserManager()

_KNOWN_PREFIXES = ("css:", "x:", "xpath:", "text:", "t:", "tag:", "ax:", "@", "@@", "@!")


def normalize_locator(locator: str) -> str:
    """定位符规范化。

    DrissionPage 5.0.0b1 在元素相对检索/frame 检索中会把裸 '.cls' / '#id'
    误判为 xpath（tab 级却正常），统一补 css: 前缀规避。
    """
    if locator.startswith((".", "#")) and not locator.startswith(_KNOWN_PREFIXES):
        return f"css:{locator}"
    return locator


def prepare_locator(tab, locator: str) -> None:
    """按需为定位符启用浏览器能力。"""
    if normalize_locator(locator).startswith("ax:"):
        try:
            tab.run_cdp("Accessibility.enable")
        except Exception:
            pass  # 重复启用或启用失败都不阻塞定位本身


def has_box(ele) -> bool:
    """元素是否能算出盒模型（可见性判据）。

    DP 5.0.0b1 的 states.is_displayed 不检查祖先链的 display:none，
    关闭弹窗残留 DOM / 隐藏 iframe 内元素会误报可见；rect 可算与否更可靠。
    """
    try:
        size = ele.rect.size
        return bool(size) and float(size[0]) > 0 and float(size[1]) > 0
    except Exception:
        return False


def prefer_visible(eles: list) -> list:
    """优先返回可见元素；全部不可见时原样返回（保留测试隐藏元素的入口）。"""
    visible = [e for e in eles if has_box(e)]
    return visible if visible else eles


def _rect_center_in_page(tab, ele, container) -> tuple[float, float] | None:
    """用 JS 计算元素中心点在页面视口中的坐标（视口 CSS 像素）。

    iframe 内元素 = frame 相对坐标 + iframe 在页面中的偏移。
    不依赖 CDP DOM.getBoxModel，规避 DP 5.0.0b1 跨 frame 盒模型查询的
    NoRectError（"Could not compute box model"）问题。
    """
    import json as _json

    script = (
        "const r = arguments[0].getBoundingClientRect();"
        "return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2,"
        " w: r.width, h: r.height});"
    )
    in_frame = container is not None and container is not tab
    # iframe 元素必须在其所属文档上下文中执行 JS（跨 world 调用会报错）
    runners = ([container, tab] if in_frame else [tab])
    for runner in runners:
        try:
            rel = runner.run_js(script, ele)
            if not rel:
                continue
            p = _json.loads(rel)
            if float(p["w"]) <= 0 or float(p["h"]) <= 0:
                return None  # 隐藏元素（如隐藏 iframe 内的同名节点）不可点击
            ox, oy = 0.0, 0.0
            if in_frame:
                loc = container.rect.location  # iframe 在页面视口中的位置
                ox, oy = float(loc[0]), float(loc[1])
            return (float(p["x"]) + ox, float(p["y"]) + oy)
        except Exception:
            continue
    return None


def real_click(tab, ele, container=None, retries: int = 2) -> None:
    """Actions 真实鼠标点击，带动画期韧化与坐标兜底。

    首选 DP 原生 actions.click；布局回退期会瞬时 NoRectError（跨 frame
    盒模型查询失败），此时改用 JS 坐标直接派发 Input 鼠标事件——仍是
    真实鼠标事件而非 JS 合成点击。
    """
    import time as _time

    from DrissionPage.errors import NoRectError
    from .cursor import act_cursor, glide_cursor

    # 尝试获取目标元素在顶层视口绝对坐标并驱动虚拟光标滑行
    try:
        pt = None
        if ele and hasattr(ele, "rect"):
            pt = getattr(ele.rect, "viewport_midpoint", None) or getattr(ele.rect, "midpoint", None)
        if pt is None:
            pt = _rect_center_in_page(tab, ele, container)
        if pt is not None:
            glide_cursor(tab, pt[0], pt[1], 180)
            _time.sleep(0.08)
            act_cursor(tab, "click", pt[0], pt[1])
    except Exception:
        pass

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            try:
                ele.wait.stop_moving(gap=0.1)
            except Exception:
                pass  # stop_moving 不可用或超时都不阻塞点击
            tab.actions.click(ele)
            return
        except Exception as e:
            last_err = e
            transient = isinstance(e, NoRectError) or not str(e).strip()
            if not transient or attempt >= retries:
                break
            _time.sleep(0.8)

    point = _rect_center_in_page(tab, ele, container)
    if point is None:
        if last_err is not None and str(last_err).strip():
            raise last_err
        raise ToolError(f"点击失败（重试 {retries} 次且坐标兜底不可用）: {last_err!r}")
    try:
        tab.actions.move_to(point, duration=0.2).click()
    except Exception as e:
        if last_err is not None and str(last_err).strip():
            raise last_err
        raise ToolError(f"坐标点击失败: {e!r}") from e
