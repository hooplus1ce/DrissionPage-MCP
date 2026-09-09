# DrissionPage-MCP

基于 [FastMCP v4](https://gofastmcp.com) 与 [DrissionPage 5.0](https://www.drissionpage.cn/) 的浏览器自动化 MCP 服务。

让支持 MCP 的客户端（Claude Desktop、Cursor、各类 IDE 等）可以直接驱动浏览器完成网页操作：导航、元素定位与交互、多账号隔离等。

## 功能

- **多浏览器会话管理**：启动新浏览器（自动分配调试端口）或接管已在 `127.0.0.1:9222` 运行的浏览器，多会话并行
- **核心浏览与元素操作**：导航、等待、元素定位（支持 5.0 的 `ax:` 无障碍定位与自动匹配模式）、点击/输入/悬停/下拉选择/勾选/滚动
- **虚拟光标可视化**：所有真实鼠标交互伴随 Windows 11 深色高清虚拟光标——60FPS 缓动滑行、按下缩放、点击水波纹，自动化轨迹肉眼可追踪（`.env` 设 `SHOW_CURSOR=false` 关闭）
- **AntV X6 流程图自动化**：审批流画布的拓扑提取、拖拽移节点、端口连线、双击配置、增删节点
- **多账号隔离**：基于 5.0 的 `BrowserContext`，同一浏览器内维护多套独立 cookies
- **结构化输出**：所有工具返回 Pydantic 模型，MCP 客户端获得结构化内容；失败抛出可读的中文错误供模型自行重试

## 安装

需要 Python ≥ 3.14 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
```

## 项目结构（FastMCP 官方组合模式）

```
DrissionPage-MCP/
├── fastmcp.json             # 官方声明式项目配置（fastmcp run 自动读取）
├── server.py                # 文件型入口：fastmcp run / inspect 指向的 mcp 实例
├── .env.example             # 环境变量样例（SHOW_CURSOR 光标可视化开关）
├── src/drissionpage_mcp/
│   ├── server.py            # 组合根：主服务器 + mount 各领域子服务器 + lifespan + dev-tool 管控
│   ├── tools/               # 按领域拆分的子服务器（官方 composition 模式）
│   │   ├── browser.py    navigate.py    element.py    frame.py
│   │   ├── action.py     antd.py        vtable.py     account.py
│   │   └── snapshot.py   x6.py
│   ├── manager.py           # 浏览器会话/上下文/元素注册表（含 DP 5.0.0b1 缺陷补丁）
│   ├── vtable.py            # VTable 坐标换算层
│   ├── vtable_scripts.py    # VTable JS 片段库（含多粒度 inspect）
│   ├── x6.py                # X6 画布会话与真实拖拽/连线
│   ├── x6_scripts.py        # X6 JS 片段库（Fiber 绑定 / 拓扑提取）
│   ├── cursor.py            # Win11 虚拟光标（60FPS 滑行 + 点击涟漪）
│   ├── overlays.py          # 浮层观察器（arm/drain）
│   └── models.py            # 输出模型
└── tests/                   # 112 个单测 + 1 个真浏览器冒烟（DPMCP_SMOKE=1 门控）
```

## 运行

```bash
fastmcp run                              # 推荐：读取 fastmcp.json（stdio）
fastmcp run --transport http --port 8000 # Streamable HTTP，端点 /mcp
fastmcp inspect server.py                # 查看服务器工具清单

uv run drissionpage-mcp                  # 等价：脚本入口（stdio）
uv run python -m drissionpage_mcp --transport http --port 8000
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

## 工具一览（66 个）

底层脚本工具 `run_js` 默认禁用且对客户端隐藏（`ENABLE_RUN_JS=true` 全局放开，
或运行时 `enable_dev_tool` 临时解锁），避免绕过高阶领域工具。

| 分组 | 工具 |
|---|---|
| 浏览器 | `browser_launch` `browser_connect` `browser_close` `browser_status` |
| 标签页 | `tab_new` `tab_list` `tab_close` `tab_info` |
| 导航 | `navigate` `navigate_back` `navigate_forward` `refresh` `wait_element` `get_page_info` `get_page_html` |
| 元素 | `find_element` `find_elements` `element_info` `click`（通用：元素/选择器/坐标全能点击） `element_click` `element_input` `element_hover` `element_select` `element_check` `element_scroll` |
| 多账号 | `context_new` `context_close` `context_list` `cookies_get` `cookies_set` `cookies_clear` |
| iframe | `frame_list`（所有定位工具支持 `frame` 参数：`'active'`=激活态模块 / 序号 / id） |
| 真实交互 | `action_chain`（move_to/click/hold/drag/scroll/type/key 步骤编排，CDP Input 事件级）`press_key` |
| AntD 弹层 | `antd_select`（多选自动 ESC 收回） `antd_get_options` `antd_date_pick` `antd_modal_click` `get_toasts` |
| 页面快照 | `page_controls`（单次 JS 采集可交互控件，封顶 40 项，含面包屑模块路径） |
| VTable | `vtable_info` `vtable_inspect`（多粒度快照+交互锚点） `vtable_headers` `vtable_read_cells` `vtable_find_cell` `vtable_cell_info` `vtable_scroll_to_cell` `vtable_click_cell` `vtable_click_icon` `vtable_resolve_cell` `vtable_edit_cell` `vtable_get_selection` `vtable_cell_state` `vtable_scroll_viewport` `vtable_drag_scrollbar` `vtable_hover_cell` `vtable_cell_text` |
| X6 流程图 | `x6_nodes` `x6_fit` `x6_move_node` `x6_connect` `x6_click_node` `x6_add_node` `x6_delete_node` |
| 管控 | `enable_dev_tool` `disable_dev_tool`（临时解锁/锁定 `run_js`） |

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
`antd_select` 对多选下拉在选中后自动派发 ESC 收回浮层，避免遮挡后续按钮；
所有交互均为 Actions 真实鼠标事件。

### 模块路径识别（面包屑为权威）

`get_page_info` 与 `page_controls` 自动解析主框架 `.ant-breadcrumb` 并返回
`breadcrumb` / `module_path` 字段。这是功能模块路径的**权威依据**，
禁止凭 iframe 的 src/URL 猜测模块。

### 开发者工具管控

`run_js` 默认隐藏，防止模型绕过封装好的领域工具。需要底层调试时调用
`enable_dev_tool(name, user_explicit_instruction)`（须附用户明确指示原话），
完成后必须 `disable_dev_tool` 重新锁定。

### AntV X6（流程图画布）原理

注入 JS 经容器 React Fiber 扫描绑定 X6 Graph 实例（`window.__x6_graph`），
合并图模型（节点业务数据/边关系）与 SVG DOM 几何（视口绝对坐标、端口中心）
输出拓扑；拖移/连线均为真实 CDP 鼠标轨迹（拖拽时光标 1:1 线性同步）。
`x6_delete_node` 以真实 Backspace 优先，未生效时回退图模型级 `removeCell`
并在响应中以 `deleted_via` 标注——断言 UI 删除行为应校验 `deleted_via == "keyboard"`。

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

`tests/test_vtable_js.py` 用本机 node 直接执行 VTable 的 JS 片段（stub 掉
scenegraph），覆盖语法与 range 稀疏化/find 截断语义——这部分平时被 fake
`run_js` 的预置响应掩盖；node 不可用时自动跳过。

### VTable（canvas 表格）原理

VTable 内容渲染在 canvas 中，DOM 不可见。工具链通过注入 JS 拿到页面里的
VTable 实例（容器 `__vtable__` 直连 → React Fiber 扫描兜底，绑定存于
`window.__vt`），用其官方 API 读取单元格数据与 scenegraph 几何（canvas 局部
坐标），再叠加 canvas 在 iframe 内的偏移与 iframe 在页面视口中的偏移，得到
视口绝对坐标后交给 action_chain 派发真实鼠标事件。所有 VTable 片段集中在
`vtable_scripts.py`，坐标换算在 `vtable.py`。

## 省 token 设计约定

所有工具输出遵循：列表封顶、文本截断、**空字段整体省略**（如无浮层时响应不含
overlays 键）、**截断必须显式标注**（truncated 标志，模型可补取）。分层原则：
默认返回决策所需最小集，需要完整数据时通过 full/offset 等参数显式补取。

| 约定 | 说明 |
|---|---|
| 浮层 / 控件封顶 | overlays 4 条×60 字符、page_controls 40 项×24 字符 |
| vtable_inspect 分层 | cell 模式全量；column/row 模式无逐格几何（文本+交互态）；range 模式为文本矩阵 values + 稀疏 styles/interactive（仅偏离基线项，基线见 baseline_style）+ 框选锚点，**上限 500 格**超限报错 |
| vtable 选区 | `vtable_click_cell` 响应中 selection 为紧凑摘要（col/row/field/value≤80）；完整明细（含 originData≤120/值）走 `vtable_get_selection` |
| read_cells 双重闸门 | 格数上限 2000 + 响应 64KB 字节级安全网（UTF-8 字节；截断置 truncated/truncated_rows，maxRow 同步为实际末行） |
| element_info | 默认截断 inner_html≤1000/属性值≤200/value≤500，标注 truncated_fields；`full=True` 放宽（inner_html≤5000、属性值/value 不截断） |
| 无界参数钳制 | find_elements limit≤200（响应含 total/truncated）、vtable_find_cell≤100（truncated 标注）、get_page_html≤50K（默认 20K）、run_js 输出≤20K |
| 分页 | `antd_get_options` 单页 50 条 + total/truncated，offset 翻页（antd_select 匹配在服务端，截断不影响选中） |
| 字段白名单 | cookies_get 仅返回 name/value/domain/path/expires/httpOnly/secure/sameSite；x6 节点 data 值级截断 200 字符 |
| 其他 | 动作类工具内置观察→执行→收集流水线；action_chain type 拟人键入（30~90ms）；verified 标志（信息性，勾选/按钮格恒 False） |

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
