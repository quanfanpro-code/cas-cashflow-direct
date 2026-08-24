# cas-cashflow-direct完整路径与可选改判阈值实施计划

> **供执行者使用：** 未授权子Agent；按 `executing-plans.md` 在当前会话逐项串行执行。所有步骤使用复选框跟踪。

**目标：** 修复完整科目路径节点静默丢失导致的评分退化，并让客户每次运行从50、55、70、90中选择有效原项目的自动修改最低证据分，默认推荐70。

**架构：** 复用现有科目概念、路径规则、决定路由和运行状态，不引入模型或新依赖。路径层增加受控父属性继承、中性限定与明确未识别；决定层只增加一个统一阈值校验和比较入口，并贯穿首次分类、Agent结果导入、状态、清单和工作簿。

**技术栈：** Python标准库、现有 `openpyxl`、JSON规则词典、`unittest`/现有测试套件、Windows Excel重算。

## 全局约束

- 完整路径每个节点只能落入直接识别、继承父属性、中性限定或明确未识别，不得静默丢失。
- 系统性补齐税费、职工薪酬、费用成本、票据结算、投资属性，不写客户名称、金额或凭证号特例。
- 特定税种完整路径是45分强证据；真正无法解释的节点继续明确保留。
- 自动修改最低证据分只允许50、55、70、90，默认并推荐70；只控制有效原项目的修改，不改变评分、填空、排除、内部划转和其他门禁。
- 只做一次真实样本冒烟；不重复十万行性能测试；不调用任何大模型或模型服务。
- 修改现有文件前先备份到 `C:\Users\27651\BackUp\cas-cashflow-direct_时间戳\` 并核验SHA-256；中文文件保存为UTF-8 BOM。

---

### Task 1：建立备份与失败基线

**文件：**
- 备份：本计划后续列出的全部现有代码、测试、词典、设计、README、SKILL、CONTEXT和版本文件
- 测试：`tests/test_account_dictionary.py`
- 测试：`tests/test_decision_policy.py`
- 测试：`tests/test_structured_ai_resolution.py`
- 测试：`tests/test_pipeline.py`
- 测试：`tests/test_workbook_output.py`

**接口：**
- 消费：现有路径解释与决定路由接口
- 产出：能够准确暴露两个共同根因的失败测试

- [x] **Step 1：备份并校验全部待修改的现有文件**

建立单一时间戳目录，保留仓库内相对路径，并逐一比较源文件和备份的SHA-256；任何不一致立即停止。

- [x] **Step 2：写完整路径失败测试**

测试必须覆盖：

```python
assert car_tax.quality_score == 45
assert car_tax.candidate == "支付的各项税费"
assert every_node_has_one_role(car_tax)
assert every_node_has_one_role(staff_social_insurance)
assert every_node_has_one_role(expense_cost_detail)
assert every_node_has_one_role(trade_note_detail)
assert every_node_has_one_role(investment_object)
assert unknown_detail.unresolved_nodes
```

- [x] **Step 3：写可选阈值失败测试**

参数化覆盖50、55、70、90：

```python
route = route_normal_decision(
    score=score,
    original_state=OriginalState.VALID_CONFLICT,
    materiality=MaterialityBand.M0,
    automatic_change_threshold=threshold,
)
assert (route.action is DecisionAction.AUTOMATIC_CHANGE) == (score >= threshold)
```

并验证无效阈值拒绝、默认70、M1至M3门禁不被削弱、空白原项目填充不受影响、所有Agent改判出口遵循同一阈值、运行状态恢复和工作簿展示。

- [x] **Step 4：运行定向测试并确认按预期失败**

运行：

```text
python -m unittest tests.test_account_dictionary tests.test_decision_policy tests.test_structured_ai_resolution tests.test_pipeline tests.test_workbook_output
```

预期：仅新增断言失败，失败原因分别指向路径节点未解释/税种仅25分，以及阈值参数或动态路由尚不存在。

### Task 2：最小修复完整路径语义共同根因

**文件：**
- 修改：`references/科目语义词典.json`
- 修改：`src/cashflow_direct/account_dictionary.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 测试：`tests/test_account_dictionary.py`

**接口：**
- 消费：`load_account_semantic_rules(root)`、现有路径逐层拆分结果
- 产出：每节点角色、来源和未识别明细；受控父属性继承；特定税种45分

- [x] **Step 1：扩充通用词族和受控继承规则**

补充特定税种同义词、职工薪酬明细、费用成本层级、票据类型与结算属性、投资对象/账户限定。父属性继承只在已确认父概念和允许的子节点类别内发生；未知词不继承为确定业务。

- [x] **Step 2：让每个节点有且只有一个可审计去向**

输出结构至少能表达：

```python
{
    "node": raw_node,
    "role": "direct" | "inherited" | "neutral" | "unresolved",
    "concepts": tuple(concepts),
    "source": rule_id_or_none,
}
```

直接限制和冲突优先于继承；中性限定不独立加分；未识别保留原文和层级。

- [x] **Step 3：修正路径证据质量**

把具体法定税种加入决定性路径概念；只有完整路径能够唯一判断且内部无冲突时给45分。不得把“其他”“公司名”“账户名”等中性节点当强证据。

- [x] **Step 4：补强覆盖报告**

`科目语义词典说明.md` 的覆盖明细增加每节点角色/来源和未识别节点，使“解释完整”可从结果反查，不再只展示整条路径状态。

- [x] **Step 5：运行定向路径测试**

运行：

```text
python -m unittest tests.test_account_dictionary
```

预期：新增五类路径测试通过，真实未知节点仍落入未识别。

### Task 3：贯穿自动修改阈值

**文件：**
- 修改：`src/cashflow_direct/decision_policy.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`src/cashflow_direct/ai_review.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/cli.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/versions.py`
- 测试：`tests/test_decision_policy.py`
- 测试：`tests/test_classification_routing.py`
- 测试：`tests/test_structured_ai_resolution.py`
- 测试：`tests/test_pipeline.py`
- 测试：`tests/test_workbook_output.py`

**接口：**
- 产出：`validate_automatic_change_threshold(value: int) -> int`
- 产出：`score_meets_change_threshold(score: int, threshold: int) -> bool`
- 贯穿：`automatic_change_threshold: int = 70`

- [x] **Step 1：增加唯一阈值定义和校验**

```python
AUTOMATIC_CHANGE_SCORE_OPTIONS = (50, 55, 70, 90)
DEFAULT_AUTOMATIC_CHANGE_SCORE = 70

def validate_automatic_change_threshold(value: int) -> int:
    if value not in AUTOMATIC_CHANGE_SCORE_OPTIONS:
        raise ValueError("自动修改最低证据分只允许50、55、70、90")
    return value
```

- [x] **Step 2：修改首次分类路由**

有效原项目与候选冲突时，M0仅在 `score >= automatic_change_threshold` 自动修改；M1至M3继续执行原有复核深度和责任门禁。空白原项目的自动填充、明确排除和内部划转不读取该阈值。

- [x] **Step 3：修改全部Agent结果出口**

逐一替换 `ai_review.py` 中所有硬编码的70/90自动修改判断，统一调用 `score_meets_change_threshold`。来源冲突、双轮不一致、个人所得税、增值税、金额守恒等既有前置门禁保持不变。

- [x] **Step 4：把选择持久化到运行状态和清单**

`run_preflight(..., automatic_change_threshold=70)` 校验并写入状态和运行清单；恢复运行及导入Agent结果只读取该次状态，不回退到新的外部默认值。提升版本号，旧状态明确拒绝。

- [x] **Step 5：增加命令入口和工作簿展示**

命令参数：

```text
--automatic-change-threshold {50,55,70,90}
```

默认70。工作簿“使用说明与状态”显示“本次自动修改最低证据分”和客户选择值。

- [x] **Step 6：运行阈值相关定向测试**

运行：

```text
python -m unittest tests.test_decision_policy tests.test_classification_routing tests.test_structured_ai_resolution tests.test_pipeline tests.test_workbook_output
```

预期：四档阈值、默认值、状态恢复和所有改判出口测试通过。

### Task 4：同步权威设计、运行说明和变更记录

**文件：**
- 修改：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
- 修改：`SKILL.md`
- 修改：`README.md`
- 修改：`CONTEXT.md`
- 创建：`docs/adr/0012-自动修改最低证据分由客户逐运行选择.md`
- 保留：`docs/requirements/2026-08-24-cas-cashflow-direct完整科目路径语义完整性修复-requirement.md`
- 保留：`docs/superpowers/specs/2026-08-24-cas-cashflow-direct完整路径与可选改判阈值-design.md`

**接口：**
- 消费：Task 2与Task 3的最终行为
- 产出：权威规则、Agent问法、用户操作说明和Changelog一致

- [x] **Step 1：修订权威设计**

新增本轮正式规则并声明其覆盖旧验收中的冲突描述：四类节点去向、五类系统补强、税种45分、四档可选阈值及不受阈值影响的门禁。

- [x] **Step 2：修改Skill运行行为**

每次新运行必须先问：

```text
本次系统自动修改客户原项目至少需要多少分？可选50、55、70、90，推荐并默认70。
```

记录客户选择并传入运行；客户未另选时使用70。不得把该问题扩大为模型调用或额外配置。

- [x] **Step 3：同步README、CONTEXT和ADR**

README正文和Changelog说明业务效果、阈值边界及本轮验证；CONTEXT删除“只有70、90可改”的旧口径；ADR记录逐运行选择、状态持久化与不影响其他门禁的决定。

- [x] **Step 4：扫描中文编码和旧口径**

确认所有修改后的中文文件有UTF-8 BOM，无U+FFFD、连续问号或“仅70/90可修改”等残留冲突表述。

### Task 5：普通回归与一次真实样本验收

**文件：**
- 创建：`docs/reviews/2026-08-24-cas-cashflow-direct完整路径与可选改判阈值-review.md`
- 生成：真实样本运行目录及工作簿（不提交客户数据）

**接口：**
- 消费：全部生产实现和文档
- 产出：可复核的自动测试、真实业务和工作簿证据

- [x] **Step 1：运行全部非性能测试**

运行现有普通测试套件，明确排除 `tests/test_100k_performance.py`。预期全部通过。

- [x] **Step 2：校验Skill结构和代码差异**

运行Skill快速校验、Python语法检查、编码扫描和 `git diff --check`；预期均通过。

- [x] **Step 3：只运行一次默认70分真实样本冒烟**

使用原始真实输入完成全流程，不调用大模型；仅在既定流程需要时由本机Excel重算结果工作簿。不得因结果不合预期反复重跑，先从已生成证据定位问题。

- [x] **Step 4：核对真实业务红线**

直接读取差异明细、决定追踪、覆盖报告和正表核对报告，确认：

```text
车船税自动改判 = 1项
内部划转差异 = 1项
手续费退款自动改判 = 0项
原表与系统决定差异业务 = 2项
自动修改最低证据分 = 70
```

同时确认没有客户名称、金额、凭证号特例，且真正未知路径仍如实报告。

- [x] **Step 5：形成独立复核记录**

记录需求逐条覆盖、测试命令和结果、真实样本证据、未运行十万行测试、未调用模型服务、剩余限制和变更文件清单。

### Task 6：提交并推送

**文件：**
- 提交：仅本任务的代码、测试、词典、设计、需求、计划、ADR、复核、README、SKILL和CONTEXT
- 排除：真实客户输入/输出、忽略目录中的无关材料和任何用户既有改动

**接口：**
- 消费：Task 5的全部通过证据
- 产出：远端分支与本地提交一致

- [x] **Step 1：复核工作区和远端状态**

确认当前仓库、分支、远端、待提交文件和差异范围；对被忽略但属于本任务的需求、规格、计划和复核文档使用明确文件路径加入。

- [x] **Step 2：提交本轮变更**

提交信息使用中文业务含义，例如：

```text
fix: 补全科目路径语义并支持可选改判阈值
```

- [x] **Step 3：推送并核验**

推送当前分支到已配置远端，重新读取本地HEAD和远端分支，确认提交哈希一致且工作区无本任务遗漏。
