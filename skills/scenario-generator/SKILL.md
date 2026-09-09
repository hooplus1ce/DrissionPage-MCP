---
name: scenario-generator
description: 指导 AI 与自动化脚本生成符合 DrissionPage-MCP 规范的声明式回归场景（YAML/JSON），以 demo18 APS 多角色协同为基准范例
metadata:
  version: 1.0.0
  author: DrissionPage-MCP
---

# DrissionPage-MCP 声明式回归场景生成规范

本 Skill 旨在指导 AI 代理（Agent）或测试开发者，自动生成可被 `scenario_run` 工具直接执行的声明式端到端回归场景文件（YAML / JSON）。

---

## 1. 核心设计原则

1. **确定性与零 Token 开销**：
   * 场景文件用于将 AI 现场探索通过的交互流程“固化”为测试资产。
   * 回归回放时，测试引擎直接以毫秒级调用 MCP 工具，无需大模型参与每一步的视觉与文本推理。
2. **多角色与上下文隔离**：
   * 支持多账号（如管理员 `aps`、审批人 `aps_approver`）各自在独立的 `BrowserContext` 中运行，Cookies 与 LocalStorage 完全物理隔离。
3. **显式 Tab 穿透传递**：
   * 后续步骤必须显式通过 `${tab_id}` 引用前置步骤创建的标签页，避免后台标签页节流导致超时。

---

## 2. 场景文件结构规范

场景文件保存至 `scenarios/<scenario_name>.yaml`，标准格式如下：

```yaml
name: 场景英文标识符（必填，如 demo18_aps_multi_role_flow）
description: 场景业务目标及前置依赖说明（选填）

variables:                      # 全局常量与初始变量池（选填）
  env_prefix: demo18
  timeout_default: 5.0

steps:                          # 步骤序列（必填，按列表顺序执行）
  - step: "1. 步骤业务描述"
    tool: mcp_tool_name         # 调用的 MCP 工具名称（必填）
    args:
      param_a: value_a          # 工具入参（支持 ${var} 变量插值）
      tab_id: "${tab_id}"
    expect:                     # 断言校验（选填）
      status_ok: true           # 检查工具输出模型中 ok == true 或 found == true
      message_contains: "成功"  # 检查返回 message 或 matched_text 是否包含子串
    save:                       # 变量提取（选填）
      extracted_key: field_name # 从工具返回值抽取字段并存入变量池
    sleep: 0.5                  # 执行后额外等待秒数（选填，非必须不加）
```

---

## 3. 适用 MCP 工具矩阵与推荐参数

| 业务阶段 | 推荐工具 | 关键参数 | 说明 |
|---|---|---|---|
| **会话管理** | `profile_open` | `profile: "aps" / "aps_approver"` | 一键开独立 BrowserContext + 自动注入 Token + 打开后台 |
| | `profile_close` | `profile: "..."` | 测试完毕后必须成对关闭上下文，释放内存与会话 |
| **模块直达** | `nav_menu` | `menu_name: "采购订单"`, `tab_id: "${tab_id}"` | 自动处理标签复用、到达菜单展开、输入匹配及 iframe 激活 |
| **视觉核验** | `screenshot` | `tab_id: "${tab_id}"`, `path: ".dpmcp/verify/..."` | 关键节点截图落盘，兼顾多模态回传与磁盘留痕 |
| **结果断言** | `wait_message` | `tab_id: "${tab_id}"`, `pattern: "保存成功"` | 极速（0.02s）轮询 AntD 提示气泡，未匹配抛错熔断 |
| **表格操作** | `vtable_inspect` | `table_index: 0` | 获取 Canvas 表格结构、表头及可见数据快照 |
| | `vtable_find_cell`| `text: "PO2026..."` | 快速按文本定位单元格坐标 `(col, row)` |
| | `vtable_click_cell`| `col: 1, row: 2, icon: "checkbox"` | 自动滚动到位并点击单元格或内部图标 |
| **流程图** | `x6_nodes` | 无入参 | 读取 X6 画布全量节点、端口与拓扑连接 |
| | `x6_connect` | `from_node: "...", to_node: "..."` | CDP 级真实物理拉线连桩 |
| **基础交互** | `click` | `target: "css:.btn"` 或 `element_id: "..."` | 全能通用点击（元素/选择器/坐标） |
| | `element_input` | `element_id: "...", text: "..."` | 表单输入文本 |

---

## 4. 变量系统工作规则

1. **自动捕获规则**：
   * 任何步骤只要工具返回了包含 `tab_id`、`context_id` 或 `browser_id` 的对象，引擎会自动更新当前变量池。
   * **黄金法则**：在 `profile_open` 之后的所有步骤，必须显式传入 `tab_id: "${tab_id}"`。
2. **自定义提取规则 (`save`)**：
   ```yaml
   - step: 查找业务行
     tool: vtable_find_cell
     args:
       text: "采购单A"
     save:
       target_row: row          # 将返回值中的 row 存入 ${target_row}
   
   - step: 点击该行复选框
     tool: vtable_click_cell
     args:
       col: 0
       row: ${target_row}       # 动态引用上一步提取的行号
       icon: checkbox
   ```

---

## 5. 断言与熔断机制配置

* **`expect.status_ok: true`**：
  * 适用于 `profile_open`（检查 `logged_in` / `ok`）、`nav_menu`（检查 `ok`）、`profile_close` 等。
* **`expect.message_contains: "文本"`**：
  * 适用于表单提交、流程保存后的提示语校验。
* **反向静默断言**：
  * 若断言当前页面不应有报警或异常提示，传 `raise_if_not_found: false`：
    ```yaml
    - step: 校验无错误气泡
      tool: wait_message
      args:
        pattern: "错误|异常|失败"
        timeout: 0.5
        raise_if_not_found: false
    ```

---

## 6. 标准基准范例：demo18 多角色协同

文件：`scenarios/demo18_aps_multi_role_flow.yaml`

```yaml
name: demo18_aps_multi_role_flow
description: demo18 APS 平台多角色鉴权、模块路由与视觉核验完整回归流程

steps:
  # 阶段 1：管理员发起与功能查验
  - step: 1. 打开管理员会话（自动注入 Token 登录态）
    tool: profile_open
    args:
      profile: aps
    expect:
      status_ok: true

  - step: 2. 管理员直达采购订单模块
    tool: nav_menu
    args:
      menu_name: 采购订单
      tab_id: ${tab_id}
    expect:
      status_ok: true

  - step: 3. 截取管理员采购订单模块视图
    tool: screenshot
    args:
      tab_id: ${tab_id}
      path: .dpmcp/verify/scenario_po_shot.png

  # 阶段 2：独立审批人会话协同
  - step: 4. 打开审批人会话（独立 BrowserContext 隔离）
    tool: profile_open
    args:
      profile: aps_approver
    expect:
      status_ok: true

  - step: 5. 审批人直达产线管理模块
    tool: nav_menu
    args:
      menu_name: 产线管理
      tab_id: ${tab_id}
    expect:
      status_ok: true

  # 阶段 3：气泡断言与上下文闭环清理
  - step: 6. 验证消息断言机制（无异常消息）
    tool: wait_message
    args:
      tab_id: ${tab_id}
      pattern: 无此异常消息气泡
      timeout: 0.5
      raise_if_not_found: false

  - step: 7. 关闭审批人会话
    tool: profile_close
    args:
      profile: aps_approver
    expect:
      status_ok: true

  - step: 8. 关闭管理员会话
    tool: profile_close
    args:
      profile: aps
    expect:
      status_ok: true
```

---

## 7. 生成场景时的常见避坑清单

1. ❌ **遗漏 `tab_id: ${tab_id}`**：导致工具默认操作最新或后台标签页，在 Chrome CDP 节流机制下可能引发 `Page.captureScreenshot` 30 秒超时。
2. ❌ **直接操作未登录标签**：不要手写打开网页并敲输入框登录，务必使用 `profile_open` 统一接管 Token 注入。
3. ❌ **使用死等待 (`sleep: 5`)**：等待业务提交反馈必须优先使用 `wait_message(pattern="保存成功")`，毫秒级快速断言。
4. ❌ **忘记关闭上下文**：流程结尾必须成对调用 `profile_close`，保持浏览器内存与会话清爽。
