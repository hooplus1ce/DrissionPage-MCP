---
name: aps-data-permission
description: "APS/SCM 数据权限与数据范围表配置及实机双浏览器端到端测试标准 SOP"
metadata:
  version: 1.0.0
---

# APS 数据权限端到端自动化测试 Skill (APS-Data-Permission-E2E)

## 1. 触发场景
当执行 APS / SCM 系统测试用例，且满足以下任一条件时强制启用本规范：
- 一级/二级功能分类为「数据权限」；
- 用例包含「数据范围表 / 范围表过滤 / 使用组织隔离 / 供应组织隔离 / 制造部门隔离」；
- 需要验证不同角色或账号的数据可见性与越权防护边界。

---

## 2. 账号与环境隔离契约

支持两种测试架构（按需选择）：

1. **方案 A：单浏览器多账号独立上下文（推荐首选，更轻更快）**：
   - **管理员上下文**：调用 `profile_open(profile="aps")` 自动注入 Token 打开管理端；
   - **测试账号上下文**：调用 `profile_open(profile="aps_approver")` 开启完全隔离的 BrowserContext；
   - 测试完毕调用 `profile_close` 成对关闭。
2. **方案 B：物理双浏览器接管模式**：
   - **Port 9222 (Admin 管理端)**：系统管理员账号，负责数据范围表创建与角色权限范围绑定；
   - **Port 9223 (Test 测试端)**：测试账号 `Hooplus1cer`（用户 ID: `61787`，部门: `制造一部`），负责数据可见性核验；
   - 分别通过 `browser_connect(port=9222)` 与 `browser_connect(port=9223)` 独立接管。

---

## 3. 标准端到端测试链路 (4 大黄金步骤)

### 步骤 1：管理端数据范围表字段颗粒度配置
1. 管理端就绪（`profile_open(profile="aps")` 或 `browser_connect(port=9222)`）；
2. 调用 `nav_menu(menu_name="数据范围表", tab_id="${tab_id}")` 直达模块；
3. 点击 `[新 增]` 打开新建范围表页面（调用 `click(target="text:新 增", tab_id="${tab_id}")`）；
4. **基础信息配置**：
   - 填写 `范围表名称`（如 `产线管理-使用组织范围表-0085`）；
   - 下拉选择 `适用对象`（如 `产线管理`、`物料替代方案`、`采购申请单` 等，使用 `antd_select`）；
5. **明细表格字段颗粒度配置**：
   - 在其他信息明细表中点击 `[新 增]` 按钮新增行；
   - 调用 `vtable_click_cell` 点击 `数据字段` 列单元格，在弹出的下拉浮层中调用 `antd_select` 或 `click` 选中目标字段（如 `使用组织`）；
   - 调用 `vtable_click_cell` 点击 `校验规则` 列单元格，在下拉浮层中调用 `click` 选中规则（如 `等于` / `包含`）；
   - 调用 `vtable_click_cell` 点击 `条件值` 列单元格，在下拉选项中调用 `click` 选中条件值（如 `广东生和堂健康食品股份有限公司`）；
6. ⚠️ **必须调用 `click(target="text:保 存")` 点击页面顶部的 `[保 存]` 按钮**；
7. 极速断言：调用 `wait_message(pattern="新增成功|成功")` 确认捕获到通知气泡！

---

### 步骤 2：角色管理操作级范围表绑定 (避坑铁律)
1. 调用 `nav_menu(menu_name="角色管理", tab_id="${tab_id}")` 直达角色管理列表；
2. ❌ **严禁点击顶部操作栏的 `[配置数据权限]` 按钮**（该按钮仅用于粗粒度组织架构树配置）；
3. ✅ **必须调用 `vtable_click_cell` 点击列表中目标角色的 `角色名称` 单元格**（如 `权限测试专用` / `col: 2, row: 2`），进入「角色详情」页面；
4. 调用 `click(target="text:编 辑")` 点击顶部 `[编 辑]` 按钮进入编辑模式；
5. 在左侧功能权限树展开并选中目标业务模块（如 `生产资源设置 > 产线管理`）；
6. 在右侧操作权限表格中，找到目标操作行（如 `列表查询`），调用 `vtable_click_cell` 点击其 **`权限范围`** 列单元格；
7. 在弹出的范围表下拉列表中，调用 `antd_select` 或 `click` 选中步骤 1 中创建的数据范围表；
8. ⚠️ **调用 `click(target="text:保 存")` 点击顶部 `[保 存]` 按钮**，并调用 `wait_message(pattern="修改成功|保存成功")` 确认保存成功！

---

### 步骤 3：测试账号实机隔离验证
1. 打开测试账号会话（`profile_open(profile="aps_approver")` 或 `browser_connect(port=9223)`）；
2. 调用 `nav_menu(menu_name="产线管理", tab_id="${tab_id}")` 导航至目标业务模块；
3. 调用 `click(target="text:查 询")` 触发数据检索；
4. 调用 `vtable_inspect(tab_id="${tab_id}")` 捕获 live 数据表格切片，同时调用 `screenshot` 留痕，严格核验：
   - **数据总量缩减**：列表总条数是否按范围表过滤规则精准缩减；
   - **字段值 100% 合规**：逐条校验可见数据行的对应字段（如 `使用组织`），必须严格满足配置的条件值；
   - **越权数据 100% 拦截**：非该组织/范围的数据完全不可见。
5. **判定测试结论**：符合上述所有条件则判定为 `通过`，否则为 `失败`。

---

### 步骤 4：测试结果留痕与会话清理
1. 调用 `screenshot(path=".dpmcp/verify/permission_evidence.png")` 保存实机核验现场证据；
2. 成对调用 `profile_close(profile="aps_approver")` 与 `profile_close(profile="aps")` 关闭测试会话，释放资源。
