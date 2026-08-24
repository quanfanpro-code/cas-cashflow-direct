# 全量分类留痕人工改选实施计划

> **For agentic workers:** 用户未授权子Agent；读取`executing-plans.md`并逐项串行实施。步骤使用复选框跟踪。

**目标：** 让“全量分类留痕”的“最终决定项目”默认保留当前内容并支持逐行下拉改选，改选通过现有人工调整金额桥即时更新正表和正表核对报告。

**架构：** 在`workbook_output.py`中复用现有工作簿生成入口。全量留痕增加隐藏的基准项目、基准金额、目标金额和生效标志；正表人工调整公式追加两段`SUMIFS`，计算既有决定到最后改选的增量。下拉值和项目方向系数放在隐藏辅助区域，不新增工作表和依赖。

**技术栈：** Python 3、XlsxWriter、openpyxl、pytest、Microsoft Excel自动计算。

## 全局约束

- Windows平台，中文文件使用UTF-8；Markdown使用UTF-8 with BOM。
- 修改已有文件前备份到`C:\Users\27651\BackUp\cas-cashflow-direct_<时间戳>\`并校验成功。
- 不改变摘要语义、科目路径语义、评分、动作路由和自动基线。
- 下拉选项恰为22个正表叶子项目加“明确排除”。
- 不新增工作表、不新增依赖、不重复十万行性能测试。
- 不写客户名称、凭证号、金额或真实样本特例。
- 串行修改文件，不启动子Agent。

---

### Task 1：用失败测试锁定下拉与金额桥

**文件：**
- 修改：`tests/test_workbook_output.py`
- 测试：`tests/test_workbook_output.py`

**接口：**
- 消费：`WorkbookModel`、`build_output_workbook()`、匿名`workbook_model()`。
- 产出：两个工作簿行为测试，分别锁定全量留痕下拉/技术列和正表公式联动。

- [ ] **Step 1：新增下拉与技术列测试**

新增`test_trace_final_decision_is_editable_for_every_row_with_all_leaf_items()`：

```python
def test_trace_final_decision_is_editable_for_every_row_with_all_leaf_items(self) -> None:
    base = workbook_model(1, 0)
    review = replace(base.review_batches[0], baseline_item_code="CFO-07")
    model = replace(
        base,
        review_batches=(review,),
        trace_rows=(
            {
                "业务组成编号(技术)": "DONE-1",
                "本行分配现金变化": 100.0,
                "最终决定项目": "销售商品、提供劳务收到的现金",
            },
            {
                "业务组成编号(技术)": "RC-1",
                "本行分配现金变化": -100.0,
                "最终决定项目": "等待人工复核",
            },
        ),
    )
```

断言：两个“最终决定项目”单元格均被同一列表验证覆盖；隐藏列表恰有22个叶子项目和“明确排除”；两个单元格可编辑；四个技术列存在且隐藏；普通行基准项目等于当前决定；等待人工行基准公式引用“重要待复核事项”并在空选择时回退`CFO-07`名称。

- [ ] **Step 2：新增正表公式联动测试**

新增`test_trace_override_is_added_to_main_manual_adjustment_formula()`，断言叶子项目“人工调整”公式包含：

```text
'全量分类留痕'
-SUMIFS(
+SUMIFS(
人工改选基准项目(技术)对应列
人工改选目标金额(技术)对应列
人工改选生效标志(技术)对应列
```

同时断言“正表核对报告”的人工调整和最终金额仍分别引用正表E列和F列。

- [ ] **Step 3：运行测试并确认正确失败**

运行：

```powershell
python -X utf8 -m pytest -q tests/test_workbook_output.py -k "trace_final_decision_is_editable or trace_override_is_added"
```

预期：新测试失败，原因是全量留痕尚无全行下拉、技术列和正表增量公式；不得出现测试自身构造错误。

---

### Task 2：最小实现全量留痕下拉和公式增量

**文件：**
- 修改：`src/cashflow_direct/workbook_output.py`
- 测试：`tests/test_workbook_output.py`

**接口：**
- 消费：动态`trace_headers`、22个叶子项目、`ReviewBatch.baseline_item_code`、`本行分配现金变化`、现有`manual_adjustment_formula()`。
- 产出：`trace_manual_adjustment_terms(item_name, trace_last_row, trace_headers) -> str`和工作簿内四个隐藏技术列。

- [ ] **Step 1：定义四个技术列名称**

在`USE_SYSTEM_RECOMMENDATION`附近增加：

```python
TRACE_MANUAL_HEADERS = (
    "人工改选基准项目(技术)",
    "人工改选基准金额(技术)",
    "人工改选目标金额(技术)",
    "人工改选生效标志(技术)",
)
```

- [ ] **Step 2：集中准备全量留痕行和列**

在`build_output_workbook()`取得项目名称映射后，清除旧“人工决定”字段，并为每行追加四个空技术字段；提前形成`trace_headers`和`trace_last_row`，供正表公式和后续写表共同使用。

- [ ] **Step 3：追加正表人工调整公式项**

增加`trace_manual_adjustment_terms()`，根据动态列号生成：

```text
-SUMIFS(基准金额, 基准项目, 当前正表项目, 生效标志, 1)
+SUMIFS(目标金额, 最终决定项目, 当前正表项目, 生效标志, 1)
```

在叶子项目原有`manual_adjustment_formula()`结果后追加该字符串。无全量留痕行时返回空字符串，保持旧公式。

- [ ] **Step 4：写入下拉值和方向系数**

在全量留痕表格右侧隐藏区域横向写入23个名称，下一行写入方向系数：流入项目为`1`、流出项目为`-1`、“明确排除”为`0`。数据验证使用名称行，金额公式使用两行区域；这样不增加业务数据行数。错误类型为`stop`。

- [ ] **Step 5：把最终决定项目改为全行可编辑**

所有数据行重写为`formats["input"]`。普通行写回当前文本；等待人工行保留现有引用“重要待复核事项”的公式。对整列数据区一次性添加列表验证。

- [ ] **Step 6：写入四个隐藏辅助公式**

每行写入：

```text
基准项目：普通行=工作簿当前决定；等待人工行=重要待复核事项当前选择，未选择时回退自动基线项目
基准金额：IFERROR(本行分配现金变化*HLOOKUP(基准项目,隐藏名称与方向表,2,FALSE),0)
目标金额：IFERROR(本行分配现金变化*HLOOKUP(最终决定项目,隐藏名称与方向表,2,FALSE),0)
生效标志：最终决定在合法下拉中且不等于基准项目时为1，否则为0
```

四列和隐藏名称/方向区域全部隐藏。

- [ ] **Step 7：运行定向测试**

运行：

```powershell
python -X utf8 -m pytest -q tests/test_workbook_output.py -k "trace_final_decision_is_editable or trace_override_is_added or pending_trace_result_follows"
```

预期：全部通过。

- [ ] **Step 8：运行受影响工作簿测试**

运行：

```powershell
python -X utf8 -m pytest -q tests/test_workbook_output.py tests/test_workbook_decision_trace.py tests/test_statement.py tests/test_pipeline.py
```

预期：全部通过，不运行十万行性能测试。

---

### Task 3：同步业务说明和变更记录

**文件：**
- 修改：`CONTEXT.md`
- 修改：`SKILL.md`
- 修改：`README.md`

**接口：**
- 消费：已实现的下拉和公式行为。
- 产出：统一的用户操作口径与Changelog记录。

- [ ] **Step 1：更新领域口径**

在`CONTEXT.md`增加“全量留痕人工改选”和“人工改选基准”定义，并在已确认规则中写明：自动基线不变，全量留痕最后选择只追加人工调整。

- [ ] **Step 2：更新Skill操作说明**

把`SKILL.md`中“全量留痕不设置人工输入”的旧口径改为：最终决定项目默认显示当前内容，每行可从22个项目和“明确排除”中选择，改选通过公式更新人工调整和最终金额。

- [ ] **Step 3：更新README和Changelog**

在README的全量分类留痕、正表联动和限制说明中增加本功能；在Changelog最上方增加2026-08-25条目，说明默认值、下拉范围、人工调整公式以及不改变自动基线。

- [ ] **Step 4：检查中文编码和口径冲突**

运行：

```powershell
python -X utf8 scripts/validate_skill.py
rg -n "不设置.*人工|全量分类留痕.*只读|最终决定项目" SKILL.md README.md CONTEXT.md
```

预期：`SKILL_VALID=True`；不再保留与新需求冲突的旧口径；中文无乱码。

---

### Task 4：验收、审查与交付

**文件：**
- 创建：`docs/reviews/2026-08-25-cas-cashflow-direct全量分类留痕人工改选-review.md`
- 检查：本计划涉及的全部文件

**接口：**
- 消费：Task 1至3全部产物。
- 产出：审查记录、新鲜验证证据和最终提交。

- [ ] **Step 1：运行普通回归但排除十万行性能测试**

运行：

```powershell
python -X utf8 -m pytest -q --ignore=tests/test_100k_performance.py
```

预期：全部普通测试通过；十万行测试未运行。

- [ ] **Step 2：运行静态与结构检查**

运行：

```powershell
python -X utf8 -m compileall -q src tests
python -X utf8 scripts/validate_skill.py
git diff --check
```

预期：全部退出码为0，Skill验证通过。

- [ ] **Step 3：做一次匿名Excel重算冒烟**

生成匿名工作簿，实际修改“最终决定项目”并用Microsoft Excel重算保存，核对A改B、A改明确排除及等待人工后再次覆盖三条金额桥。记录正表人工调整和最终金额的实际值。

- [ ] **Step 4：完成代码与Ponytail审查**

逐项对照需求、设计和计划，确认没有第二套业务规则、新依赖、新工作表、客户特例或重复金额；发现问题则回到失败测试后修复。

- [ ] **Step 5：写验收审查文档**

记录审查范围、发现、修复、测试数量、匿名冒烟结果、未运行十万行性能测试和残余限制。

- [ ] **Step 6：提交并推送**

在全部验证新鲜通过后执行：

```powershell
git add CONTEXT.md SKILL.md README.md src/cashflow_direct/workbook_output.py tests/test_workbook_output.py docs/requirements/2026-08-25-cas-cashflow-direct全量分类留痕人工改选-requirement.md docs/superpowers/specs/2026-08-25-cas-cashflow-direct全量分类留痕人工改选-design.md docs/superpowers/plans/2026-08-25-cas-cashflow-direct全量分类留痕人工改选.md docs/reviews/2026-08-25-cas-cashflow-direct全量分类留痕人工改选-review.md
git commit -m "feat: 支持全量留痕逐行人工改选"
git push origin main
```

预期：本地`main`与`origin/main`一致，工作区无未提交修改。

## 计划自检

- 需求中的默认值、23项下拉、明确排除、金额方向、等待人工叠加、正表联动、隐藏技术列和不重复性能测试均有对应任务。
- 无TBD、TODO、“稍后实现”或未定义接口。
- 文件、函数和测试名称在各任务间一致。
