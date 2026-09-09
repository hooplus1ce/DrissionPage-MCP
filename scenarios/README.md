# 声明式场景回归层 (Scenario Regression)

基于 DrissionPage-MCP 的声明式自动化回归测试规范。

---

## 一、设计背景

* **现状痛点**：在复杂的业务场景（如多角色协同审批、采购下单到出库）中，若每次回归都完全依赖大模型进行实时决策，会面临 **高 Token 开销（每步数千 Tokens）、耗时长、以及非确定性决策抖动**。
* **核心价值**：
  1. **将 AI 现场探索成果沉淀为资产**：AI 首次探索验证成功的流程，可直接导出为一份 `.yaml` 脚本；
  2. **毫秒级确定性回放**：回归时无需大模型参与每一步交互推理，由执行引擎按步骤直接驱动 MCP 工具流水线；
  3. **多角色与上下文自动串联**：自动在步骤间传递动态生成的 `tab_id`、`context_id`，支持多账号无缝协作。

---

## 二、场景文件规范 (YAML / JSON)

场景文件放置在 `scenarios/` 目录下（支持 `.yaml`、`.yml` 或 `.json`）。

### 顶层字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 场景唯一标识符，如 `demo18_aps_multi_role_flow` |
| `description` | string | 否 | 场景业务目标与前提说明 |
| `variables` | dict | 否 | 初始环境变量与自定义常量字典 |
| `steps` | list | 是 | 有序执行的测试步骤列表 |

### 单步 `steps` 字段结构

```yaml
- step: "步骤名称或业务描述（必填，便于报错定位）"
  tool: "要调用的 MCP 工具名称（必填，如 nav_menu, click, wait_message 等）"
  args:
    param1: "参数值（支持变量插值，如 ${tab_id}）"
  expect:                        # 可选：断言检查
    status_ok: true              # 检查工具输出模型中 ok == true 或 found == true
    message_contains: "成功"     # 检查 message 或 matched_text 中是否包含指定关键字
  save:                          # 可选：变量提取与上下文沉淀
    order_no: "data"             # 将本次工具返回结果中的 'data' 字段存入变量 ${order_no}
  sleep: 1.0                     # 可选：执行完该步后的显式休眠秒数（非必须不建议使用）
```

---

## 三、变量系统与动态插值

引擎内置变量替换机制，所有 `args` 中的参数值支持 `${变量名}` 语法进行动态插值。

### 1. 自动捕获的系统变量
当上文步骤的工具返回模型中包含以下字段时，引擎会**自动更新**至当前会话变量池中，后续步骤无需手动声明即可直接引用：
* `${tab_id}`：当前操作的标签页 ID（由 `profile_open`、`tab_new`、`nav_menu` 等自动更新）
* `${context_id}`：当前多账号浏览器上下文 ID（由 `profile_open`、`context_new` 自动更新）
* `${browser_id}`：当前浏览器实例 ID

### 2. 自定义提取变量
使用 `save` 字段从工具返回值中提取任意业务数据，例如：
```yaml
- step: 提取采购单号
  tool: vtable_find_cell
  args:
    text: "PO2026"
  save:
    target_row: row              # 把返回的 row 行号提取并存为 ${target_row}
```

---

## 四、断言与熔断机制

每个步骤的 `expect` 配置用于保障测试可靠性：
* `status_ok: true`：
  校验返回对象中的 `ok` 属性或 `found` 属性（如 `wait_message` / `find_element`）；
* `message_contains: "关键词"`：
  校验结果中的提示消息文本（支持局部包含匹配）。

> **熔断保护 (`stop_on_error`)**：  
> 当任意步骤工具调用抛错或 `expect` 断言未满足时，引擎会**立即停止后续步骤执行**，输出明确的失败步骤序号、用时与错误详情，防止脏数据蔓延。

---

## 五、实战范例：demo18 APS 双角色回归

文件位置：`scenarios/demo18_aps_multi_role_flow.yaml`

```yaml
name: demo18_aps_multi_role_flow
description: demo18 APS 平台多角色鉴权、模块路由与视觉核验完整回归流程

steps:
  # --- 阶段一：管理员发起并查验 ---
  - step: 1. 打开管理员会话（自动注入 Token 登录态）
    tool: profile_open
    args:
      profile: aps               # 使用 profiles.toml 中配置的管理员账号
    expect:
      status_ok: true

  - step: 2. 管理员直达采购订单模块
    tool: nav_menu
    args:
      menu_name: 采购订单
      tab_id: ${tab_id}          # 自动代入上一步生成的 tab_id
    expect:
      status_ok: true

  - step: 3. 截取管理员采购订单模块视图
    tool: screenshot
    args:
      tab_id: ${tab_id}
      path: .dpmcp/verify/scenario_po_shot.png

  # --- 阶段二：审批人独立会话查验 ---
  - step: 4. 打开审批人会话（独立 BrowserContext 隔离）
    tool: profile_open
    args:
      profile: aps_approver      # 使用 profiles.toml 中配置的审批人账号
    expect:
      status_ok: true

  - step: 5. 审批人直达产线管理模块
    tool: nav_menu
    args:
      menu_name: 产线管理
      tab_id: ${tab_id}
    expect:
      status_ok: true

  # --- 阶段三：断言与环境清理 ---
  - step: 6. 验证消息断言机制（无异常消息）
    tool: wait_message
    args:
      tab_id: ${tab_id}
      pattern: 无此异常消息气泡
      timeout: 0.5
      raise_if_not_found: false  # 超时允许返回 found=false

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

## 六、运行方式

### 方式 1：通过 MCP 客户端工具调用（AI 驱动回放）

可以直接对 AI 发送指令调用 `scenario_run`：

```python
# 按场景文件名直接调用（引擎会自动在 scenarios/ 目录下检索）
scenario_run(scenario="demo18_aps_multi_role_flow")

# 或指定相对路径
scenario_run(scenario="scenarios/demo18_aps_multi_role_flow.yaml")
```

### 方式 2：通过 Python 脚本脱机执行（CI/CD 集成）

```python
import asyncio
from fastmcp import Client
from drissionpage_mcp.server import mcp

async def main():
    async with Client(mcp) as client:
        result = await client.call_tool("scenario_run", {
            "scenario": "scenarios/demo18_aps_multi_role_flow.yaml",
            "stop_on_error": True
        })
        data = result.data
        print(f"场景 [{data.name}] 执行结果: {'通过' if data.ok else '失败'}")
        for s in data.steps:
            print(f"  [{'PASS' if s.ok else 'FAIL'}] {s.step} ({s.elapsed_seconds}s)")

if __name__ == "__main__":
    asyncio.run(main())
```
