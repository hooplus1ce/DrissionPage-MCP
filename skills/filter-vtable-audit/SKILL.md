---
name: filter-vtable-audit
description: 对 APS 类（Ant Design + VTable）列表页做筛选区与列字段一致性审查并产出分批禅道 BUG 报告
metadata:
  version: 1.0.0
---

# Filter VTable Audit — 列表筛选区与列字段一致性审查

## Overview

对 APS 类（Ant Design + VTable）列表页做三方一致性审查，产出一份**可直接粘贴禅道**的分批 BUG 模板报告：

1. **数量一致性**：筛选区字段数 vs VTable 业务列数是否对齐；
2. **名称一致性**：同名业务字段在筛选区与列表是否使用同一显示名；
3. **值覆盖性**：对「下拉」型筛选字段，展开采集其全部待选值，校验**对应列表列出现的每个单元格值都必须能在下拉待选值中选到**（即 `列单元格值集合 ⊆ 下拉待选值集合`，记 `C ⊆ D`），反之（列里有下拉选不到的值）即 BUG。

典型输出：5 类 BUG（字典编码未翻译 / 字典文案两套 / 筛选独有无列 / 列独有无筛选 / 命名不一致）→ 分批成多条禅道 bug 模板。

## 前提（先确认，再动手）

- 浏览器已接管（qa-automation `browser_connect`，默认端口 9222；未连接则先连接）。
- 页面已打开目标模块列表页（如 `customerManagement`），需先确认**激活 iframe** 是哪张业务页（多 Tab 场景以 `ui_page_context` 返回的 `active_iframe.frame_url` 为准，不要假设）。
- 目标列表是 **VTable**（canvas 渲染）。

## 执行工作流

### 步骤 1 — 定位页面上下文

调用 `ui_page_context`：记录 `active_iframe`（frame_id / frame_name）与 `focus_layer`。后续所有带 `frame` 的工具调用统一填该 frame（传 `frame="active"` 或精确 frame_name 均可）。

### 步骤 2 — 采集筛选区字段全集

调用 `ui_analyze_scope`（`max_controls: 300` 起，宁可多不可漏；`max_overlays: 5`），返回**紧凑控件清单**，从中解析筛选字段：

- 每个筛选字段由控件三元组构成：**字段名下拉**（a11y `name`=字段名，如"客户名称"）→ **操作符下拉**（`name`=包含/等于/介于 之一）→ **值控件**（文本框，或无 a11y name、css 形如 `selectUid*`/`.legions-pro-select` 的**值下拉**）。
- **排除非筛选控件**：按钮（查询/设置/重置/收起▲/导出/更多）、分页（`li.ant-pagination-*`、每页条数、跳页 input）、表格工具栏。
- 同一字段会以 control+combobox 双节点重复出现（如 c1/c2 都是"创建组织"），**只按字段计 1 次**；按"操作符下拉个数=字段数"复核。
- 判定结果：字段总数 N、其中「值下拉」字段清单（后续步骤 4 需要）。

### 步骤 3 — 采集 VTable 列定义与单元格值

**列定义**：调用 `vtable_analysis`（`mode: "full"`，`visible_only: false`，`max_columns: 30`，`include_values: false`）拿全量列 `title`+`field`。

- 输出若超 token 上限会自动持久化到 tool-results 目录的 `.txt`（JSON 单行），此时用 `python json.load` 提取 `analysis.columns[].field/.title`，不要整读 base64/大文件。
- **排除系统/操作列后再计数**：复选框(`_vtable_checkbox`)、序号(`_vtable_series_number`)、操作/日志列（如 `_op`）。其余为**业务列**。

**单元格值（仅对需要做值匹配的列）**：用 `vtable_read_cells` 按矩形批量读取（`col0/row0/col1/row1`，行优先，row0=0 是表头，数据从 row1 起）。经验：一次 20 行 × 11 列约 6KB，安全；整表 26 列建议分 2 次。记录每列取值集合（去重）。

### 步骤 4 — 展开值下拉采集待选值（重点取证）

对步骤 2 识别出的每个「值下拉」，用 `ui_click` 传入其 css（`selectUid*` 类选择器，指向值下拉元素，勿点操作符下拉），`observe_after: true`：

- 返回结果 `overlays[].text` / `changes[].text` **直接给出全部待选值文本**（如 "1级 2级 3级 4级 5级"），无需再读 DOM 或截图。
- 依次点击下一个值下拉会自动关闭上一个；全部采集完，点击页面空白处（无控件坐标，如工具条空白区）关闭浮层。

### 步骤 5 — 三方比对，得出差异清单

执行 `references/comparison-rules.md` 中的判定规则，输出差异表：

- 数量差：筛选 N vs 业务列 M；同名对齐对数；列出「仅筛选区有」「仅列表有」「名称不一致」三类明细。
- 值覆盖：对每个「有对应列的值下拉字段」，判定**列单元格值集合 ⊆ 下拉待选值集合**是否成立（C ⊆ D）：若列值 `1-5`(数字) vs 下拉 "1级"~"5级"(文本) 或列值 {未审批,已审批} vs 下拉 {待审核,已审核}，均判**不匹配（列值在下拉中选不到）**。

### 步骤 6 — 分批汇总输出禅道 BUG 模板

按 `references/bug-report-template.md` 的结构输出 Markdown 报告，**保存到当前项目 `artifacts/` 目录**，命名 `<模块名>_筛选与列字段一致性缺陷_禅道BUG模板_YYYYMMDD.md`：

- 一个缺陷一“批”，按类别归组（字典不一致 / 筛选-列配置不同步 / 命名文案），同类归入同一修复迭代建议；
- 每条 BUG 含：所属模块 / Bug 标题 / 严重程度（禅道 1致命 2严重 3一般 4轻微）/ 优先级 / Bug 类型 / 前置条件 / 复现步骤 / 预期结果 / 实际结果 / 影响分析 / 证据（实测值）。
- 用 `present_files` 交付报告；如截了证据图一并附上。

## 交付质量要求

- 数据必须逐条来自实测（下拉 text、单元格值、列 title），标注**数据范围**（第几页 × 几行），勿编造总数或未读数据；
- 通过项（布尔是/否等匹配一致的字段）也列出，标 ✅，避免全篇"狼来了"；
- 数量/名称差异用差集语言表述（如"筛选独有 2 项、列表独有 1 项、同名对齐 22 对"），便于禅道评审人快速定位。

## Resources

### references/comparison-rules.md

加载以获取字段/列/值的完整判定规则、系统列排除口径与常见根因推断。

### references/bug-report-template.md

加载以获取禅道 BUG 模板报告骨架与 5 类 BUG 的成例（含字段表格式、严重度分级、文件命名约定）。
