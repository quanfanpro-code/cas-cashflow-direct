# cas-cashflow-direct 摘要未解释语义修复实施计划

> **For agentic workers:** 未授权子Agent，按`executing-plans.md`在当前会话串行执行。每个任务遵循失败测试、最小实现、完整验证。

**目标：** 让固定规则如实识别并保存未解释的摘要业务内容，按需触发现有受限摘要Agent，并允许“原文仍无法解释”以不计分、不构成冲突的方式继续运行。

**架构：** 复用`SummarySpan`、`SummarySemanticResult`、现有Agent任务分批、结果导入、固定候选和评分入口。只在摘要模块增加原文覆盖检查和Agent原文不足终态；流水线仅补充序列化和终态识别，不新建模块或依赖。

**技术栈：** Python标准库、现有JSON规则、pytest、现有流水线和Git。

## 全局约束

- 项目范围仅限`D:\BaiduSyncdisk\workbuddy skills\cas-cashflow-direct`及桌面权威设计。
- 修改任何已有文件前，备份到`C:\Users\27651\BackUp\cas-cashflow-direct_<时间戳>\`并核对SHA-256。
- 不操作LM Studio，不安装依赖，不使用客户名称、金额、凭证号或来源行特例。
- 摘要Agent不返回项目、候选、质量、分数、动作或置信度。
- 未解释摘要不加分、不扣分、不构成冲突；实际形成相反候选时才按冲突处理。
- 不重复十万行性能测试；真实样本只运行一次。
- 中文Markdown使用UTF-8 with BOM；代码和JSON保持原文件编码并检查替换字符。

---

### 任务1：备份并锁定摘要完成红线

**文件：**
- 备份：`src/cashflow_direct/summary_semantics.py`
- 备份：`src/cashflow_direct/pipeline.py`
- 备份：`src/cashflow_direct/versions.py`
- 备份：`references/摘要语义规则.json`
- 备份：`tests/test_summary_semantics.py`
- 备份：`tests/test_pipeline.py`
- 备份：`tests/test_classification_routing.py`
- 备份：`CONTEXT.md`
- 备份：`README.md`
- 备份：`SKILL.md`
- 备份：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`

- [ ] 创建独立时间戳备份目录并复制上述实际存在的文件。
- [ ] 逐个比较源文件与备份文件SHA-256，任何不一致立即停止。
- [ ] 确认Git工作区没有用户未提交修改；新增需求、设计和计划文档除外。

### 任务2：用失败样例锁定摘要覆盖和Agent终态

**文件：**
- 修改：`tests/test_summary_semantics.py`
- 修改：`tests/test_pipeline.py`
- 修改：`tests/test_classification_routing.py`

**接口：**
- 使用：`analyze_summary(summary, rules)`
- 使用：`build_summary_agent_task(result)`
- 使用：`merge_summary_agent_slots(result, payload, rules)`
- 预期新增：`SummarySemanticResult.unexplained_spans`
- 预期Agent结果：`outcome=resolved|source_insufficient`

- [ ] 增加“外部安装劳务费、鉴定试验费、打孔加工费”均形成`needs_agent`且保存准确未解释区间的测试。
- [ ] 增加公司名称、地点、日期、金额、合同号和普通“某公司款”不误触发的测试。
- [ ] 增加Agent只补部分原文后仍不能标记完成的测试。
- [ ] 增加Agent返回`source_insufficient`后形成合法终态、候选为空、质量0、未解释区间保留的测试。
- [ ] 增加摘要原文不足不否定路径45分、选择45分仍可自动修改的分类测试。
- [ ] 增加摘要实际形成不同候选时仍属于冲突的回归测试。
- [ ] 运行三个测试文件，确认新增测试因缺少实现而失败，且失败原因与需求一致。

### 任务3：实现最小摘要覆盖检查

**文件：**
- 修改：`src/cashflow_direct/summary_semantics.py`
- 修改：`references/摘要语义规则.json`

**接口：**
- 产生：`_unexplained_business_spans(summary, facts, rules) -> tuple[SummarySpan, ...]`
- 调整：`_unresolved_slots(summary, facts, unexplained, rules) -> tuple[str, ...]`
- 扩展：`SummarySemanticResult(..., unexplained_spans=())`

- [ ] 在规则文件增加只用于触发Agent的业务句式形态，不把触发词绑定现金流项目。
- [ ] 合并固定规则和Agent已经覆盖的原文区间，跳过实体、日期、金额、编号、空白和标点。
- [ ] 对剩余原文只保留真正带业务动作、对象、用途、属性、关系或分句作用的区间。
- [ ] 删除“存在任意Agent事实就不再检查”的旁路；合并后重新执行同一覆盖检查。
- [ ] 让Agent任务携带未解释原文区间，并限定返回区间必须对应当前未解释内容。
- [ ] 支持`source_insufficient`合法终态；保留留痕，清空摘要候选并把摘要质量设为0。
- [ ] 运行`tests/test_summary_semantics.py`，确认新增和既有摘要测试全部通过。

### 任务4：接通运行状态、版本和动作回归

**文件：**
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/versions.py`
- 修改：`tests/test_pipeline.py`
- 修改：`tests/test_classification_routing.py`
- 修改：`tests/test_release_bundle.py`（仅在现有版本断言需要同步时）

**接口：**
- 扩展：`_summary_result_to_dict`和`_summary_result_from_dict`
- 调整：`import_summary_results`
- 版本：运行结构`4.4`，摘要语义版本`2026-08-24-unexplained-summary-v5`

- [ ] 序列化和恢复未解释原文区间。
- [ ] 摘要Agent批次保留现有每批最多25项的规则。
- [ ] 导入时同时接受全部解释完成和原文不足两个合法业务终态；部分解释仍保持未完成。
- [ ] 保持分类器现有规则：质量0且无摘要候选时不生成摘要分数，完整路径照常评分。
- [ ] 升级运行结构和摘要语义版本，旧运行不得续接。
- [ ] 运行流水线、分类路由和版本定向测试，确认全部通过。

### 任务5：普通回归、规则覆盖校准和文档同步

**文件：**
- 修改：`CONTEXT.md`
- 修改：`README.md`
- 修改：`SKILL.md`（仅同步运行步骤和Agent结果格式所必需内容）
- 修改：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- 新建：`docs/reviews/2026-08-24-cas-cashflow-direct摘要未解释语义修复-review.md`

- [ ] 用已有5,355项摘要状态做只读覆盖检查，核对触发原因；只校准通用语法触发条件，不加客户特例。
- [ ] 运行普通完整回归：`python -X utf8 -m pytest -q --ignore=tests/test_tianwei_acceptance.py --ignore=tests/test_100k_performance.py`。
- [ ] 更新权威设计中“摘要固定规则完成条件”“Agent失败门禁”和真实验收历史口径。
- [ ] 更新`CONTEXT.md`中的“未解释业务内容”和“原文信息不足”定义。
- [ ] 更新README正文和2026-08-24 Changelog，明确Agent任务0不再是优秀结果。
- [ ] 校验中文文件BOM和替换字符，运行Skill快速校验及`git diff --check`。

### 任务6：一次真实样本冒烟、复核和发布

**文件：**
- 只读：用户已确认的两份真实输入和现有确认参数。
- 新建：独立的真实测试输出目录，不覆盖历史结果。

- [ ] 仅启动一次真实样本，使用用户已确认的45分最低阈值。
- [ ] 完成实际生成的摘要Agent任务；不得用固定脚本伪造Agent语义，也不得调用LM Studio。
- [ ] 续接同一运行状态完成分类、必要AI失败出口和最终工作簿，不因中间等待重开第二次真实运行。
- [ ] 核对唯一摘要数、摘要Agent任务数、规则完成数、Agent完成数、原文不足数、四档质量、自动改判、待人工数量、金额桥、差异表、工作簿结构、公式、外部链接和输入哈希。
- [ ] 写入复核记录，明确残余边界；不重复十万行性能测试。
- [ ] 检查最终差异仅包含本次文件，提交中文提交信息并推送当前`main`到`origin/main`。
