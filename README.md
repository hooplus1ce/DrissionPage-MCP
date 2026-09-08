# DrissionPage-MCP

基于 [FastMCP v4](https://gofastmcp.com) 与 [DrissionPage 5.0](https://www.drissionpage.cn/) 的浏览器自动化 MCP 服务。

让支持 MCP 的客户端（Claude Desktop、Cursor、各类 IDE 等）可以直接驱动浏览器完成网页操作：导航、元素定位与交互、多账号隔离等。

## 功能

- **多浏览器会话管理**：启动新浏览器（自动分配调试端口）或接管已在 `127.0.0.1:9222` 运行的浏览器，多会话并行
- **核心浏览与元素操作**：导航、等待、元素定位（支持 5.0 的 `ax:` 无障碍定位与自动匹配模式）、点击/输入/悬停/下拉选择/勾选/滚动
- **多账号隔离**：基于 5.0 的 `BrowserContext`，同一浏览器内维护多套独立 cookies
- **结构化输出**：所有工具返回 Pydantic 模型，MCP 客户端获得结构化内容；失败抛出可读的中文错误供模型自行重试

## 安装

需要 Python ≥ 3.14 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
```

## 运行

```bash
uv run drissionpage-mcp                     # stdio（本地客户端直连）
uv run drissionpage-mcp --transport http --port 8000   # Streamable HTTP，端点 /mcp
```

### 客户端配置示例（Claude Desktop / 通用 stdio）

```json
{
  "mcpServers": {
    "drissionpage": {
      "command": "uv",
      "args": ["run", "--directory", "D:\\Developer\\Hoolinks\\DrissionPage-MCP", "drissionpage-mcp"]
    }
  }
}
```

## 工具一览（55 个）

| 分组 | 工具 |
|---|---|
| 浏览器 | `browser_launch` `browser_connect` `browser_close` `browser_status` `run_js` |
| 标签页 | `tab_new` `tab_list` `tab_close` `tab_info` |
| 导航 | `navigate` `navigate_back` `navigate_forward` `refresh` `wait_element` `get_page_info` `get_page_html` |
| 元素 | `find_element` `find_elements` `element_info` `element_click` `element_input` `element_hover` `element_select` `element_check` `element_scroll` |
| 多账号 | `context_new` `context_close` `context_list` `cookies_get` `cookies_set` `cookies_clear` |
| iframe | `frame_list`（所有定位工具支持 `frame` 参数：`'active'`=激活态模块 / 序号 / id） |
| 真实交互 | `action_chain`（move_to/click/hold/drag/scroll/type/key 步骤编排，CDP Input 事件级）`press_key` |
| AntD 弹层 | `antd_select` `antd_get_options` `antd_date_pick` `antd_modal_click` `get_toasts` |
| 页面快照 | `page_controls`（单次 JS 采集全部可交互控件，封顶 40 项） |
| VTable | `vtable_info` `vtable_headers` `vtable_read_cells` `vtable_find_cell` `vtable_cell_info` `vtable_scroll_to_cell` `vtable_click_cell` `vtable_click_icon` `vtable_resolve_cell` `vtable_edit_cell` `vtable_get_selection` `vtable_cell_state` `vtable_scroll_viewport` `vtable_drag_scrollbar` `vtable_hover_cell` `vtable_cell_text` |

### 会话模型

服务在进程内维护三层注册表：`browser_id → Chromium`、`context_id → BrowserContext`、`element_id → 元素`。
工具的 `browser_id` / `tab_id` 参数省略时作用于当前唯一会话/最新标签页；存在多个会话时必须显式指定。
元素在页面刷新后会失效，需重新 `find_element` 定位。

### 面向 iframe 微前端的适配

功能模块以 iframe 挂载时，服务自动保证元素可交互性：默认检索优先**激活态（可见）iframe**，
主文档兜底；已关闭弹窗的残留 DOM 会被可见性过滤剔除；定位符自动规范化
（裸 `.cls`/`#id` 在 frame/相对检索中会被 DP 5.0.0b1 误判为 xpath）。
DrissionPage 的 tab 穿透检索在本 beta 中返回过期文档的幽灵节点，服务已规避。

### AntD portal 弹层

`antd_select` / `antd_date_pick` 兼容新旧两代类名（`.ant-select-item-option` 与
`.ant-select-dropdown-menu-item`、`.ant-picker-*` 与 `.ant-calendar-*`）；
`antd_modal_click` 自动定位最顶层可见弹窗并兼容无 footer 的定制弹窗；
所有交互均为 Actions 真实鼠标事件。

### 定位符语法（DrissionPage 5.0）

```
#id / .class / tag:div / @attr=value    常用简写
css:selector / xpath://div / text:文字  显式指定方式
ax:@name=搜索@role=button               无障碍树定位（5.0 新增）
@@attr1=v1@@attr2=v2                    多条件 AND
不带前缀                                 自动匹配：先试 xpath/css，再按文本模糊匹配
```

## 开发与测试

```bash
uv run pytest                # 单元测试（假对象，不启动浏览器）
DPMCP_SMOKE=1 uv run pytest tests/test_smoke.py   # 真浏览器冒烟测试（需本机 Chrome）
```

### VTable（canvas 表格）原理

VTable 内容渲染在 canvas 中，DOM 不可见。工具链通过注入 JS 拿到页面里的
VTable 实例（容器 `__vtable__` 直连 → React Fiber 扫描兜底，绑定存于
`window.__vt`），用其官方 API 读取单元格数据与 scenegraph 几何（canvas 局部
坐标），再叠加 canvas 在 iframe 内的偏移与 iframe 在页面视口中的偏移，得到
视口绝对坐标后交给 action_chain 派发真实鼠标事件。所有 VTable 片段集中在
`vtable_scripts.py`，坐标换算在 `vtable.py`。

## 省 token 设计约定

所有工具输出遵循：列表封顶（浮层 4 条、控件 40 项）、文本截断（24~60 字符）、
**空字段整体省略**（如无浮层时响应不含 overlays 键）。动作类工具内置
观察→执行→收集流水线（MutationObserver 捕捉动作后的新浮层）；
`vtable_click_cell` 响应含 verified 标志（目标格是否进入选区，信息性——
勾选/按钮格与未开点选的表格恒为 False）；`action_chain` 的 type 步骤
默认拟人键入节奏（30~90ms 随机键间隔）。

## 端到端验证脚本

```bash
uv run python scripts/e2e_aps_check.py    # 全功能端到端检查（接管 9222 浏览器）
uv run python scripts/probe_aps.py        # APS 技术栈适配探测（只读）
uv run python scripts/e2e_crud_flow.py    # 真实 CRUD 流程：进模块→表单→填写→保存→toast 断言
uv run python scripts/e2e_vtable.py       # VTable 真机验证：绑定→列头→读值→找值→点击→图标
uv run python scripts/dump_dom.py         # 全量 DOM 快照与组件框架分析 -> dom_snapshot/
```

## 版本说明

- 依赖 **DrissionPage 5.0.0b1**（预览版）。5.0 删除了 `ChromiumPage`/`WebPage`，全面转向 `Chromium`/`BrowserContext`/`Tab` 模型；正式版发布后如有 API 变动，本项目的封装层（`manager.py` 与 `tools/`）是唯一的适配点。
- 后续规划：网络监听（HTTP/WebSocket/SSE）、独立代理配置、截图与 PDF。

## 许可

MIT
