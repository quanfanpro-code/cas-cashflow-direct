# cas-cashflow-direct 自动基线与双语义纠偏实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让客户金额、系统自动调整、人工调整形成可解释金额桥，并由固定关系规则正确形成摘要和完整科目路径的候选及质量。

**Architecture:** 复用现有金额汇总、摘要入口和科目词典入口，各自只增加一个共同根因层：`statement.py`集中形成金额层，`summary_semantics.py`集中绑定摘要关系，`account_dictionary.py`集中解释完整路径。`pipeline.py`只编排三者，`workbook_output.py`只展示金额桥和工作簿公式，不复制业务判断。

**Tech Stack:** Python 3.11+、标准库、pytest、xlsxwriter、openpyxl、Microsoft Excel重算；不新增依赖。

## Global Constraints

- 修改任何既有文件前，先备份到`C:\Users\27651\BackUp\cas-cashflow-direct_20260824_012511\`并核对SHA-256。
- 中文Markdown、JSON和代码注释使用UTF-8；Markdown和JSON优先UTF-8 with BOM，写后扫描替换字符。
- 不启动或调用LM Studio，不安装HanLP、LTP、结巴、PyTorch或Transformers。
- 不恢复可靠同类组、累计重要性人工确认或相关Sheet。
- 不增加科目路径覆盖Sheet；覆盖情况写入现有`科目语义词典说明.md`。
- 不删除历史运行、真实输入或旧工作簿。
- 测试先行；收尾不重复执行十万行性能测试，只做普通回归和一次真实样本冒烟。

---

### Task 1: 固化自动基线与人工调整金额层

**Files:**
- Modify: `src/cashflow_direct/statement.py`
- Modify: `src/cashflow_direct/models.py`
- Test: `tests/test_statement.py`

**Interfaces:**
- Produces: `StatementLayers(automatic_baseline, detail_reconstruction, system_adjustments, manual_adjustments)`。
- Produces: `build_statement_layers(components, decisions, entries, internal_transfers, rules, existing, opening_cent, fx_cent)`。
- Consumes: `ClassificationDecision.original_standard_item_id`、`decision_source`、`resolved`、`excluded`和`cash_delta_cent`。

- [x] **Step 1: 写自动基线失败测试**

在`tests/test_statement.py`加入以下行为测试：

```python
def test_customer_statement_is_the_baseline_and_pending_keeps_original():
    layers = build_statement_layers(
        components=(component("C1", -10_000, original="CFO-07"),),
        decisions=(pending_decision("C1", original="CFO-07", candidate="CFO-05"),),
        existing=existing_statement(CFO_07=10_000),
    )
    assert layers.system_adjustments["CFO-07"] == 0
    assert layers.automatic_baseline.values["CFO-07"] == 10_000

def test_automatic_change_subtracts_original_and_adds_target():
    layers = build_statement_layers(
        components=(component("C1", -10_000, original="CFO-07"),),
        decisions=(automatic_change("C1", original="CFO-07", target="CFO-05"),),
        existing=existing_statement(CFO_07=10_000),
    )
    assert layers.system_adjustments["CFO-07"] == -10_000
    assert layers.system_adjustments["CFO-05"] == 10_000

def test_manual_change_only_changes_manual_adjustment():
    layers = build_statement_layers(
        components=(component("C1", -10_000, original="CFO-07"),),
        decisions=(manual_change("C1", original="CFO-07", target="CFO-05"),),
        existing=existing_statement(CFO_07=10_000),
    )
    assert layers.automatic_baseline.values["CFO-07"] == 10_000
    assert layers.manual_adjustments["CFO-07"] == -10_000
    assert layers.manual_adjustments["CFO-05"] == 10_000
```

另加内部划转按配对金额减原项目、原项目为空自动补列、没有客户正表时仍能编制三个边界测试。

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -X utf8 -m pytest tests/test_statement.py -q`

Expected: 新接口尚不存在或旧汇总跳过待人工，新增测试失败。

- [x] **Step 3: 实现最小金额层**

在`statement.py`增加只处理叶子项目的调整函数，再统一重算汇总行：

```python
@dataclass(frozen=True, slots=True)
class StatementLayers:
    automatic_baseline: StatementResult
    detail_reconstruction: StatementResult
    system_adjustments: dict[str, int]
    manual_adjustments: dict[str, int]

def _apply_reclassification(values, original_id, target_id, component, rules):
    if original_id:
        values[original_id] -= statement_amount_cent(
            component.cash_delta_cent, rules.item_by_id[original_id].normal_direction
        )
    if target_id:
        values[target_id] += statement_amount_cent(
            component.cash_delta_cent, rules.item_by_id[target_id].normal_direction
        )
```

系统层只消费非人工且已解决的决定；人工层只消费`decision_source == "manual"`。有客户正表时从客户叶子金额起算；无客户正表时从零起算系统已决定业务。内部划转按原项目、实际现金腿方向和`matched_cent`形成减项。所有小计、净额和期末余额均通过现有公式组件统一重算。

- [x] **Step 4: 运行金额层测试并确认绿灯**

Run: `python -X utf8 -m pytest tests/test_statement.py -q`

Expected: 全部通过。

### Task 2: 把三层金额桥写入正表和核对报告

**Files:**
- Modify: `src/cashflow_direct/models.py`
- Modify: `src/cashflow_direct/materiality.py`
- Modify: `src/cashflow_direct/pipeline.py`
- Modify: `src/cashflow_direct/workbook_output.py`
- Test: `tests/test_workbook_output.py`
- Test: `tests/test_pipeline_decision_routing.py`

**Interfaces:**
- Consumes: Task 1的`StatementLayers`。
- Produces: `ComparisonRow.system_adjustment_cent`、`detail_reconstruction_cent`、`detail_gap_cent`。
- Produces: `ReviewBatch.baseline_item_code`，用于人工选择时只在人工调整层减去原项目。

- [x] **Step 1: 写核对报告和人工公式失败测试**

断言“正表核对报告”表头为：

```python
(
    "项目", "客户金额", "系统自动调整", "自动基线", "人工调整",
    "最终金额", "最终差异", "明细重建金额", "原表与明细勾稽差额", "支持组成",
)
```

再构造“客户CFO-07为100元、待人工候选CFO-05”的工作簿，断言D列自动基线仍为100元；选择CFO-05的人工公式必须在人工调整中同时产生CFO-07减100元、CFO-05加100元，不能改变自动基线缓存值。

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -X utf8 -m pytest tests/test_workbook_output.py tests/test_pipeline_decision_routing.py -q`

Expected: 旧报告没有系统自动调整和明细勾稽列，旧人工公式只加目标不减原项目。

- [x] **Step 3: 接入金额层和人工初值**

`pipeline.finalize`先构造Task 1金额层，再用自动基线生成正表。`ComparisonRow`保存六段金额及明细重建差额。`ReviewBatch`增加默认字段`baseline_item_code`，放在数据类末尾以保持已有构造调用兼容。

`workbook_output.py`在技术列末尾增加“原基线项目(技术)”。`系统项目调整(技术)`根据人工选择只负责从原基线项目减项；`目标项目金额(技术)`只负责向选择项目加项。主表人工调整公式结构为：

```text
已导入人工调整初值
+ 按原基线项目汇总的人工减项
+ 按人工目标项目汇总的人工加项
+ 疑似重复人工剔除
```

“系统自动调整”列写Task 1固定值；“原表与明细勾稽差额”只做展示，不参与任何正表公式。

- [x] **Step 4: 运行工作簿测试并确认绿灯**

Run: `python -X utf8 -m pytest tests/test_workbook_output.py tests/test_pipeline_decision_routing.py tests/test_statement.py -q`

Expected: 全部通过，自动基线与人工调整不串层。

### Task 3: 修复摘要的动作、对象和修饰关系

**Files:**
- Modify: `src/cashflow_direct/summary_semantics.py`
- Modify: `references/摘要语义规则.json`
- Test: `tests/test_summary_semantics.py`

**Interfaces:**
- Produces: `_normalize_business_relations(summary, facts)`，在候选和质量计算前修正复合名词、员工往来、资产交易和个税服务对象。
- Keeps: `analyze_summary`、`build_summary_agent_task`和`merge_summary_agent_slots`外部签名不变。

- [x] **Step 1: 写四类通用失败测试及变体**

```python
@pytest.mark.parametrize("summary", [
    "付农民工资专用账户款", "转农民工工资专户资金", "支付工资保证金",
])
def test_wage_account_phrase_is_not_staff_compensation(summary):
    result = analyze_summary(summary, rules)
    assert "CFO-05" not in result.candidate_item_ids
    assert result.quality.value < 45

@pytest.mark.parametrize("summary", [
    "收到员工退回借款", "收回职工借支", "备用金退回",
])
def test_employee_advance_return_is_not_borrowing(summary):
    assert "CFF-02" not in analyze_summary(summary, rules).candidate_item_ids

def test_equipment_purchase_is_not_monopolized_by_goods_word():
    result = analyze_summary("支付设备采购货款", rules)
    assert result.candidate_item_ids != ("CFO-04",)

@pytest.mark.parametrize("summary", [
    "缴纳分红个税", "代缴股权转让个人所得税", "缴纳个人所得税",
])
def test_individual_tax_without_employee_service_is_not_staff_strong(summary):
    result = analyze_summary(summary, rules)
    assert not (result.candidate_item_ids == ("CFO-05",) and result.quality.value == 45)
```

另加“支付本月职工工资且明确本企业职工服务对象”仍可唯一形成职工候选，以及动作加孤立对象最高25分的正向边界测试。

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -X utf8 -m pytest tests/test_summary_semantics.py -q`

Expected: 旧规则把工资专户判为CFO-05/45，或把通用对象重复当决定性事实。

- [x] **Step 3: 实现关系归一和固定质量约束**

在词典增加“账户/专户/保证金”“员工往来/借支”“长期资产属性”“个税服务对象”等受控值；在`_normalize_nested_actions`后调用`_normalize_business_relations`。该函数只重标或删除错误绑定的事实，不直接写项目。

把强质量判断改为：唯一候选、存在现金动作和业务对象，且`purpose`、`attribute`、`business_relation`或明确`counterparty_role`中至少一个独立决定性事实命中；`business_object`不再列入`strong_values`。无法绑定的关系进入现有受限Agent槽位，Agent返回后仍走同一候选和质量函数。

- [x] **Step 4: 运行摘要和分类相关测试**

Run: `python -X utf8 -m pytest tests/test_summary_semantics.py tests/test_classification.py tests/test_classification_routing.py -q`

Expected: 全部通过。

### Task 4: 让完整科目路径规则取代单段越级命中

**Files:**
- Modify: `src/cashflow_direct/account_dictionary.py`
- Modify: `references/科目语义词典.json`
- Modify: `src/cashflow_direct/pipeline.py`
- Test: `tests/test_account_dictionary.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `AccountPathSemanticResult(account, status, concepts, candidate_item_ids, inflow_candidate_item_ids, outflow_candidate_item_ids, quality, semantic, basis, unresolved_slots)`。
- Produces: `analyze_account_path(account_path, rules, agent_concepts=())`。
- Produces: `build_account_agent_task(result)`和`merge_account_agent_concepts(result, payload, rules)`。
- Keeps: 分类继续通过`AccountDictionary.lookup_path`和`score_dictionary_hits`消费已经由固定程序形成的完整路径结果。

- [x] **Step 1: 写路径关系与Agent越权失败测试**

```python
def test_management_equipment_does_not_use_equipment_segment_as_long_asset():
    result = analyze_account_path("管理费用_设备款", rules)
    assert "CFI-06" not in result.candidate_item_ids

def test_production_labor_welfare_is_not_purchase_goods():
    result = analyze_account_path("生产成本_鉴定成本_人工_福利", rules)
    assert result.candidate_item_ids == ("CFO-05",)

def test_same_equipment_word_changes_with_parent_path():
    assert analyze_account_path("固定资产_运输设备", rules).candidate_item_ids == ("CFI-06",)
    assert analyze_account_path("管理费用_办公设备维修", rules).candidate_item_ids == ("CFO-07",)
    assert not analyze_account_path("生产成本_设备折旧", rules).candidate_item_ids

def test_account_agent_cannot_return_item_or_confidence():
    with pytest.raises(ValueError, match="不得返回项目、质量或分数"):
        merge_account_agent_concepts(result, {"item_id": "CFI-06", "confidence": "high"}, rules)
```

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -X utf8 -m pytest tests/test_account_dictionary.py tests/test_pipeline.py -q`

Expected: 旧`lookup_path`按反向科目段命中，`score_dictionary_hits`直接把词典confidence换分。

- [x] **Step 3: 把科目词典改为节点概念和完整路径规则**

`科目语义词典.json`结构改为：

```json
{
  "schema_version": "3.0.0",
  "concepts": [
    {"concept": "expense_context", "terms": ["管理费用", "销售费用", "制造费用"]},
    {"concept": "long_asset", "terms": ["固定资产", "无形资产", "在建工程", "设备"]},
    {"concept": "repair", "terms": ["维修", "修理", "修缮"]},
    {"concept": "staff_cost", "terms": ["职工薪酬", "工资", "人工", "福利", "社保", "公积金"]},
    {"concept": "non_cash", "terms": ["折旧", "摊销", "结转"]}
  ],
  "path_rules": [
    {"rule_id": "PATH-STAFF", "all": ["staff_cost"], "without": ["non_cash"], "outflow": ["CFO-05"]},
    {"rule_id": "PATH-EXPENSE-REPAIR", "all": ["expense_context", "repair"], "outflow": ["CFO-07"]},
    {"rule_id": "PATH-LONG-ASSET", "level1": ["固定资产", "无形资产", "在建工程"], "all": ["long_asset"], "outflow": ["CFI-06"]}
  ]
}
```

补齐税费、材料商品、经营费用、职工薪酬、长期资产、投资、借款、利息、资本及非现金限制等通用概念。质量只由固定程序按候选唯一性、路径层级完整性、决定性概念和冲突形成；JSON和Agent均不提供分数。

`AccountDictionary.lookup_path`删除通用层和运行时层的单段回退，只允许完整路径条目；企业特殊规则也必须精确完整路径。`scan_accounts`先分析全部路径，固定规则完整解释的结果直接进入当前状态，只有`unresolved_slots`非空才生成任务。

- [x] **Step 4: 改成受限路径Agent导入**

路径任务只列`level_index`、`node_text`、已识别概念和允许概念。导入只接受原节点文字、概念及关系；发现`item_id`、候选项目、`confidence`或分数立即拒绝。合并后调用`analyze_account_path`重算并存储固定结果，旧格式结果不得导入新运行。

- [x] **Step 5: 运行路径和分类测试**

Run: `python -X utf8 -m pytest tests/test_account_dictionary.py tests/test_pipeline.py tests/test_classification.py tests/test_evidence.py -q`

Expected: 全部通过；两个指定越级命中被阻断。

### Task 5: 生成不参与分类的路径覆盖说明

**Files:**
- Modify: `src/cashflow_direct/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `build_account_coverage(components, path_results)`，按业务组成最弱路径状态去重统计。
- Consumes: Task 4的`AccountPathSemanticResult.status`。

- [x] **Step 1: 写覆盖统计失败测试**

构造三个业务组成：一个固定规则完整解释、一个部分解释、一个未识别，其中一个组成包含两条路径。断言：

```python
assert coverage["component_count"] == 3
assert sum(group["component_count"] for group in coverage["groups"]) == 3
assert sum(group["amount_cent"] for group in coverage["groups"]) == sum(
    abs(component.cash_delta_cent) for component in components
)
```

再断言修改覆盖说明字段不会改变`classify_all`结果，且工作簿Sheet数不增加。

- [x] **Step 2: 运行测试并确认红灯**

Run: `python -X utf8 -m pytest tests/test_pipeline.py -q`

Expected: 旧说明没有覆盖汇总，或多路径业务会重复统计。

- [x] **Step 3: 实现覆盖说明**

对每个业务组成按`未识别 > 冲突 > 部分解释 > Agent补充 > 固定规则完整解释`选择最弱状态，只计一次笔数和绝对金额；路径明细逐路径保留。`_write_dictionary_doc`在正文前写“规则覆盖情况”，列总路径数、各状态路径数、业务组成笔数、绝对金额，以及按金额和频次排序的待补路径前20项。该结构不传入`classification.py`、`decision_policy.py`或完成状态计算。

- [x] **Step 4: 运行管线测试并确认绿灯**

Run: `python -X utf8 -m pytest tests/test_pipeline.py tests/test_workbook_output.py -q`

Expected: 全部通过，仍为十二张表。

### Task 6: 版本、文档、完整回归和一次真实冒烟

**Files:**
- Modify: `src/cashflow_direct/versions.py`
- Modify: `README.md`
- Modify: `SKILL.md`（仅当运行步骤或输入输出契约发生变化）
- Modify: `CONTEXT.md`
- Modify: `C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- Create: `docs/reviews/2026-08-24-cas-cashflow-direct自动基线与双语义纠偏-review.md`

**Interfaces:**
- Produces: 新运行拒绝旧摘要、旧路径Agent答案和旧自动基线状态。
- Produces: README Changelog中本批业务变化、测试证据和不重复十万行性能测试说明。

- [x] **Step 1: 升级版本并更新说明**

升级受影响规则和状态摘要；README用业务语言说明三层金额桥、四类摘要关系、完整路径规则、受限路径Agent及覆盖说明，Changelog记录本次日期、真实样本结果和兼容边界。桌面权威设计第32节记录最终实现证据，不能沿用第31节旧金额缺口结论。

- [x] **Step 2: 运行受影响测试和完整普通回归**

Run: `python -X utf8 -m pytest -q`

Expected: 全部普通测试通过；明确不运行带十万行性能标记或独立性能脚本。

- [x] **Step 3: 运行结构、编码和差异检查**

Run: `python -X utf8 scripts/validate_skill.py`

Run: `python -X utf8 -m compileall -q src tests`

Run: `git diff --check`

并扫描本批修改文件中的Unicode替换字符、乱码问号、密钥样式和旧路径Agent越权字段。

- [x] **Step 4: 运行真实样本冒烟，并在发现内部划转断桥后作一次修正重建**

使用现有真实测试参数新建隔离运行，不改原始文件，不复用旧运行状态。完整执行到最终工作簿，路径Agent如有任务只使用当前受限任务格式；不得启动LM Studio。Microsoft Excel实际重算后检查：

1. 输入文件SHA-256前后一致；
2. 客户金额到自动基线的差额由系统自动调整桥接；75项待人工不再从原项目消失；
3. 农民工资专户样本不再形成职工薪酬45分强候选；
4. 科目语义说明包含可追溯覆盖汇总；
5. 工作簿仍为十二张表，公式错误0、外部链接0、关键下拉和金额格式正确；
6. 真实状态按剩余人工事项和勾稽差额如实显示。

- [x] **Step 5: 写独立复核记录**

复核文档逐项列需求、实现位置、失败测试、通过证据、真实样本证据、未解决人工事项和兼容边界。结论不得把覆盖率当准确率，不得把程序冒烟通过写成工作簿最终可用。

- [x] **Step 6: 提交并推送**

先确认`git status --short`只含本任务文件，再提交：

```text
fix: 修正自动基线与双语义关系判断
```

推送当前`main`到`origin/main`，随后核对本地HEAD、远端HEAD和提交哈希一致。
