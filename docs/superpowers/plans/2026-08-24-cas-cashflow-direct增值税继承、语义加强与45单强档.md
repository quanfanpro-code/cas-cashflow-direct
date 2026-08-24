# cas-cashflow-direct增值税继承、语义加强与45单强档实施计划

> **执行者说明：** 用户未授权子Agent。执行前阅读`executing-plans.md`，由当前会话按任务串行实施；每项先写失败测试，再写最小实现。

**目标：** 修复跨业务组成的增值税跟随，补强通用摘要和完整路径语义，并新增不接受纯50分的45单强自动修改档。

**架构：** 保留现有业务组成和金额分配，增加仅适用于进项税、销项税的显式附属关系；统一行动中心以一个授权函数处理五档选择；语义继续使用现有固定词典和关系绑定。工作簿用公式让税额行跟随基础项目的唯一人工选择，不合并两行金额。

**技术栈：** Python 3、dataclass、pytest/unittest、JSON规则、xlsxwriter、openpyxl、Git。

## 全局约束

- 修改任何现有文件前，先备份到`C:\Users\27651\BackUp\cas-cashflow-direct_<时间戳>\`并核对SHA-256。
- 中文文件使用UTF-8；Markdown、JSON等新建或改写文件优先UTF-8 with BOM，写后扫描乱码和Unicode替换字符。
- 不启动LM Studio，不调用新的本地或远程模型服务，不新增依赖。
- 不写企业名、客户名、金额、凭证号、行号或整段真实摘要特例。
- 不恢复可靠同类组、累计金额人工确认或相关工作表。
- 所有分类出口复用同一评分和行动中心。
- 普通自动测试只用构造数据；最终只做一次真实样本冒烟，不重复十万行性能测试。

---

### 任务1：建立增值税跨组成附属关系

**文件：**

- 新建：`src/cashflow_direct/vat_companion.py`
- 修改：`src/cashflow_direct/models.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 测试：`tests/test_vat_companion.py`
- 测试：`tests/test_classification_routing.py`
- 测试：`tests/test_pipeline_decision_routing.py`

**接口：**

- `VatCompanionRelation(vat_component_id, base_component_id, shared_entry_ids, status, reason)`保存唯一、缺失或冲突关系。
- `build_vat_companion_relations(components, source_allocations)`只使用同凭证、共享现金来源、同方向和唯一基础组成建立关系。
- `apply_vat_companion_relations(decisions, relations)`在基础决定改变后统一刷新附属税额。
- `ClassificationDecision.vat_base_component_id`和`vat_relation_status`把关系传到运行状态与工作簿。

- [x] **步骤1：备份本任务将修改的现有文件并校验哈希**

备份`models.py`、`classification.py`、`pipeline.py`和现有测试文件；源文件与备份文件的SHA-256必须逐一相同。

- [x] **步骤2：写关系识别失败测试**

测试至少构造以下四组数据：

```python
def test_vat_and_base_split_by_labeled_flow_rows_share_one_cash_source():
    relations = build_vat_companion_relations(components, allocations)
    assert relations[0].status == "unique"
    assert relations[0].base_component_id == "CMP-BASE"

def test_same_voucher_without_shared_cash_source_does_not_follow():
    assert relation.status == "missing"

def test_two_possible_bases_are_reported_as_conflict():
    assert relation.status == "conflict"

def test_standalone_tax_payment_has_no_companion_relation():
    assert relations == ()
```

- [x] **步骤3：运行关系测试并确认红灯**

运行：

```powershell
python -m pytest -q tests/test_vat_companion.py
```

预期：因模块或关系函数尚不存在而失败。

- [x] **步骤4：实现最小关系对象和识别函数**

只为路径含“进项税”或“销项税”的业务组成寻找基础组成；现金来源使用`ComponentSourceAllocation.entry_id`交集，不用行顺序或金额最近值兜底。多个基础组成时返回冲突。

- [x] **步骤5：运行关系测试并确认转绿**

运行同一测试文件，预期全部通过。

- [x] **步骤6：写分类路由失败测试**

覆盖：

```python
def test_unique_vat_companion_follows_a_reliably_kept_base():
    assert vat.system_item_id == base.system_item_id
    assert vat.resolved is True
    assert vat.vat_base_missing is False

def test_unique_vat_companion_waits_for_base_without_own_ai_task():
    assert vat.decision_action == "vat_follow_base"
    assert vat.resolved is False
    assert vat.component_id not in {task.component_id for task in result.ai_tasks}

def test_ai_resolution_refreshes_dependent_vat():
    assert refreshed_vat.system_item_id == resolved_base.system_item_id
```

- [x] **步骤7：运行路由测试并确认红灯**

运行：

```powershell
python -m pytest -q tests/test_classification_routing.py tests/test_pipeline_decision_routing.py -k "vat or companion"
```

预期：现有程序仍把拆分税额标为基础交易缺失，或仍生成独立Agent任务。

- [x] **步骤8：把关系接入统一分类和Agent结果出口**

`route_classification_decisions`接收现有`source_allocations`。普通候选和路由完成后，统一应用附属关系并过滤附属税额的独立Agent任务。`pipeline.py`在初次分类、结构化Agent结果、后续复核和程序人工决定完成后调用同一个刷新函数。

- [x] **步骤9：运行任务1测试**

```powershell
python -m pytest -q tests/test_vat_companion.py tests/test_classification_routing.py tests/test_pipeline_decision_routing.py
```

预期：全部通过，且既有“单独缴税”和“基础交易缺失”边界没有回归。

---

### 任务2：让附属增值税复用基础项目的唯一人工选择

**文件：**

- 修改：`src/cashflow_direct/models.py`
- 修改：`src/cashflow_direct/materiality.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 测试：`tests/test_workbook_output.py`
- 测试：`tests/test_pipeline.py`

**接口：**

- `UnresolvedDecision.follows_component_id`和`ReviewBatch.follows_component_id`标识依赖行。
- 依赖行“人工确认项目”为受保护公式，引用基础行选择；不设置数据验证。
- 依赖行状态只显示“随基础项目待定”或“随基础项目完成”，不计作第二项“等待人工处理”。

- [x] **步骤1：备份本任务首次修改的现有文件并校验哈希**

备份`materiality.py`、`workbook_output.py`、`test_workbook_output.py`和`test_pipeline.py`。

- [x] **步骤2：写工作簿失败测试**

构造一项基础人工批次和一项附属税额批次，断言：

```python
assert base_choice.data_type != "f"
assert vat_choice.data_type == "f"
assert base_choice.coordinate in vat_choice.value
assert vat_choice.coordinate not in validated_cells
assert vat_status.value.startswith("=IF(")
assert "随基础项目待定" in vat_status.value
assert "随基础项目完成" in vat_status.value
```

再断言基础和税额原基线不同的场景下，两行技术调整公式各自引用自己的原基线，正表调整合计等于两行应转金额。

- [x] **步骤3：运行工作簿测试并确认红灯**

```powershell
python -m pytest -q tests/test_workbook_output.py -k "vat and follow"
```

预期：税额行仍是独立空白输入并带下拉菜单。

- [x] **步骤4：实施依赖批次和工作簿公式**

`pipeline.py`为唯一关系且基础待人工的税额创建依赖批次；`workbook_output.py`先建立“业务组成编号到工作表行号”的映射，再写基础选择引用公式。若依赖批次找不到基础批次，生成时立即报错。

- [x] **步骤5：验证不重复人工和金额桥**

```powershell
python -m pytest -q tests/test_workbook_output.py tests/test_pipeline.py -k "vat or review or manual"
```

预期：依赖税额只有公式没有下拉；首页待处理计数只计算基础行；正表公式仍仅引用重要待复核事项和疑似重复事项。

---

### 任务3：新增45单强授权档并封闭全部出口

**文件：**

- 修改：`src/cashflow_direct/decision_policy.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`src/cashflow_direct/ai_review.py`
- 修改：`src/cashflow_direct/cli.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/versions.py`
- 测试：`tests/test_decision_policy.py`
- 测试：`tests/test_structured_ai_resolution.py`
- 测试：`tests/test_pipeline.py`
- 测试：`tests/test_workbook_output.py`

**接口：**

- `change_is_authorized(score, threshold, summary_quality, account_path_quality) -> bool`是唯一授权函数。
- `AUTOMATIC_CHANGE_SCORE_OPTIONS == (45, 50, 55, 70, 90)`；默认值仍为70。

- [x] **步骤1：备份本任务首次修改的现有文件并校验哈希**

备份`decision_policy.py`、`ai_review.py`、`cli.py`、`versions.py`及对应测试文件。

- [x] **步骤2：写五档授权矩阵失败测试**

参数化断言：

```python
@pytest.mark.parametrize(
    "score,summary_quality,path_quality,expected",
    [(45,45,0,True),(55,45,10,True),(70,45,25,True),(90,45,45,True),
     (50,25,25,False),(45,25,10,False)],
)
def test_45_single_strong_authorization(score, summary_quality, path_quality, expected):
    assert change_is_authorized(score, 45, summary_quality, path_quality) is expected
```

并保留50、55、70、90原数值门槛矩阵，新增冲突、方向矛盾、强制检查和M3仍阻断45档的路由测试。

- [x] **步骤3：运行门槛测试并确认红灯**

```powershell
python -m pytest -q tests/test_decision_policy.py -k "threshold or single_strong"
```

预期：45仍被拒绝或纯50被错误授权。

- [x] **步骤4：实现唯一授权函数并接入首次分类**

把直接数字比较收口到`change_is_authorized`；`route_decision`接收摘要质量和路径质量。冲突和强制检查仍先于授权函数返回。

- [x] **步骤5：写Agent出口失败测试**

同一纯50分评估在45档下，分别通过一次Agent、双Agent一致、后续复核和第三轮出口，均断言不能自动修改；带一个45分强来源时才可修改。

- [x] **步骤6：运行Agent出口测试并确认红灯**

```powershell
python -m pytest -q tests/test_structured_ai_resolution.py -k "threshold or single_strong"
```

预期：至少一个出口仍用`score >= threshold`错误放行纯50。

- [x] **步骤7：替换全部Agent出口的直接比较**

所有Agent结果只把固定程序重算后的总分、摘要质量和路径质量交给统一授权函数，不复制45档条件。

- [x] **步骤8：更新运行入口、状态、工作簿和版本**

命令入口加入45；提示文案写成“45（单强）、50、55、70、90，推荐70”。提升评分、行动和运行结构版本，确保旧运行拒绝续跑。

- [x] **步骤9：运行任务3测试**

```powershell
python -m pytest -q tests/test_decision_policy.py tests/test_structured_ai_resolution.py tests/test_pipeline.py tests/test_workbook_output.py
```

预期：五档矩阵、全部Agent出口、运行持久化和工作簿说明一致通过。

---

### 任务4：补强通用摘要和完整路径语义

**文件：**

- 修改：`references/摘要语义规则.json`
- 修改：`references/科目语义词典.json`
- 修改：`src/cashflow_direct/summary_semantics.py`
- 修改：`src/cashflow_direct/account_dictionary.py`
- 修改：`src/cashflow_direct/versions.py`
- 测试：`tests/test_summary_semantics.py`
- 测试：`tests/test_account_dictionary.py`
- 测试：`tests/test_classification.py`
- 测试：`tests/test_classification_routing.py`

**接口：**

- 现有`analyze_summary`继续返回语素、候选、质量和状态。
- 现有`analyze_account_path`继续逐节点返回候选、质量和覆盖结果。
- 不新增分词器、模型客户端或第三个证据来源。

- [x] **步骤1：备份本任务将修改的规则、实现和测试文件并校验哈希**

JSON备份后还必须用UTF-8解析验证；修改后保持原结构和BOM策略。

- [x] **步骤2：写摘要表达族失败测试**

至少覆盖：

```python
assert analyze_summary("支付本公司员工本月工资", rules).candidate_item_ids == ("CFO-05",)
assert "CFO-05" not in analyze_summary("付承包商农民工资专户工程款", rules).candidate_item_ids
assert analyze_summary("支付生产线产品检测费", rules).candidate_item_ids == ("CFO-04",)
assert analyze_summary("支付管理咨询费", rules).candidate_item_ids == ("CFO-07",)
assert analyze_summary("支付自有设备安装改造款", rules).candidate_item_ids == ("CFI-06",)
assert analyze_summary("缴纳分红个税", rules).candidate_item_ids != ("CFO-05",)
assert analyze_summary("支付电费", rules).quality.value < 45
```

另加劳务派遣、物流运输、计量认证、搬迁维修、股权激励个税、代缴与返还，以及摘要和路径冲突的正反例。

- [x] **步骤3：运行语义测试并确认红灯**

```powershell
python -m pytest -q tests/test_summary_semantics.py tests/test_classification.py -k "service or installation or tax or utility or wage"
```

预期：尚未覆盖的通用关系无法形成正确候选或质量。

- [x] **步骤4：扩充固定词典和现有关系绑定**

只添加通用语素和必要的父子约束：业务对象必须与付款对象、用途、项目归属、税款服务对象或资产属性绑定。孤立“工资”“电费”“安装”“服务费”不得形成45分强候选。

- [x] **步骤5：写完整路径失败测试**

覆盖税费、职工薪酬、费用成本、票据结算和投资属性的明细节点，断言每个节点的状态均属于直接识别、父属性继承、中性限定或明确未识别；摘要和路径形成不同唯一候选时，最终来源冲突为真。

- [x] **步骤6：运行完整路径测试并确认红灯**

```powershell
python -m pytest -q tests/test_account_dictionary.py tests/test_classification_routing.py -k "detail or path or conflict"
```

- [x] **步骤7：补齐节点概念和组合规则**

优先补现有词典和组合关系，不写整段摘要、客户名称或金额特例。确实无法唯一解释的输入保持弱、多候选或无候选，并允许现有受限Agent任务机制接手语言槽位。

- [x] **步骤8：运行任务4测试**

```powershell
python -m pytest -q tests/test_summary_semantics.py tests/test_account_dictionary.py tests/test_classification.py tests/test_classification_routing.py
```

预期：新增表达族和所有既有语义边界通过，农民工资专户不再误判为本企业职工薪酬。

---

### 任务5：同步权威设计和项目文档

**文件：**

- 修改：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- 修改：`CONTEXT.md`
- 修改：`SKILL.md`
- 修改：`README.md`
- 修改：`docs/adr/0002-vat-accompanies-trade.md`
- 修改：`docs/adr/0012-自动修改最低证据分由客户逐运行选择.md`
- 新建：`docs/reviews/2026-08-24-cas-cashflow-direct增值税继承、语义加强与45单强档-review.md`

- [x] **步骤1：备份全部现有文档并校验哈希**

桌面权威设计也备份到同一时间戳目录，项目内不得建立备份文件夹。

- [x] **步骤2：按实现结果更新权威设计和ADR**

明确跨组成价税关系、不重复人工选择、45单强矩阵、语义表达族和Agent边界；不得把实现细节冒充业务规则。

- [x] **步骤3：更新SKILL和CONTEXT运行说明**

客户新运行时应被询问45（单强）、50、55、70、90，推荐70；说明增值税待人工只跟随基础选择。

- [x] **步骤4：更新README及Changelog**

Changelog单列本批三个同级修复、兼容影响、默认值不变和旧运行需重建。

- [x] **步骤5：创建独立复核记录**

逐项列出需求、测试证据、真实样本结果、未覆盖边界和是否通过；不得预先填写未执行结果。

- [x] **步骤6：检查编码、链接和术语一致性**

扫描U+FFFD、问号乱码、四档旧文案、旧增值税“仅当前组成”表述和断开的本地链接。

---

### 任务6：完整验证、一次真实冒烟、提交和推送

**文件：**

- 验证：全部修改文件
- 更新：`docs/reviews/2026-08-24-cas-cashflow-direct增值税继承、语义加强与45单强档-review.md`

- [x] **步骤1：运行本批定向测试**

```powershell
python -m pytest -q tests/test_vat_companion.py tests/test_decision_policy.py tests/test_summary_semantics.py tests/test_account_dictionary.py tests/test_classification_routing.py tests/test_structured_ai_resolution.py tests/test_workbook_output.py tests/test_pipeline.py tests/test_pipeline_decision_routing.py
```

- [x] **步骤2：运行普通全量回归和技能校验**

使用项目现有验证入口运行全量普通测试、技能结构校验、Python编译、`git diff --check`、编码和敏感信息扫描。不得运行十万行性能测试。

- [x] **步骤3：只执行一次真实样本冒烟**

使用现有真实测试输入新建隔离运行，核对：

- 跨组成增值税不再错误显示基础交易缺失；
- 基础待人工时税额没有第二个人工输入；
- 45档矩阵留痕与本次实际选择一致；
- 自动改判、内部划转和手续费退款符合已确认红线；
- 正表金额桥、差异表、来源分配和输入哈希守恒；
- 真实摘要语义没有退化为空占位结果。

- [x] **步骤4：完成复核记录**

写入实际命令、通过数量、真实输出路径、工作簿状态、输入哈希和仍需人工事项；不把待人工状态写成最终可使用。

- [x] **步骤5：最终自审**

逐项核对需求文档三类需求、设计红线、计划所有复选框、README Changelog和桌面权威设计，确认无遗漏后再提交。

- [x] **步骤6：提交并推送**

强制加入被`.gitignore`忽略但属于本批交付的需求、设计、计划和复核文档；只提交本批文件。提交信息：

```text
fix: 完善增值税继承与单强档语义
```

推送到当前`origin/main`，随后核对本地HEAD与远端main一致。

## 依赖顺序和主要风险

1. 任务1先形成关系和状态，任务2才能安全生成工作簿联动。
2. 任务3必须先封闭统一授权函数，再改命令和文案，避免存在旁路。
3. 任务4独立于工作簿，但必须在真实冒烟前完成，否则真实分类结果不可解释。
4. 最大风险是税额依赖在Agent或人工出口失效；用同一刷新函数和多出口测试控制。
5. 第二风险是45档被当普通数值门槛；用纯50反例覆盖所有出口。
6. 第三风险是语义规则过拟合；所有规则必须有相近反例，真实工作簿不作为逐行金标准。

## 计划检查点3

用户已明确确认三个需求、45单强条件、价税人工联动和最终交付，并要求“不重复确认”。因此按上述顺序直接进入实施，不再为同一事项重复请求批准。
