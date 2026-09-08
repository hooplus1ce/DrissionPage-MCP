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

import argparse
import asyncio

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from .manager import manager

INSTRUCTIONS = """\
DrissionPage-MCP：基于 DrissionPage 5.0 的浏览器自动化服务，面向 UI 自动化功能测试。

典型流程：
1. browser_launch（启动新浏览器，自动分配端口）或 browser_connect（接管已在 9222 端口运行的浏览器）
2. tab_new 打开标签页，或直接操作 latest_tab
3. navigate 打开网址，find_element 定位元素后用 element_click / element_input 等交互
4. 多账号隔离用 context_new 创建独立 BrowserContext（cookies 互不影响）
5. 结束后 browser_close 关闭自启浏览器（browser_connect 接管的浏览器不会被关闭）

工具默认省略 browser_id / tab_id 时作用于当前唯一（或最新）会话/标签页；
存在多个会话时必须显式指定 id。

iframe 功能模块（如 APS 等管理系统）：
- 每个二级菜单/功能模块以 iframe 挂载在顶级 DOM，激活态（可见）的 iframe 即当前页面
- 先 frame_list 查看，用 frame='active' 把定位范围限定到激活模块，
  避免主文档与 iframe 中同名元素混淆
- AntD 弹窗/下拉/日期/消息气泡以 portal 渲染在其所属功能模块的文档中，
  antd_select / antd_date_pick / antd_modal_click / get_toasts 已自动处理

真实交互（UI 测试首选）：
- element_click 默认通过 Actions 派发真实鼠标事件（移动→按下→抬起）
- action_chain 可编排复杂真实操作：move_to/click/hold+move+release 拖拽/
  scroll/type 输入/key_down+key_up 按键，全部为 CDP Input 事件级别
- press_key 用于 ENTER/ESC 等单键；element_input 输入文本

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
  antd_select / antd_get_options 已自动兼容，无需手写类名
- 日期组件为 .ant-calendar-*（旧版）；antd_date_pick 已自动兼容
- 存在 ant-ant-* 双前缀类名（legions 包装产物），定位时直接用 ant-* 即可
- VTable 表格内容渲染在 canvas 里，DOM 定位只能到画布容器；vtable 交互触发的
  浮层分三家族：① .vtable__menu-element*（VTable 自带菜单，位于 .vtable 容器内部、
  z-index -9999，关闭态带 --hidden 修饰类）；② .vtable-filter-menu（挂在 body 末尾）；
  ③ 常规 AntD portal（ant-tooltip/ant-dropdown 等）。定位时按此优先检查
- VTable 实例经 window.__vt 绑定后可用官方 API 做断言：getSelectedCellInfos
  （选区含业务记录 originData）、cellIsInVisualView、getCellCheckboxState、
  getBodyVisibleCellRange；vtable 工具族已封装，优先用工具
- 表单结构规整：.ant-form-item 内 .ant-form-item-label label + 控件，
  可用 '@@tag:label@@text()=字段名' 反查同 form-item 内的控件
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

from .tools import (  # noqa: E402
    account,
    action,
    antd,
    browser,
    element,
    frame,
    navigate,
    snapshot,
    vtable,
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
    snapshot.mcp,
):
    mcp.mount(_sub)


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
