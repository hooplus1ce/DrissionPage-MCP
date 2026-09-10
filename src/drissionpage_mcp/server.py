"""FastMCP 组合根（composition root）。

遵循 FastMCP 官方推荐模式：
- 各业务领域的工具定义在 tools/ 下的独立子服务器中；
- 本模块创建主服务器并 mount() 组合（实时链接，工具名保持原样）；
- lifespan 统一管理浏览器资源的清理。

启动方式（官方推荐）：
- `fastmcp run`（项目根目录的 fastmcp.json 指向本模块的 mcp 实例）；
- `python -m drissionpage_mcp`（等价封装）；
- `python -m drissionpage_mcp --transport http --port 8000`（HTTP 传输）。
"""

from __future__ import annotations

import os
import argparse
import asyncio
from pathlib import Path

try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.is_file():
        load_dotenv(dotenv_path=_env_path, override=False)
    else:
        load_dotenv(override=False)
except Exception:
    pass
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan

from .manager import manager

INSTRUCTIONS = """\
DrissionPage-MCP：基于 DrissionPage 5.0 的浏览器自动化服务，面向 UI 自动化功能测试。

典型流程：
1. browser_launch（启动新浏览器，自动分配端口）或 browser_connect（接管已在 9222 端口运行的浏览器）
2. tab_new 打开标签页，或直接操作 latest_tab
3. navigate 打开网址，find_element 定位元素后用 click / element_input 等交互
4. 多账号隔离用 context_new 创建独立 BrowserContext（cookies 互不影响），或用 profile_open 一键开
5. 结束后 browser_close 关闭自启浏览器（browser_connect 接管的浏览器不会被关闭）

工具默认省略 browser_id / tab_id 时作用于当前唯一（或最新）会话/标签页；
存在多个会话时必须显式指定 id。

账号档案与登录（APS 平台）：
- 凭据只驻留服务端：profile_list 查看已配置档案（.env 的 HL_* 或 HL_PROFILES_FILE 指向的 TOML），
  工具只按档案名取用，任何返回值都不含密码与访问令牌
- 多角色并行：profile_open(profile=...) 为每个档案开独立 BrowserContext + 标签页 + 自动登录；
  或签/会签、权限测试需要多个账号各自登录时，用不同 profile 各开一套（cookies/令牌互不干扰）
- 验证码不经过任何 OCR 组件：auth_login/profile_open 返回 captcha_required 时，
  先 auth_captcha(profile=...) 取回验证码图片 → 由多模态模型读出字符 →
  auth_login(profile=..., captcha_id=..., captcha_code="<识别结果>") 完成登录；
  登录成功后访问令牌写入 localStorage["HL-Access-Token"] + 同名 cookie；
  登录态自动缓存（HL_SESSION_TTL，默认 12h），force=true 可强制重登
- profile_close 关闭档案上下文；auth_session_clear 清除缓存登录态

iframe 功能模块（如 APS 等管理系统）：
- 进入/切换功能模块优先使用 nav_menu(menu_name='...') 一键直达并自动等待激活 iframe，无需手动搜索与多轮点击
- 每个二级菜单/功能模块以 iframe 挂载在顶级 DOM，激活态（可见）的 iframe 即当前页面
- 先 frame_list 查看，用 frame='active' 把定位范围限定到激活模块，
  避免主文档与 iframe 中同名元素混淆
- 模块路径识别权威依据：严格以主框架顶部的面包屑导航（.ant-breadcrumb / .ant-breadcrumb-link）为准，
  get_page_info 与 page_controls 已自动提取并返回 breadcrumb / module_path，严禁根据 iframe 的 src/URL 猜测模块路径！
- AntD 弹窗/下拉/日期/消息气泡以 portal 渲染在其所属功能模块的文档中，
  antd_select / antd_date_pick / antd_modal_click / get_toasts 已自动处理
- get_toasts 是即时快照：没有气泡时立即返回空列表（绝不空等）；
  要等某条气泡出现再做断言，用 wait_message(pattern=..., timeout=...)
- screenshot 可对视口、整页、指定元素或激活模块 iframe 进行真实截图，回传图片内容块用于视觉核验

网络数据包监控（feature 套件 'net'，需先 enable_feature('net') 解锁）：
- 标准顺序：net_listen_start(urls=...) → 执行 UI 动作 → net_listen_wait(...) 取包；
  start 之前产生的数据包一律取不到（队列出队语义，同包不会重复读到）
- 即时取包用 net_listen_snapshot（不空等）；等网络整体安静用 net_listen_wait_silent；
  用例结束用 net_listen_stop 释放 Network 域
- 断言接口载荷错位类缺陷：按 url 过滤取包后读 post_data / params
  （如 net_listen_start(urls="approverOptions") → 切换下拉 → net_listen_wait() 看 ?type=）
真实交互（UI 测试首选）：
- click 派发真实鼠标事件（支持选择器/坐标/元素 id）；action_chain 编排 move_to/hold+move+release
  拖拽/scroll/type/key（CDP Input 级别）；press_key 单键；element_input 输入文本

定位符语法（DrissionPage 5.0）：
- '#id' / '.class' / 'tag:div' / '@attr=value' —— 常用简写
- 'css:selector' / 'xpath://div' / 'text:文字' —— 显式指定方式
- 'ax:@name=搜索@role=button' —— 无障碍树定位（5.0 新增，可触达 CSS 难定位的菜单/弹层）
- 不带前缀时自动匹配：先尝试 xpath/css，再按文本模糊匹配
- 多条件用 @@ 连接，如 '@@tag:div@@text()=确定'
- 注意：按钮文本可能含全角空格（如 '新 增'、'确 定'），文本匹配时用部分文字或 text: 前缀模糊匹配

APS 前端组件框架（实测指纹，定位时优先使用）：
- 组件库为 legions-pro-*（封装 AntD v3 时代组件）：legions-pro-select（下拉）、
  legions-pro-modal（可拖拽弹窗）、legions-pro-form（表单）、
  legions-pro-vtable（VTable canvas 表格）、legions-pro-quick-filter-row（列表查询区）、
  legions-pro-pagecontainer（页面容器）
- 下拉选项是旧版类名 .ant-select-dropdown-menu-item（不是 v4+ 的 .ant-select-item-option）；
  antd_select 已自动兼容新旧类名，无需手写类名
- 日期组件为 .ant-calendar-*（旧版）；antd_date_pick 已自动兼容
- 多选下拉框规则（黄金法则）：多选下拉框（Select[multiple]）在选完待选值后必须按下 ESC 键让下拉框收回（antd_select 工具已默认开启 close_multi=True 自动收回；若手动操作务必发 press_key('ESCAPE') 收回），严禁让展开的下拉菜单遮挡后续操作按钮！
- 存在 ant-ant-* 双前缀类名（legions 包装产物），定位时直接用 ant-* 即可
- VTable 表格内容渲染在 canvas 里，DOM 定位只能到画布容器；vtable 交互触发的
  浮层分三家族：① .vtable__menu-element*（VTable 自带菜单，位于 .vtable 容器内部、
  z-index -9999，关闭态带 --hidden 修饰类）；② .vtable-filter-menu（挂在 body 末尾）；
  ③ 常规 AntD portal（ant-tooltip/ant-dropdown 等）。定位时按此优先检查
- VTable 表格工具已收敛为 3 个核心能力：vtable_inspect（全功能多粒度快照）、vtable_find_cell（搜文本）、
  vtable_click_cell（点击单元格或图标，自动滚动）；查表格信息统一用 vtable_inspect
- 表单结构规整：.ant-form-item 内 .ant-form-item-label label + 控件，
  可用 '@@tag:label@@text()=字段名' 反查同 form-item 内的控件

开发者工具安全规范：
- 底层通用脚本工具（如 run_js）默认已禁用并对 AI 隐藏，严禁用于常规 UI 自动化测试（点击、输入、表格操作、拖拽等）！
- 所有常规交互必须使用已封装的高阶领域工具（vtable_*、antd_*、element_*、action_chain 等）；
- 仅当用户在指令中明确指示“执行 JS”或需底层调试时，才可通过 enable_dev_tool 解锁 run_js，操作完成后须调用 disable_dev_tool 重新锁定。
"""


@lifespan
async def app_lifespan(server: FastMCP):
    try:
        yield {"manager": manager}
    finally:
        # 阻塞的 DP 调用放到线程里执行，避免卡事件循环
        await asyncio.to_thread(manager.shutdown)


# 主服务器：组合各领域子服务器（无命名空间，工具名保持原样）
mcp: FastMCP = FastMCP(
    "DrissionPage-MCP",
    instructions=INSTRUCTIONS,
    lifespan=app_lifespan,
)

# 挂载 skills 技能提供者（FastMCP Skills 规范，通过 skill:// 协议暴露）
from fastmcp.server.context import Context
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.transforms import ResourcesAsTools

_skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
if _skills_dir.is_dir():
    mcp.add_provider(
        SkillsDirectoryProvider(
            roots=_skills_dir,
            reload=True,
            supporting_files="resources",
        )
    )

# 资源转工具（允许纯 Tool 客户端通过 list_resources 和 read_resource 消费 skills 技能）
mcp.add_transform(ResourcesAsTools(mcp))
from .tools import (  # noqa: E402
    account,
    action,
    antd,
    auth,
    browser,
    element,
    frame,
    navigate,
    net,
    scenario,
    snapshot,
    vtable,
    x6,
)
for _sub in (
    browser.mcp,
    navigate.mcp,
    element.mcp,
    frame.mcp,
    action.mcp,
    antd.mcp,
    vtable.mcp,
    account.mcp,
    auth.mcp,
    snapshot.mcp,
    scenario.mcp,
    x6.mcp,
    net.mcp,
):
    mcp.mount(_sub)
# ---------- 搜索转换器（可选开启，当工具很多时大幅节省 Token） ----------

ENABLE_TOOL_SEARCH = os.getenv("ENABLE_TOOL_SEARCH", "false").lower() in ("1", "true", "yes")

if ENABLE_TOOL_SEARCH:
    from fastmcp.server.transforms.search import BM25SearchTransform

    _ALWAYS_VISIBLE_TOOLS = [
        "profile_open",
        "profile_close",
        "nav_menu",
        "click",
        "element_input",
        "antd_select",
        "screenshot",
        "wait_message",
        "vtable_inspect",
        "scenario_run",
        "list_resources",
        "read_resource",
    ]
    mcp.add_transform(
        BM25SearchTransform(
            always_visible=_ALWAYS_VISIBLE_TOOLS,
            max_results=5,
        )
    )

# ---------- 底层开发者工具管控（默认对 AI 隐藏 run_js，避免模型跑偏） ----------

DEV_TOOLS: set[str] = {"run_js"}

# 默认隐藏底层脚本工具，除非环境变量显式设置 ENABLE_RUN_JS=true
ENABLE_RUN_JS = os.getenv("ENABLE_RUN_JS", "false").lower() in ("1", "true", "yes")

if not ENABLE_RUN_JS:
    mcp.disable(tags={"dev"})


@mcp.tool(
    tags={"system", "security"},
    annotations={"title": "临时解锁开发者工具", "readOnlyHint": False},
)
async def enable_dev_tool(
    name: str,
    user_explicit_instruction: str,
    ctx: Context | None = None,
) -> str:
    """仅在用户明确指令要求执行底层脚本或底层调试时，临时解锁被隐藏的开发者工具（如 'run_js'）。

    【安全规范】常规 UI 自动化测试（点击、输入、下拉选择、表格操作、拖拽排序列等）严禁申请解锁此工具！
    必须优先使用封装好的领域工具（如 vtable_*、antd_*、element_*、action_chain 等）。
    仅当用户在本轮对话中明确要求“执行 JS 脚本”或需要底层调试且无相应工具覆盖时，方可传入用户指示原文申请解锁。

    Args:
        name: 要解锁的工具名称（目前支持: 'run_js'）
        user_explicit_instruction: 用户当前明确要求执行底层脚本的指示原话
    """
    clean_name = name.strip()
    if clean_name not in DEV_TOOLS:
        raise ToolError(f"未受管控的开发者工具: '{clean_name}'，支持解锁的工具: {sorted(DEV_TOOLS)}")
    if not user_explicit_instruction or not user_explicit_instruction.strip():
        raise ToolError("必须提供用户明确要求调用底层工具的指令内容作为授权凭证。")

    if ctx:
        try:
            await ctx.enable_components(tags={"dev"})
        except Exception:
            pass

    mcp.enable(tags={"dev"})
    return f"已临时解锁工具 [{clean_name}]。完成该项操作后必须调用 disable_dev_tool 重新锁定。"


@mcp.tool(
    tags={"system", "security"},
    annotations={"title": "重新锁定开发者工具", "readOnlyHint": False},
)
async def disable_dev_tool(name: str = "run_js", ctx: Context | None = None) -> str:
    """重新锁定底层开发者工具，将其从可用工具列表中移除，避免污染后续常规 UI 自动化测试。

    Args:
        name: 要锁定的工具名称，默认为 'run_js'
    """
    clean_name = name.strip()
    if clean_name not in DEV_TOOLS:
        raise ToolError(f"未受管控的开发者工具: '{clean_name}'，支持管控的工具: {sorted(DEV_TOOLS)}")

    if ctx:
        try:
            await ctx.disable_components(tags={"dev"})
        except Exception:
            pass

    mcp.disable(tags={"dev"})
    return f"已锁定并隐藏工具 [{clean_name}]。"
# ---------- 业务场景特性套件（按需插拔，降低 Token 消耗） ----------

SUPPORTED_FEATURES: set[str] = {"x6", "vtable", "net"}

FEATURE_SUITES: dict[str, set[str]] = {
    "x6": {
        "x6_nodes",
        "x6_fit",
        "x6_move_node",
        "x6_connect",
        "x6_click_node",
        "x6_add_node",
        "x6_delete_node",
    },
    "vtable": {
        "vtable_inspect",
        "vtable_find_cell",
        "vtable_click_cell",
    },
    "net": {
        "net_listen_start",
        "net_listen_wait",
        "net_listen_snapshot",
        "net_listen_wait_silent",
        "net_listen_pause",
        "net_listen_resume",
        "net_listen_stop",
    },
}

_DISABLED_FEATURE_NAMES: set[str] = set()


def enable_feature_internal(name: str) -> bool:
    clean = name.strip().lower()
    if clean not in SUPPORTED_FEATURES:
        return False
    mcp.enable(tags={clean})
    _DISABLED_FEATURE_NAMES.discard(clean)
    return True


def disable_feature_internal(name: str) -> bool:
    clean = name.strip().lower()
    if clean not in SUPPORTED_FEATURES:
        return False
    mcp.disable(tags={clean})
    _DISABLED_FEATURE_NAMES.add(clean)
    return True


def enable_all_features() -> None:
    for f in SUPPORTED_FEATURES:
        enable_feature_internal(f)


def _init_features() -> None:
    raw = os.getenv("DISABLED_FEATURES", "x6,net").strip()
    if raw:
        for f in raw.split(","):
            clean = f.strip().lower()
            if clean in SUPPORTED_FEATURES:
                disable_feature_internal(clean)


_init_features()


@mcp.tool(
    tags={"system", "feature"},
    annotations={"title": "启用场景特性套件", "readOnlyHint": False},
)
async def enable_feature(name: str, ctx: Context | None = None) -> str:
    """按需启用特定业务场景的工具套件（如进入审批流设计页面启用 'x6'，进入大数据表格启用 'vtable'，
    需要抓取/断言接口请求与响应时启用 'net'）。

    Args:
        name: 特性套件名称，支持 'x6'（流程图 7 个工具）、'vtable'（表格 3 个工具）、
              'net'（网络数据包监听 7 个工具）
    """
    clean_name = name.strip().lower()
    if clean_name not in SUPPORTED_FEATURES:
        available = "、".join(sorted(SUPPORTED_FEATURES))
        raise ToolError(f"未知的特性套件 [{name}]，可用套件：{available}")

    if ctx:
        try:
            await ctx.enable_components(tags={clean_name})
        except Exception:
            pass

    enable_feature_internal(clean_name)
    count = len(FEATURE_SUITES.get(clean_name, []))
    return f"已启用特性套件 [{clean_name}]（解锁 {count} 个专属工具）。"


@mcp.tool(
    tags={"system", "feature"},
    annotations={"title": "禁用场景特性套件", "readOnlyHint": False},
)
async def disable_feature(name: str, ctx: Context | None = None) -> str:
    """离开特定业务场景后禁用工具套件，减少向模型暴露的 Schema Token 开销。

    Args:
        name: 特性套件名称，支持 'x6'、'vtable'、'net'
    """
    clean_name = name.strip().lower()
    if clean_name not in SUPPORTED_FEATURES:
        available = "、".join(sorted(SUPPORTED_FEATURES))
        raise ToolError(f"未知的特性套件 [{name}]，可用套件：{available}")

    if ctx:
        try:
            await ctx.disable_components(tags={clean_name})
        except Exception:
            pass

    disable_feature_internal(clean_name)
    return f"已禁用特性套件 [{clean_name}]，降低上下文 Token 消耗。"


@mcp.tool(
    tags={"system", "feature"},
    annotations={"title": "查看特性套件状态", "readOnlyHint": True},
)
def list_features() -> dict:
    """查看所有场景特性套件的启用/禁用状态及其包含的工具列表。"""
    return {
        suite: {
            "enabled": suite not in _DISABLED_FEATURE_NAMES,
            "tools": sorted(list(tools)),
        }
        for suite, tools in sorted(FEATURE_SUITES.items())
    }

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="drissionpage-mcp", description="DrissionPage 5.0 MCP 服务"
    )
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio", help="传输方式，默认 stdio"
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 传输监听地址")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 传输监听端口")
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
