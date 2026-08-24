# 存货与成本流转语义补强实施计划

> **For agentic workers:** Read `subagent-driven-development.md` when the user authorizes subagents; otherwise read `executing-plans.md` and implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让完整科目路径正确区分存货外购、成本归集、内部非现金流转和最终营业成本去向，并按本次现金实际支付对象形成候选。

**Architecture:** 复用现有`科目语义词典.json → account_dictionary.py → classification.py → decision_policy.py`单一路径。扩充受控节点概念和完整路径组合，只在固定质量集合中登记两类新的决定性概念；不新增语义引擎、依赖或工作表。

**Tech Stack:** Python 3、pytest、JSON固定规则、openpyxl现有工作簿链路。

## Global Constraints

- 只处理`D:\BaiduSyncdisk\workbuddy skills\cas-cashflow-direct`及桌面权威设计。
- 修改已有文件前使用`C:\Users\27651\BackUp\cas-cashflow-direct_20260824_201807`备份并核验。
- 中文Markdown和JSON使用UTF-8；Markdown优先UTF-8 with BOM，JSON保持现有可解析编码。
- 分类锚定本次现金实际支付对象，不根据最终结转主营业务成本倒推。
- 非现金流转优先阻断；职工、税费、长期资产、投资和借款等现有专用属性优先。
- 不改变评分组合、最低分选择、重要性行动表、增值税附属继承和工作簿结构。
- 不新增模型调用、依赖、客户特例、行业专用规则或跨凭证存货追踪。
- 第四阶段不读取真实样本；第五阶段只运行一次真实样本冒烟；不重复十万行性能测试。
- 全部实现串行完成，不启动子Agent。

---

### Task 1: 用路径构造测试锁定成本流转红线

**Files:**
- Modify: `tests/test_account_dictionary.py`
- Test: `tests/test_account_dictionary.py`

**Interfaces:**
- Consumes: `load_account_semantic_rules(ROOT)`和`analyze_account_path(path, rules)`。
- Produces: 存货外购、生产投入、职工优先和非现金阻断的行为红线。

- [x] **Step 1: 写正向失败测试**

增加参数化测试，逐项断言以下路径的`outflow_candidate_item_ids == ("CFO-04",)`、`quality.value == 45`且没有未识别节点：

```python
(
    "材料采购_直接材料",
    "在途物资_商品采购",
    "原材料_钢材",
    "委托加工物资_加工费",
    "周转材料_包装物采购",
    "生产成本_外协加工费",
    "制造费用_生产用电费",
    "劳务成本_外部劳务费",
    "合同履约成本_外部履约服务费",
    "主营业务成本_外包服务费",
)
```

- [x] **Step 2: 写边界和阻断失败测试**

断言：

```python
assert analyze_account_path("生产成本_人工_福利", rules).outflow_candidate_item_ids == ("CFO-05",)
for path in (
    "生产成本_设备折旧",
    "周转材料_摊销",
    "库存商品_完工结转",
    "发出商品_结转主营业务成本",
    "存货跌价准备_计提减值",
):
    assert not analyze_account_path(path, rules).outflow_candidate_item_ids
assert not analyze_account_path("合同履约成本_差旅费", rules).outflow_candidate_item_ids
assert not analyze_account_path("主营业务成本", rules).outflow_candidate_item_ids
assert not analyze_account_path("库存商品", rules).outflow_candidate_item_ids
```

- [x] **Step 3: 运行RED**

Run: `python -m pytest tests/test_account_dictionary.py -q`

Expected: 新增的外购存货和成本归集正向案例因候选为空或只有25分而失败；既有职工和非现金案例继续通过。

### Task 2: 最小扩充统一词典和固定质量

**Files:**
- Modify: `references/科目语义词典.json`
- Modify: `src/cashflow_direct/account_dictionary.py`
- Test: `tests/test_account_dictionary.py`

**Interfaces:**
- Consumes: 现有`concepts`、`path_rules`、`PATH-NONCASH`和`_fixed_account_quality()`。
- Produces: 新的通用节点概念和仍由`AccountPathSemanticResult`承载的候选、质量及规则留痕。

- [x] **Step 1: 调整父级概念边界**

将制造费用、劳务成本和其他业务成本从普通期间费用父级移出；把生产成本、制造费用、劳务成本、合同履约成本、主营业务成本和其他业务成本统一作为成本链父级。合同履约成本不再同时作为中性成本层级。

- [x] **Step 2: 增加存货和生产投入概念**

在同一个JSON概念表中增加：

```json
{"concept":"inventory_acquisition_parent","terms":["材料采购","在途物资","原材料","委托加工物资","周转材料","包装物","低值易耗品"]},
{"concept":"inventory_state_parent","terms":["库存商品","发出商品","在产品","半成品","产成品","商品进销差价","材料成本差异"],"role":"neutral"},
{"concept":"production_input_detail","terms":["生产用电费","生产用水费","生产用燃料","生产检测费","生产检验费","燃料动力费","燃动费"]}
```

扩充外部生产服务词族，覆盖外协加工、委外加工、外包服务、外部劳务和外部履约服务；扩充非现金词族，覆盖领用、出库、入库、完工、分配、分摊、跌价准备和减值准备。

- [x] **Step 3: 增加完整路径规则**

按现有JSON结构增加或调整：

```json
{"rule_id":"PATH-INVENTORY-ACQUISITION","level1_any":["inventory_acquisition_parent"],"forbid":["non_cash","staff_cost","long_asset_detail"],"outflow_candidate_item_ids":["CFO-04"]},
{"rule_id":"PATH-INVENTORY-STATE-PURCHASE","level1_any":["inventory_state_parent"],"require_any":["purchase_inventory"],"forbid":["non_cash","staff_cost"],"min_levels":2,"outflow_candidate_item_ids":["CFO-04"]},
{"rule_id":"PATH-PRODUCTION-SERVICE","level1_any":["production_parent"],"require_any":["production_service_detail"],"forbid":["non_cash","staff_cost"],"min_levels":2,"outflow_candidate_item_ids":["CFO-04"]},
{"rule_id":"PATH-PRODUCTION-INPUT","level1_any":["production_parent"],"require_any":["production_input_detail"],"forbid":["non_cash","staff_cost"],"min_levels":2,"outflow_candidate_item_ids":["CFO-04"]}
```

保持`PATH-NONCASH`位于停止规则，确保非现金关系先截断其他候选。

- [x] **Step 4: 登记决定性概念**

在`_fixed_account_quality()`现有`decisive`集合中只增加：

```python
"inventory_acquisition_parent",
"production_input_detail",
```

外部服务继续复用现有`production_service_detail`决定性概念，不增加第二套评分。

- [x] **Step 5: 运行GREEN**

Run: `python -m pytest tests/test_account_dictionary.py -q`

Expected: 全部路径语义测试通过；每个新增路径节点的解释角色完整。

### Task 3: 验证正式分类入口和评分组合

**Files:**
- Modify: `tests/test_classification.py`
- Test: `tests/test_classification.py`

**Interfaces:**
- Consumes: `classify_component(component, load_rule_pack(ROOT), load_common_dictionary(ROOT))`。
- Produces: 证明JSON路径语义通过正式双来源分类入口生效的回归测试。

- [x] **Step 1: 写分类入口测试**

构造无客户特征的业务，至少覆盖：

```python
decision = classify_component(
    cashflow_component("支付外协加工款", -100, ("生产成本_外协加工费",)),
    load_rule_pack(ROOT),
    load_common_dictionary(ROOT),
)
assert decision.system_item_id == "CFO-04"
assert decision.account_path_quality == 45
```

另验证`合同履约成本_差旅费`不会仅凭路径形成CFO-04，以及生产人工仍形成CFO-05。

- [x] **Step 2: 运行测试**

Run: `python -m pytest tests/test_classification.py -q`

Expected: 正式分类入口测试全部通过；摘要和路径冲突仍由现有冲突规则处理。

- [x] **Step 3: 运行受影响范围测试**

Run: `python -m pytest tests/test_account_dictionary.py tests/test_classification.py tests/test_candidate_classification.py tests/test_structured_ai_review.py tests/test_structured_ai_resolution.py -q`

Expected: 全部通过，未改变AI或行动表接口。

### Task 4: 同步现行说明和版本边界

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `CONTEXT.md`
- Modify: `C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- Test: `scripts/validate_skill.py`

**Interfaces:**
- Consumes: 已确认需求、设计和实际测试结果。
- Produces: 对用户和后续Agent一致的业务红线、Changelog和旧运行迁移说明。

- [x] **Step 1: 更新现行说明**

明确：存货和成本链只保留业务性质；分类锚定本次支付；最终营业成本去向不直接决定项目；非现金流转优先阻断。

- [x] **Step 2: 更新Changelog**

在README更新记录顶部写入本轮规则覆盖、行为变化、测试结果、真实样本状态和未解决边界，不复用旧测试数字。

- [x] **Step 3: 验证版本迁移**

确认`current_versions()`中的`account_dictionary`哈希随词典变化；不手工增加第二个词典版本常量。

- [x] **Step 4: 运行文档和Skill校验**

Run: `python scripts/validate_skill.py`

Expected: `SKILL_VALID=True`。

### Task 5: 普通全量验证

**Files:**
- Create: `docs/reviews/2026-08-24-cas-cashflow-direct存货与成本流转语义补强-review.md`

**Interfaces:**
- Consumes: Tasks 1至4全部实现和测试结果。
- Produces: 阶段5之前的完整普通回归证据。

- [x] **Step 1: 运行全部普通测试**

Run: `python -m pytest -q`

Expected: 除明确跳过的真实外部资料测试外全部通过；该命令不包含十万行性能标记执行时须核对`pytest.ini`，若包含则显式排除`tests/test_100k_performance.py`。

- [x] **Step 2: 运行质量检查**

Run: `python -m compileall -q src tests`

Run: `git diff --check`

Run: 中文UTF-8严格解码和Unicode替换字符扫描。

Expected: 全部退出码0。

- [x] **Step 3: 记录普通验证结果**

把实际命令、通过数量、跳过数量、子测试数量和未执行十万行性能测试写入复核记录，不提前宣告真实样本通过。

### Task 6: 一次真实样本冒烟和最终交付

**Files:**
- Modify: `docs/reviews/2026-08-24-cas-cashflow-direct存货与成本流转语义补强-review.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 当前桌面真实输入、既有确认文件和全量普通回归通过的实现。
- Produces: 一次隔离运行、结果工作簿、真实覆盖差异和最终交付证据。

- [x] **Step 1: 只运行一次真实样本**

使用现有真实样本入口和新的隔离输出目录，沿用`preflight → confirm-mapping/confirm-cash → classify → 必要的既有确认结果导入 → finalize`。不得重跑十万行性能测试，不调用LM Studio或其他模型服务。

- [x] **Step 2: 核对真实不变量**

记录输入SHA-256、业务组成数、摘要和路径覆盖、45/50/55/70/90分布、自动修改、增值税附属跟随、内部划转、待人工数量、现金勾稽和工作簿十二张表。重点检查新增成本链候选是否有正面业务对象，非现金结转是否仍无候选。

- [x] **Step 3: 验收审查**

逐项对照需求、设计和本计划；修复任何正常、边界或错误路径缺口后重新运行受影响测试。把发现、修复、验证和残余风险写入复核记录。

- [x] **Step 4: 最终验证**

再次运行定向测试、全部普通回归、Skill校验、JSON解析、编译、编码扫描和`git diff --check`，使用新鲜结果更新README Changelog。

- [x] **Step 5: 提交和推送**

用户已经授权Requirement Workflow完成并通过验收后提交并推送。最终确认工作区只包含本轮代码、测试和文档，不包含客户输入、结果工作簿、运行目录或备份；然后创建一个提交并推送`origin/main`。

## 计划自审

1. 需求中的正向、边界、错误路径、存货范围、非现金阻断、人工门禁和文档交付均有对应任务；
2. 没有TBD、TODO、占位实现或新增依赖；
3. 所有实现复用现有`analyze_account_path`和`classify_component`接口；
4. 真实样本只在Task 6运行一次，十万行性能测试明确不运行；
5. 用户已确认串行执行和最终提交推送，不需要重复选择执行方式或交付动作。

## 检查点3结论

用户已确认推荐方案并授权后续自主完成；本计划没有新增用户选择或权限，Requirement Workflow检查点3通过。执行中只有发现必须改变评分、行动表、行业范围、真实输入范围或产生破坏性操作时才暂停。
