# cas-cashflow-direct 全面复核问题修复实施计划

> **给执行者：** 未经用户授权子代理时，读取 `executing-plans.md` 并在当前会话逐任务串行实施；只有用户明确授权子代理时才读取 `subagent-driven-development.md`。所有步骤使用复选框跟踪。

**目标：** 在不另建第二套设计或决策中心的前提下，修复独立全面复核确认的全部问题，使实现、测试、工作簿、版本和追踪共同符合2026-08-22权威设计。

**架构：** 继续采用权威设计第3节方案B“统一判断中心”。现金范围和业务组成在分类前闭合；`decision_policy.py` 是证据组合与动作路由唯一入口；运行状态、SQLite、留痕和工作簿消费同一正式决定；工作簿承担最终人工闭环。

**技术栈：** Windows、Python 3、标准库、pytest、SQLite、XlsxWriter、OpenPyXL；不新增依赖。

## 全局约束

- 唯一业务设计：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`，本轮四项严重错误纠偏后的SHA-256为`7F7D706F1B5F9D7823B2902A72EA446D261585516595892951C04E7EA34D7640`。
- 唯一项目目录：`D:\BaiduSyncdisk\workbuddy skills\cas-cashflow-direct`。
- 当前12个已修改测试文件是权威设计红线，禁止回退、覆盖、删除或放宽断言。
- 修改任何已有文件前，先备份到`C:\Users\27651\BackUp\cas-cashflow-direct_时间戳\`并逐文件核对SHA-256。
- 中文Markdown、Python、JSON和CSV使用UTF-8；项目校验要求的运行文件使用UTF-8 with BOM。
- 阶段4不得读取、列举、复制、散列或使用天微真实文件；阶段5才读取并实际运行通用真实验收。
- 不使用子代理，不新增依赖，不创建第二套判断中心，不增加客户专用规则。
- 用户已授权：Requirement Workflow全部阶段完成、检查点4通过且最终新鲜验证无阻断问题后，提交并推送到`origin/main`；此前不提交、不推送。
- 不删除或移动任何文件。`results.tsv`和历史ADR只进入待处理清单，取得用户单独确认后才能移动到回收站或重命名。
- 当前分支为`main`。因12个未提交红线测试只存在于当前工作区，用户已在检查点3明确同意在当前本地`main`工作区实施；实施阶段不提交或推送，检查点4通过后按任务13提交并推送。

---

### 任务1：冻结基线、统一备份和执行门禁

**文件：**

- 读取：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- 读取：`docs/requirements/2026-08-23-cas-cashflow-direct全面复核问题修复-requirement.md`
- 读取：`docs/reviews/2026-08-23-cas-cashflow-direct复核问题实施追踪.md`
- 备份：本计划后续列出的全部现有源码、测试、配置、说明和上下文文件
- 生成：`C:\Users\27651\BackUp\cas-cashflow-direct_时间戳\backup-manifest.tsv`

**接口：**

- 输入：当前工作区、权威设计哈希、12个现有红线测试差异。
- 输出：逐文件源路径、备份路径、源SHA-256、备份SHA-256和一致性结果。

- [ ] **步骤1：确认执行位置和保护边界**

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
git rev-parse --show-toplevel
git branch --show-current
git status --short
git diff --name-only -- tests
```

预期：根目录和本计划一致；分支为`main`；现有测试差异恰好是已保护的12个文件；未出现意外源码修改。

- [ ] **步骤2：核验权威文件哈希**

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md'
```

预期：`7F7D706F1B5F9D7823B2902A72EA446D261585516595892951C04E7EA34D7640`。

- [ ] **步骤3：备份全部预计修改的现有文件并逐项比对哈希**

预计范围至少包括：`.gitignore`、`CONTEXT.md`、`README.md`、`SKILL.md`、`references/直接法分类规则.json`、`src/cashflow_direct/`下本计划列出的文件，以及所有拟修改测试文件。使用PowerShell复制，不覆盖旧备份批次；清单每一行必须满足源哈希等于备份哈希。

- [ ] **步骤4：复核当前红线基线，不修改测试**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
```

预期：复现当前已记录的30个设计红线失败；如失败集合发生变化，先定位漂移，不进入任务2。

- [ ] **步骤5：记录阶段结果，不执行Git提交**

验收：备份清单完整、哈希全部一致、工作区保护边界无漂移。

---

### 任务2：统一证据质量、来源独立性与动作表

**文件：**

- 修改：`src/cashflow_direct/decision_policy.py`
- 修改：`src/cashflow_direct/evidence.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`references/直接法分类规则.json`
- 测试：`tests/test_decision_policy.py`
- 测试：`tests/test_classification.py`
- 测试：`tests/test_evidence.py`

**接口：**

- 消费：`EvidenceSourceAssessment`、`EvidenceAssessment`、`OriginalItemState`、`MaterialityLevel`。
- 产出：双向独立性判断、70/90来源数硬校验、与权威设计一致的160格动作结果。

- [ ] **步骤1：保留并补强失败测试**

核心断言必须包括：

```python
assert combine_source_assessments(summary, path).sources_independent is True
assert route_normal_decision(55, OriginalItemState.CONFLICTS, MaterialityLevel.M2).action is DecisionAction.AI_REVIEW
assert route_normal_decision(70, OriginalItemState.BLANK, MaterialityLevel.M0).action is DecisionAction.AUTOMATIC_FILL
assert route_normal_decision(90, OriginalItemState.UNSTANDARDIZABLE, MaterialityLevel.M0).action is DecisionAction.AUTOMATIC_FILL
```

另增加：裸“咨询费、服务费”最高弱质量；只有“应付账款”一级最高弱质量；伪造70分且来源数不是2时拒绝。

- [ ] **步骤2：运行定向测试并确认仍红**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_decision_policy.py tests/test_classification.py tests/test_evidence.py
```

预期：仅权威设计差异相关用例失败，测试本身能够区分新旧行为。

- [ ] **步骤3：实施最小共同根因修复**

双向独立性按任一来源提供另一来源没有的分类事实判断：

```python
def _are_independent(summary, account_path):
    summary_facts = _specific_facts(summary)
    path_facts = _specific_facts(account_path)
    return bool((summary_facts - path_facts) or (path_facts - summary_facts))
```

动作表只改权威设计明确的五格；在统一证据构造或路由入口增加70/90必须为两个独立来源的防御性校验。删除规则数据中使“咨询费、服务费”和一级“应付账款”直接升为中质量唯一候选的旧兜底，不添加反向个案词。

- [ ] **步骤4：运行定向测试至绿**

运行步骤2命令。预期全部通过，且160格枚举逐格唯一。

- [ ] **步骤5：运行邻接回归**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_candidate_classification.py tests/test_classification_routing.py tests/test_ai_and_materiality.py
```

预期：通过；不得使代扣个税专门路线、方向强制检查或原项目举证责任退回旧口径。

---

### 任务3：修复现金腿门禁和多行业务组成

**文件：**

- 修改：`src/cashflow_direct/components.py`
- 修改：`src/cashflow_direct/normalization.py`
- 修改：`src/cashflow_direct/semantic_mapping.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 测试：`tests/test_components.py`
- 测试：`tests/test_pipeline.py`
- 测试：`tests/test_structure_and_mapping.py`

**接口：**

- 消费：`CashScope`、`NormalizedEntry`、`CashRowCleanupRequest`。
- 产出：只由已确认现金行或明确代理现金账户形成的`CashflowComponent`；不唯一时形成`ComponentStructureRequest`。

- [ ] **步骤1：补齐现金门禁和多业务失败案例**

必须覆盖：旧项目、正负旧流量金额、单边文件、被排除账户标签均不能制造现金腿；两条业务600和400必须形成两个业务身份；超过64行时不得整体合并；路径空、断层、错序、别行和仅现金路径均形成明确异常。

```python
assert result.components == ()
assert result.cash_row_cleanup_requests[0].voucher_key == voucher_key
assert [item.cash_delta_cent for item in split_result.components] == [-60000, -40000]
assert len({key for item in split_result.components for key in item.source_keys}) == 2
```

- [ ] **步骤2：运行定向测试并确认红灯**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_components.py tests/test_pipeline.py tests/test_structure_and_mapping.py
```

- [ ] **步骤3：把现金腿识别收口到一个门禁**

`find_cash_row_cleanup_requests`和`build_cashflow_components`共用同一判断：

```python
confirmed_cash = entry_account_key in scope.included_account_keys
explicit_proxy = counterpart_account_key in scope.included_account_keys
may_build = confirmed_cash or explicit_proxy
```

`original_flow_item`、`flow_amount_cent`和被排除账户标签不得参与`may_build`。无法定位时只生成逐行清洗请求，不创建单边组件。

- [ ] **步骤4：按原始行和金额关系形成业务**

先处理同一行对方科目、唯一一一对应、全体对方行金额恰好守恒和唯一组合。没有唯一守恒组合时生成结构请求，不进入`by_item`整单合并。超过64行不再返回“可整体合并”；至少保留逐行候选、金额差和不明确状态，由结构门禁处理。

- [ ] **步骤5：统一非法路径产生方**

在标准化阶段生成`account_path_empty`或`account_path_invalid`，只有输入已经明确提供完整层级时才检查顺序；不得根据摘要或编码重建路径。

- [ ] **步骤6：运行定向测试至绿并复核金额守恒**

运行步骤2命令；另检查每个凭证分配合计等于已确认现金变化，任何`source_key`不重复占用。

---

### 任务4：修复AI输入边界、技术失败终态和结构失败出口

**文件：**

- 修改：`src/cashflow_direct/ai_review.py`
- 修改：`src/cashflow_direct/component_structure_ai.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/models.py`
- 测试：`tests/test_ai_and_materiality.py`
- 测试：`tests/test_structured_ai_review.py`
- 测试：`tests/test_structured_ai_resolution.py`
- 测试：`tests/test_component_structure_ai.py`
- 测试：`tests/test_pipeline.py`

**接口：**

- 消费：普通分类任务、方向/冲减强制任务、`missing_ids`、`invalid_ids`。
- 产出：每个任务的独立提交次数和终态；普通任务最小证据输入；结构任务三次失败后唯一Agent出口。

- [ ] **步骤1：锁定普通任务输入白名单**

普通任务序列化后只允许原始摘要、完整路径和适用的有效规则，不得出现系统候选池、原项目、金额、重要性和其他AI结论：

```python
assert "candidate_item_ids" not in ordinary_payload
assert "original_item" not in ordinary_payload
assert "amount" not in ordinary_payload
assert set(ordinary_payload["business_evidence"]) == {"summary", "account_path"}
```

方向和冲减强制任务继续允许系统过滤后的方向相容候选，且必须显式标记任务类型。

- [ ] **步骤2：增加漏答连续三次和结构失败回归**

```python
assert terminal_state["attempts"] == 3
assert terminal_state["status"] == "no_valid_result"
assert pending_ai_tasks == []
assert structure_state["next_action"] == "agent_confirm_structure"
```

- [ ] **步骤3：运行定向测试确认红灯**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_ai_and_materiality.py tests/test_structured_ai_review.py tests/test_structured_ai_resolution.py tests/test_component_structure_ai.py tests/test_pipeline.py
```

- [ ] **步骤4：统一技术失败登记**

`_register_ai_technical_attempts`以“本批应答任务减去有效任务”登记失败，`missing_ids`、重复、越界和非法结构均进入同一任务计数；每次提交每个任务最多加1。达到3次后写`no_valid_result`，再按当前动作格执行保持、A/B、C、低金额人工或人工决定，不允许通用出口覆盖专门格。

- [ ] **步骤5：结构任务失败后停止继续排程**

`import_component_structure_ai_results`看到任务达到3次后只生成Agent结构确认请求，不再把同一任务写回下一轮结构AI文件；结构不唯一时不得进入语义、评分和分类。

- [ ] **步骤6：运行定向测试至绿**

运行步骤3命令；检查最终工作簿生成前不存在任何`pending`或`waiting_ai`任务。

---

### 任务5：修复公司规则、冲减确认和同类累计

**文件：**

- 修改：`src/cashflow_direct/ai_review.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`src/cashflow_direct/materiality.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 测试：`tests/test_classification_routing.py`
- 测试：`tests/test_materiality_policy.py`
- 测试：`tests/test_pipeline_decision_routing.py`

**接口：**

- 消费：NOTE状态和适用范围、`business_object`、`purpose`、冲减确认资料。
- 产出：适用规则、越界规则强制人工标记、正确同类键、完整M3冲减确认记录。

- [ ] **步骤1：补全规则生命周期测试**

长期有效规则可以影响适用业务；“仅本次采用”只命中当前组件；停用、被替代、冲突未采用、过期和越界规则不得进入普通AI上下文，其中过期、越界和冲突必须形成`company_rule_conflict`人工出口。

- [ ] **步骤2：补全同类键和M3冲减记录测试**

```python
assert key_for(first) != key_for(second)  # 用途相同、业务对象不同
assert confirmation["company"]
assert confirmation["period"]
assert confirmation["affected_count"] >= 1
assert confirmation["affected_amount_cent"] != 0
assert confirmation["future_effect"]
```

- [ ] **步骤3：运行定向测试确认红灯**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_classification_routing.py tests/test_materiality_policy.py tests/test_pipeline_decision_routing.py
```

- [ ] **步骤4：实施最小修复**

活动规则集合只含当前适用的长期规则；仅本次规则由组件ID或本次确认ID精确绑定。`_same_class_key`固定为：

```python
return (
    record.cash_direction,
    record.candidate_item_id,
    record.standard_level1,
    record.business_object or "未细分业务对象",
    record.purpose or "未细分用途或项目",
)
```

M3冲减确认保存权威设计17.4要求的六类事实。B4已完成的A/B结果通过任务ID复用，不重复排程。

- [ ] **步骤5：运行定向测试至绿**

运行步骤3命令；确认B5代扣个税语境仍走17.6专门路线。

---

### 任务6：统一正式状态、版本和运行记录

**文件：**

- 修改：`src/cashflow_direct/versions.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/storage.py`
- 修改：`src/cashflow_direct/models.py`
- 测试：`tests/test_pipeline.py`
- 测试：`tests/test_release_bundle.py`
- 测试：`tests/test_money_and_storage.py`

**接口：**

- 消费：正式分类决定、M3人工路由、当前项目根目录。
- 产出：状态文件和SQLite一致的正式决定；包含全部判断口径的版本包。

- [ ] **步骤1：增加版本缺口和M3持久化失败测试**

```python
assert versions["materiality_and_accumulation"]
assert versions["forced_checks"]
assert state_decision["decision_action"] == db_decision["decision_action"] == "human_decision"
```

模拟任一版本变化后，`assert_current_versions`必须拒绝续跑。

- [ ] **步骤2：运行定向测试确认红灯**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_pipeline.py tests/test_release_bundle.py tests/test_money_and_storage.py
```

- [ ] **步骤3：增加两个独立版本键并提升结构版本**

`current_versions()`至少返回：`schema`、`scoring`、`action_matrix`、`account_mapping`、`company_notes`、`materiality_and_accumulation`、`forced_checks`以及现有规则文件哈希。旧运行记录缺任一键即只读，不补默认值续跑。

- [ ] **步骤4：把M3转待人工写回正式状态**

在生成工作簿副本前完成状态迁移，并通过现有存储事务同时更新SQLite；工作簿只读取迁移后的决定，不再对内存副本做第二次临时改写。

- [ ] **步骤5：运行定向测试至绿**

运行步骤2命令；再模拟旧运行目录，确认错误信息明确指出缺失或不匹配的版本键。

---

### 任务7：完成重复事项、非法输入和工作簿公式闭环

**文件：**

- 修改：`src/cashflow_direct/duplicates.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/validation.py`
- 修改：`src/cashflow_direct/trace_output.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 测试：`tests/test_workbook_output.py`
- 测试：`tests/test_duplicates.py`
- 测试：`tests/test_final_readiness.py`
- 测试：`tests/test_trace_output_filtering.py`
- 测试：`tests/test_workbook_decision_trace.py`

**接口：**

- 消费：正式人工批次、疑似重复组、排除原因、语义原文位置、工作簿模型。
- 产出：不会崩溃且可在Excel中直接闭环的唯一工作簿。

- [ ] **步骤1：保留A-1和A-14红线并补充公式验证**

未决定重复组生成成功；人工在“重要待复核事项”选择项目后，重复表项目和调整金额通过公式随之更新；非法摘要或路径事项选择项目或明确排除后，首页状态、正表和勾稽自动更新。

- [ ] **步骤2：补充排除原因和列顺序测试**

任何`excluded=True`而原因空白均使`validate_final_readiness`失败。全量分类留痕表头严格按：

```python
("来源文件", "来源工作表", "来源单元格",  # 来源定位
 "日期", "凭证号", "原始摘要",           # 原始业务信息
 # 标准化与映射 → 系统判断 → AI判断 → 人工处理 → 技术字段
)
```

原始字段连续，技术字段位于末尾并默认隐藏。

- [ ] **步骤3：运行定向测试确认红灯**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_workbook_output.py tests/test_duplicates.py tests/test_final_readiness.py tests/test_trace_output_filtering.py tests/test_workbook_decision_trace.py
```

- [ ] **步骤4：修复重复组项目联动**

`DuplicateGroup`允许项目尚未决定；重复表使用隐藏的最终项目技术列，从人工批次按组件或批次键公式查得最终项目，再以该项目形成调整。Python侧计算遇到未决定组只保留，不以空字符串查询`item_by_id`。

- [ ] **步骤5：把可保留金额的非法输入纳入同一人工批次**

资料异常原因、原始金额和全部标准项目选择进入“重要待复核事项”；人工确认项目是唯一完成门禁。选择前金额不进正表，选择项目后只进所选项目，选择明确排除后不进正表。

- [ ] **步骤6：补原文位置、真实冲突留痕和统一排除校验**

摘要、路径的语义字段同时保存文字和值的原始位置；来源冲突使用真实`independent_source_count`和`sources_independent`；生成工作簿前集中调用排除原因校验。

- [ ] **步骤7：运行定向测试至绿并检查实际公式**

运行步骤3命令；用OpenPyXL以`data_only=False`检查公式引用，以Microsoft Excel重算后再用`data_only=True`核对缓存值和最终状态。

---

### 任务8：补齐摘要语义、通用真实验收和测试矩阵

**文件：**

- 修改：`src/cashflow_direct/semantic_mapping.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`tests/test_structured_ai_review.py`
- 修改：`tests/test_tianwei_acceptance.py`
- 修改：`tests/test_qingping_regression.py`
- 修改：其他现有测试文件，仅限补齐权威设计第26节直接覆盖

**接口：**

- 消费：摘要原文、结构化语义任务、通用真实验收目录环境变量。
- 产出：否定、不确定、条件性字段；不含客户专用断言的通用验收。

- [ ] **步骤1：补摘要结构字段**

任务和结果至少包含：

```python
{
    "negation": [],
    "uncertainty": [],
    "conditionality": [],
    "source_spans": [],
}
```

每个非空判断均能追到摘要原文位置；这些字段参与候选质量和冲突判断，不能只输出不消费。

- [ ] **步骤2：清除真实验收客户硬编码**

`test_tianwei_acceptance.py`只断言输入识别、金额守恒、原始行唯一使用、来源独立、合法分数、唯一动作、AI队列终态、工作簿结构、勾稽和状态；不得出现企业名、客户科目名、特定金额、行号或特有词。

- [ ] **步骤3：补权威设计第26节直接测试**

覆盖11.5全部正反例、评分数字版本变化拒绝、55分边界、同类拆组、NOTE过期/越界/冲突、留痕禁止等待AI、排除原因、可靠累计M3只组级确认和保持原项目不另设评档抽查。

- [ ] **步骤4：运行普通测试，继续封存天微文件**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_structured_ai_review.py tests/test_qingping_regression.py
```

阶段4不得运行依赖`CAS_CASHFLOW_TIANWEI_DIR`的真实测试；只允许检查测试源码中不存在客户专用断言。

---

### 任务9：登记评分使用位置并完成非破坏性精简

**文件：**

- 修改：`src/cashflow_direct/decision_policy.py`
- 修改：`src/cashflow_direct/materiality.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`references/直接法分类规则.json`
- 修改：`.gitignore`
- 修改：`README.md`
- 修改：`SKILL.md`
- 新建：`docs/reviews/2026-08-23-cas-cashflow-direct评分与入口登记.md`
- 新建：`docs/reviews/2026-08-23-cas-cashflow-direct全面复核问题闭环.md`
- 新建：`docs/reviews/2026-08-23-cas-cashflow-direct待删除移动清单.md`

**接口：**

- 消费：全仓搜索结果、A/B/S追踪矩阵、所有前序测试结果。
- 产出：评分/动作/自动修改入口登记、35项闭环、待删除移动清单。

- [ ] **步骤1：扫描全部旧值和隐藏入口**

```powershell
rg -n '15|30|40|70|effective_materiality|effective_level|allowed_operations|apply_duplicate_decisions|FALLBACK|automatic_change|automatic_fill|ai_review|human_decision' src tests references README.md SKILL.md
```

逐个判断命中是金额、设计合法分数、历史说明还是未登记旧入口；不能机械替换数字。

- [ ] **步骤2：实施能够证明安全的非破坏性精简**

删除无生产调用的`effective_materiality`；把始终等于`single_level`的`effective_level`收敛为单一字段并同步所有消费方；复用已加载的`RulePack`，不在同一分类流程重复读取；删除不会产生候选的空`FALLBACK`规则数据。保留兼容对象未查清的`legacy_key`、`apply_duplicate_decisions`和`allowed_operations`。

- [ ] **步骤3：只放行本轮正式文档进入版本控制范围**

修改`.gitignore`时不解除整个`docs/`。只为本轮需求、实施追踪、实施计划、评分入口登记和问题闭环添加精确例外；其他含客户资料或历史过程文档继续忽略。

- [ ] **步骤4：形成待删除/移动清单，不执行删除或移动**

清单至少包含根目录`results.tsv`、重复编号ADR、兼容对象未明的历史接口，并记录路径、现有用途搜索、建议动作、影响和恢复方式。停下等待用户单独确认，不用永久删除命令。

- [ ] **步骤5：同步README现行说明、Changelog并建立35项闭环**

README、SKILL只写最终实际行为，不复制长篇设计。必须在README“更新记录”顶部新增`2026-08-23`本轮记录，至少写明六个阻断问题、其余设计差异、工作簿闭环、版本迁移、测试总数、天微真实验收实际状态和仍需人工处理事项；测试数字只能取本轮最终命令输出。闭环表每项包含设计节号、修改文件、测试名、实际结果和证据位置；未验证项不得写通过。

- [ ] **步骤6：运行文档、编码和使用点检查**

```powershell
python scripts/validate_skill.py
git diff --check
rg -n '\uFFFD|\?\?\?' CONTEXT.md README.md SKILL.md docs src tests references
```

预期：`SKILL_VALID=True`；无空白错误、乱码或未登记入口。

---

### 任务10：修复真实工作簿四项严重错误并建立验收红线

**文件：**

- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/differences.py`
- 测试：`tests/test_workbook_output.py`
- 测试：`tests/test_differences.py`
- 测试：`tests/test_tianwei_acceptance.py`

**接口：**

- 消费：权威设计第29节、正式决定、待人工批次、原项目、系统候选和真实摘要结果。
- 产出：金额型U列、纯项目清单AK列、每行首项一致的AL列、边界正确的差异明细和非退化真实验收结论。

- [ ] **步骤1：先写四项错误的失败测试**

覆盖：U列为数值且金额格式与H、I、K列一致；AK列不得出现“采用系统首选项目”；每一行AL下拉首项均为“采用系统首选项目”；系统候选为空时使用原标准项目作为系统首选回退；确无首选的非法输入例外行选择该操作项时必须提示改选且不得算完成；待人工业务不得进入差异明细；内部转账显示“不适用评分”并保留匹配转账证据；真实摘要结果全部为空候选、空事实或最终70/90分均为零时，真实验收必须失败并给出原因。

- [ ] **步骤2：运行定向测试，确认新增红线能复现实际错误**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q tests/test_workbook_output.py tests/test_differences.py tests/test_tianwei_acceptance.py
```

预期：只出现与上述四项严重错误和退化摘要验收有关的失败；如失败指向其他共同根因，先更新根因记录和计划再修改实现。

- [ ] **步骤3：在共同入口做最小修复**

`pipeline.py`在生成待人工批次时使用“正式系统候选优先、原标准项目兜底”；`workbook_output.py`直接从分币金额生成U列数值，AK只列真实可选项目，AL统一把“采用系统首选项目”放在第一项；确无首选的非法输入例外行必须保留在同一工作簿，并在选择该操作项时提示改选。`differences.py`保持权威设计第21.7节边界，只列系统/AI已决定的实际填入、修改或排除，内部转账不参与评分且展示已匹配证据。不得把待人工业务塞进差异明细来强行对平。

- [ ] **步骤4：建立真实摘要非退化验收**

真实验收读取正式摘要结果后，至少核对任务号闭合、非空候选或分类事实占比、正式分数分布和动作分布。全部摘要为空、全部被降为低质量占位，或应当存在冲突组合却70/90分均为零时，必须判定验收失败，不能生成“通过”结论。

- [ ] **步骤5：运行定向测试至绿并复核真实工作簿结构**

运行步骤2命令；再用OpenPyXL检查U列值类型和格式、AK内容、AL数据验证公式、差异明细行类型及内部转账证据。若真实摘要尚未正式完成，只能记录为“待正式摘要后验收”，不得沿用保守占位结果。

---

### 任务11：阶段4普通测试总验收

**文件：**

- 验证：全部源码、普通测试、说明和运行时资料
- 更新：`docs/reviews/2026-08-23-cas-cashflow-direct全面复核问题闭环.md`

**接口：**

- 消费：任务2至10的完整改动。
- 产出：阶段4普通验收证据；不得包含天微真实文件结果。

- [ ] **步骤1：运行全部普通测试**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q --ignore=tests/test_tianwei_acceptance.py
```

预期：全部通过，0失败；不得靠跳过新增红线测试实现。

- [ ] **步骤2：运行动作表枚举和关键最小案例**

分别复现：未决定重复组、缺失现金腿、多业务600/400、三次漏答、非法输入同工作簿闭环、55分M2、70/90分M0。每个结果写入闭环报告。

- [ ] **步骤3：运行项目校验**

```powershell
python -m compileall -q src tests scripts
python scripts/validate_skill.py
git diff --check
```

- [ ] **步骤4：执行编码、依赖和秘密扫描**

验证UTF-8/BOM、Unicode替换字符、未批准依赖、绝对客户路径、企业名和疑似密钥均无新增问题。

- [ ] **步骤5：复核工作区边界**

确认所有源码修改都有备份；12个原始红线测试没有丢失；没有天微文件、运行产物、客户工作簿或备份进入项目变更。

---

### 任务12：阶段5真实文件验收与检查点4

**文件：**

- 读取：`C:\Users\27651\Desktop\真实测试案例\天微`中用户指定的两个真实文件
- 修改：`tests/test_tianwei_acceptance.py`，仅在通用断言确有缺口时修改
- 更新：`docs/reviews/2026-08-23-cas-cashflow-direct全面复核问题闭环.md`

**接口：**

- 消费：阶段4已全绿实现、真实输入文件和通用验收测试。
- 产出：真实输入指纹、实际运行目录、测试结果、工作簿状态和差异结论。

执行纠偏：旧摘要结果被任务10新增的非退化门禁拒绝后，阶段5不得调用模型补造结果，也不得继续生成“真实验收通过”工作簿。本阶段以“门禁有效、旧结论撤销、真实验收待完成”收口；代码修复仍可在自动验收和独立复核通过后进入阶段6交付。

- [ ] **步骤1：确认两个真实文件并记录输入指纹**

只在阶段5执行。记录文件名、大小和SHA-256；不得把原始文件复制到项目。

- [ ] **步骤2：运行真实验收测试**

```powershell
$env:PYTHONPATH='src'
$env:CAS_CASHFLOW_TIANWEI_DIR='C:\Users\27651\Desktop\真实测试案例\天微'
python -m pytest -q tests/test_tianwei_acceptance.py
```

预期：通用不变量全部通过；如失败，只修共同机制，不增加客户名称、金额、行号或关键词特例。

- [ ] **步骤3：检查最终工作簿**

核对工作表、公式、隐藏技术列、人工选择区、AI终态、现金勾稽、状态和输入哈希。尚有必要人工事项时允许“待完成人工确认”，不得冒充“最终可使用”。

- [ ] **步骤4：重新运行全量测试和校验**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

- [ ] **步骤5：完成检查点4材料**

逐项填完35项闭环、阶段4普通验收、阶段5真实验收、未决人工事项、待删除移动清单和工作区变更证据，交用户确认后再进入阶段6交付。

---

### 任务13：阶段6最终交付、提交和推送

**文件：**

- 复核：`README.md`、`SKILL.md`、`CONTEXT.md`、全部源码、测试和本轮正式文档
- 提交：仅本轮经核对的程序、测试和非客户正式文档
- 推送：`origin/main`

**接口：**

- 消费：检查点4确认、全量测试结果、技能校验结果、README Changelog和变更清单。
- 产出：可追溯的本地提交和与其一致的远端`origin/main`。

- [ ] **步骤1：读取完成分支规则并执行最终发布前复核**

读取`finishing-a-development-branch.md`。确认README顶部Changelog已经记录本轮实际结果，且没有保留旧测试数字作为当前结论。

- [ ] **步骤2：再次执行最终验证**

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

预期：全量测试0失败，`SKILL_VALID=True`，差异格式检查通过。

- [ ] **步骤3：核对提交边界**

```powershell
git status --short
git diff --stat
git diff -- README.md
git ls-files --others --exclude-standard
```

逐项排除真实案例、客户工作簿、运行目录、SQLite、备份、缓存、日志和一次性评估产物。只纳入本轮源码、测试、README、SKILL、CONTEXT以及精确放行的正式需求、追踪、计划和闭环文档。

- [ ] **步骤4：创建单一提交**

```powershell
git add -- `
  .gitignore CONTEXT.md README.md SKILL.md `
  'references/直接法分类规则.json' `
  src/cashflow_direct/models.py `
  src/cashflow_direct/components.py `
  src/cashflow_direct/normalization.py `
  src/cashflow_direct/semantic_mapping.py `
  src/cashflow_direct/decision_policy.py `
  src/cashflow_direct/evidence.py `
  src/cashflow_direct/classification.py `
  src/cashflow_direct/ai_review.py `
  src/cashflow_direct/component_structure_ai.py `
  src/cashflow_direct/materiality.py `
  src/cashflow_direct/pipeline.py `
  src/cashflow_direct/storage.py `
  src/cashflow_direct/versions.py `
  src/cashflow_direct/duplicates.py `
  src/cashflow_direct/workbook_output.py `
  src/cashflow_direct/validation.py `
  src/cashflow_direct/trace_output.py `
  tests/test_ai_and_materiality.py `
  tests/test_classification.py `
  tests/test_classification_routing.py `
  tests/test_components.py `
  tests/test_component_structure_ai.py `
  tests/test_decision_policy.py `
  tests/test_evidence.py `
  tests/test_final_readiness.py `
  tests/test_materiality_policy.py `
  tests/test_money_and_storage.py `
  tests/test_pipeline.py `
  tests/test_pipeline_decision_routing.py `
  tests/test_qingping_regression.py `
  tests/test_release_bundle.py `
  tests/test_structure_and_mapping.py `
  tests/test_structured_ai_resolution.py `
  tests/test_structured_ai_review.py `
  tests/test_tianwei_acceptance.py `
  tests/test_trace_output_filtering.py `
  tests/test_workbook_decision_trace.py `
  tests/test_workbook_output.py `
  'docs/requirements/2026-08-23-cas-cashflow-direct全面复核问题修复-requirement.md' `
  'docs/reviews/2026-08-23-cas-cashflow-direct复核问题实施追踪.md' `
  'docs/superpowers/plans/2026-08-23-cas-cashflow-direct全面复核问题修复实施计划.md' `
  'docs/reviews/2026-08-23-cas-cashflow-direct评分与入口登记.md' `
  'docs/reviews/2026-08-23-cas-cashflow-direct全面复核问题闭环.md' `
  'docs/reviews/2026-08-23-cas-cashflow-direct待删除移动清单.md'
git commit -m "fix: 按权威设计完成现金流直接法复核闭环"
```

禁止使用`git add .`或`git add -A`；必须逐文件暂存。提交后再次查看提交文件清单。

- [ ] **步骤5：推送并核对远端**

```powershell
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

预期：本地HEAD与远端`main`提交号完全一致。推送失败时只处理实际错误，不改写历史、不强推。

- [ ] **步骤6：交付报告**

向用户报告业务修复结果、全量测试、真实验收、README Changelog、提交号、远端一致性、保留未删除项目和恢复备份路径。

## 问题覆盖索引

| 任务 | 覆盖问题 |
|---|---|
| 任务2 | A-4、A-5、A-7、A-20、B2、B3、B5、B6、S-10 |
| 任务3 | A-2、A-6、A-10、B1、S-7 |
| 任务4 | A-8、A-9、S-3 |
| 任务5 | A-11、A-12、A-13、B4、S-4 |
| 任务6 | A-16、S-6 |
| 任务7 | A-1、A-14、A-17、A-21、A-22 |
| 任务8 | A-3、A-19、A-23 |
| 任务9 | A-15、A-18、A-24 |
| 任务10 | 权威设计第29节四项严重错误、退化摘要验收、差异明细边界纠偏 |

## 自我复核

- [ ] A-1至A-24、B1至B6、S-3、S-4、S-6、S-7、S-10均映射到至少一个任务。
- [ ] 六个阻断问题均有最小复现、代码落点和回归测试。
- [ ] 权威设计第6至26节及第29节均有实现或验收落点。
- [ ] 计划内容均为可直接执行的步骤，不含空白代办、推迟语句或未定义接口。
- [ ] 任务间类型和状态名称与当前代码一致；拟新增版本键在任务6统一定义。
- [ ] 没有新增依赖、子代理、客户特例或未经确认的文件删除；Git提交和推送只在检查点4通过后的任务13执行。
- [ ] 阶段4和阶段5的真实文件边界没有混用。

## 执行方式

本轮按用户当前授权采用当前会话串行执行，不使用子代理。由于红线测试只存在于当前未提交工作区，计划不创建隔离工作树；检查点3已确认在当前本地`main`工作区实施。检查点4通过前不提交、不推送；检查点4通过并完成最终验证后，按任务13创建单一提交并推送`origin/main`。
