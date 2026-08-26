# cas-cashflow-direct 东方农博运行问题修复实施计划

状态：实施与真实工作簿验收已完成。最终普通回归820项通过、2项跳过、120项参数化检查通过；提交和推送按用户授权执行。

> **For agentic workers:** 用户未授权子Agent；按`executing-plans.md`在当前会话串行执行，每个任务完成失败测试、最小实现和验证后再进入下一任务。步骤使用复选框跟踪。

**目标：** 修复五个共同根因和两项追加输出要求，使人工表可直接处理、低金额空白项目能够按实际执行重要性兜底、未决现金完整参与桥接、直接存货入口正确形成路径证据、来源顺序固定，并阻止无依据排除及直接修改运行状态。

**架构：** 保留现有业务组成、两项来源评分、统一行动表和正表金额桥。新增一个低金额兜底出口、一个受控排除校验、一个未决现金桥接层，并把人工表的判断主行与现金分配明细分开；Excel只展示和汇总程序决定，不建立第二套评分或分类。

**技术栈：** Python、dataclass、SQLite、XlsxWriter、openpyxl、pytest、Microsoft Excel真实重算。

## 全局约束

- 所有重要性判断读取`MaterialityAmounts`中的本次运行输入，不在规则或代码中写死25万元、12.5万元或1.25万元。
- 原始Excel只读，真实验收前后核对SHA-256；输出使用新隔离运行目录。
- 富掌柜和P云停车只属于东方农博本次现金范围决定，不写入通用规则。
- 不新增外部依赖，不新增第二套评分或行动中心，不为客户名称、金额、凭证号、行号编写特例。
- 所有现有文件修改前先备份到`C:\Users\27651\Desktop\BackUp\cas-cashflow-direct_<时间戳>\`，保留相对路径和原文件名。
- 禁止使用`rm -rf`，不删除用户已有文件；不覆盖桌面原始工作簿。
- 用户未授权子Agent，全部任务串行完成。
- 当前工作区已有用户修改的`SKILL.md`和未跟踪PPTX；修改前先核对差异，只暂存本任务明确修改，不把PPTX加入提交。
- 所有代码注释和文档使用中文；面向用户的工作簿不显示内部金额档代号。

---

### 任务1：修复直接存货入口和差异表来源顺序

**文件：**

- 修改：`references/科目语义词典.json`
- 修改：`src/cashflow_direct/account_dictionary.py`
- 修改：`src/cashflow_direct/differences.py`
- 修改：`tests/test_account_dictionary.py`
- 修改：`tests/test_differences.py`

**接口：**

- 输入：现有完整路径节点概念、摘要和现金方向。
- 输出：直接存货入口路径候选，以及固定顺序的路径来源1和摘要来源2。

- [ ] **步骤1：备份任务涉及的全部现有文件。**

  备份后列出文件清单，并确认备份目录不在项目仓库内。

- [ ] **步骤2：写直接存货入口失败测试。**

  在`tests/test_account_dictionary.py`增加参数化测试，至少覆盖：

  ```python
  @pytest.mark.parametrize(
      "path",
      (
          "原材料_甲材料",
          "周转材料_低值易耗品",
          "周转材料_包装物",
          "委托加工物资_甲项目",
      ),
  )
  def test_direct_inventory_entry_does_not_require_a_second_purchase_marker(path):
      result = score_path(path, cash_direction="outflow")
      assert result.candidate_item_ids == ("CFO-04",)
      assert result.quality.value > 0
  ```

  同时增加领用、入库、出库、完工、结转、制造费用分配仍被阻断，以及成本去向缺少外购对象仍不形成候选的反向测试。

- [ ] **步骤3：写固定来源顺序失败测试。**

  在`tests/test_differences.py`覆盖路径有效/摘要无效、路径无效/摘要有效、两项有效、两项冲突四类：

  ```python
  assert row["独立来源1"].startswith("完整对方科目路径")
  assert row["独立来源2"].startswith("摘要")
  ```

  无效来源也必须在固定栏显示“无效证据0分”和原因。

- [ ] **步骤4：运行定点测试并确认测试先失败。**

  运行：

  ```bash
  python -m pytest tests/test_account_dictionary.py tests/test_differences.py -q
  ```

  预期：直接存货入口因`require_any`失败；差异表来源顺序因摘要优先失败。

- [ ] **步骤5：做最小实现。**

  将现有规则拆成两个明确入口：

  ```json
  {
    "rule_id": "PATH-DIRECT-INVENTORY-ENTRY",
    "level1_any": ["inventory_acquisition_parent"],
    "forbid": ["non_cash", "staff_cost", "long_asset_detail"],
    "min_levels": 2,
    "outflow_candidate_item_ids": ["CFO-04"]
  }
  ```

  成本去向规则继续保留`require_any`。在路径评分结果中保留“已识别但被阻断”的状态和阻断原因；`differences._independent_sources()`固定返回：

  ```python
  return account_path_text, summary_text
  ```

- [ ] **步骤6：运行任务测试。**

  运行同一步骤4；预期全部通过。

---

### 任务2：实现空白原项目低于实际执行重要性的强制兜底

**文件：**

- 新建：`src/cashflow_direct/blank_original_fallback.py`
- 修改：`src/cashflow_direct/models.py`
- 修改：`src/cashflow_direct/ai_review.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 新建：`tests/test_blank_original_fallback.py`
- 修改：`tests/test_structured_ai_resolution.py`
- 修改：`tests/test_pipeline_decision_routing.py`

**接口：**

- 新增：

  ```python
  def apply_blank_original_fallback(
      component: CashflowComponent,
      decision: ClassificationDecision,
      materiality: MaterialityAmounts,
      ordered_leaf_item_ids: tuple[str, ...],
      item_directions: Mapping[str, str],
  ) -> ClassificationDecision:
      ...
  ```

- 决策记录新增：`summary_preferred_item_id`、`account_path_preferred_item_id`、`fallback_source`、`fallback_step`。

- [ ] **步骤1：备份现有模型、AI、管线和测试文件。**

- [ ] **步骤2：写六类兜底失败测试。**

  `tests/test_blank_original_fallback.py`分别验证：路径分高、摘要分高、同分同项目、同分不同项目、多候选和完全无候选。核心断言：

  ```python
  assert result.resolved is True
  assert result.decision_action == "automatic_fill"
  assert result.fallback_source in {"account_path", "summary"}
  assert result.evidence_score == original.evidence_score
  ```

  同分不同项目必须选择路径；多候选依次使用来源首选、现有系统首选、22个叶子项目固定展示顺序；完全无候选时，流入选择`CFO-03`，流出选择`CFO-07`，并记录`direction_other_operating`。

- [ ] **步骤3：写重要性边界失败测试。**

  使用两个不同的`performance_cent`证明不存在固定金额：

  ```python
  assert apply(amount=99_999, performance=100_000).resolved is True
  assert apply(amount=100_000, performance=100_000).resolved is False
  assert apply(amount=199_999, performance=200_000).resolved is True
  ```

  原项目有效、输入非法、现金方向未知或金额达到实际执行重要性时均不得调用本兜底。

- [ ] **步骤4：运行定点测试并确认失败。**

  ```bash
  python -m pytest tests/test_blank_original_fallback.py tests/test_structured_ai_resolution.py tests/test_pipeline_decision_routing.py -q
  ```

  预期：新函数和新留痕字段尚不存在。

- [ ] **步骤5：做最小实现。**

  `ai_review._apply_ai_outcome()`保存两项来源各自的候选和首选；所有AI成功、AI无有效结果和固定规则分类的共同出口调用`apply_blank_original_fallback()`。选择逻辑只使用：

  ```python
  winning_source = (
      "account_path"
      if decision.account_path_quality >= decision.summary_quality
      else "summary"
  )
  ```

  过滤现金方向后按已批准定序选一个项目，不改变原来源质量和总分。

- [ ] **步骤6：运行任务测试。**

  运行步骤4命令；预期全部通过，并额外断言低于实际执行重要性的适用事项不再进入人工队列。

---

### 任务3：增加受控排除授权和运行状态一致性门禁

**文件：**

- 新建：`src/cashflow_direct/exclusion_policy.py`
- 新建：`src/cashflow_direct/state_integrity.py`
- 修改：`src/cashflow_direct/models.py`
- 修改：`src/cashflow_direct/classification.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/validation.py`
- 修改：`src/cashflow_direct/storage.py`
- 新建：`tests/test_exclusion_policy.py`
- 新建：`tests/test_state_integrity.py`
- 修改：`tests/test_final_readiness.py`
- 修改：`tests/test_pipeline_decision_routing.py`

**接口：**

- 新增排除类型：`internal_transfer`、`non_cash`、`zero_amount`、`cash_scope_excluded`、`confirmed_duplicate`、`confirmed_adjustment`。
- 新增：

  ```python
  def authorize_exclusion(
      component: CashflowComponent,
      decision: ClassificationDecision,
      exclusion_type: str,
      state: Mapping[str, object],
  ) -> ExclusionAuthorization:
      ...
  ```

- 新增：

  ```python
  def assert_decision_store_consistent(run_dir: Path, state: Mapping[str, object]) -> None:
      ...
  ```

- [ ] **步骤1：备份全部现有文件。**

- [ ] **步骤2：把本轮最小复现变成失败测试。**

  构造真实银行现金腿、摘要为空的业务：

  ```python
  with pytest.raises(ValueError, match="无法分类不是排除依据"):
      confirm_manual_decisions(
          run_dir,
          [{"component_id": component_id, "exclude": True, "basis": "无效输入"}],
      )
  ```

  同样覆盖低金额、证据不足、候选不唯一和来源冲突。

- [ ] **步骤3：写允许情形和调整桥测试。**

  内部划转、非现金、零金额、现金范围排除和已确认重复继续由各自入口通过；`confirmed_adjustment`必须保存结构化类型和金额，不能只设`excluded=True`。

- [ ] **步骤4：写直接修改状态失败测试。**

  正常分类后直接修改`运行状态.json`中的`excluded`、`system_item_id`或`stage`，再调用`finalize_run()`：

  ```python
  with pytest.raises(RuntimeError, match="运行状态与计算留痕不一致"):
      finalize_run(run_dir)
  ```

- [ ] **步骤5：运行定点测试并确认失败。**

  ```bash
  python -m pytest tests/test_exclusion_policy.py tests/test_state_integrity.py tests/test_final_readiness.py tests/test_pipeline_decision_routing.py -q
  ```

- [ ] **步骤6：做最小实现。**

  `confirm_manual_decisions()`的普通入口默认只能接收`item_id`。收到`exclude`时必须有结构化`exclusion_type`，并由`authorize_exclusion()`核对现有事实；自由文字只作为补充说明。分类决定增加`exclusion_type`和`confirmed_adjustment_cent`。

  `assert_decision_store_consistent()`从SQLite读取`classification_decision`和`human_decision`，与状态文件中的同编号记录进行规范化JSON比较；分类、人工决定和最终生成入口均在修改状态前执行。阶段状态同时由待处理决定、AI队列和人工决定记录重新推导，不直接信任JSON里的`stage`。

- [ ] **步骤7：运行任务测试。**

  运行步骤5命令；预期全部通过，且现有规则排除回归不受影响。

---

### 任务4：拆分重要事项和低金额批次，并实现真实批量

**文件：**

- 修改：`src/cashflow_direct/models.py`
- 修改：`src/cashflow_direct/materiality.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 新建：`tests/test_review_queue_partition.py`
- 修改：`tests/test_ai_and_materiality.py`
- 修改：`tests/test_consistency_policy.py`

**接口：**

- `UnresolvedDecision`新增：`decision_action`、`system_candidate_signature`、`account_path_signature`、`summary_business_signature`、`evidence_status`、`forced_check_reason`。
- 新增：

  ```python
  def partition_review_batches(
      unresolved: Sequence[UnresolvedDecision],
      performance_cent: int,
      all_leaf_item_ids: Sequence[str],
  ) -> tuple[tuple[ReviewBatch, ...], tuple[ReviewBatch, ...]]:
      ...
  ```

- [ ] **步骤1：备份现有文件。**

- [ ] **步骤2：写分流失败测试。**

  验证达到实际执行重要性的空白原项目未决事项进入重要事项；`low_amount_human_batch`只进入低金额队列；任务2已经兜底完成的事项不进入任何队列。

- [ ] **步骤3：写七项批次键失败测试。**

  两项业务只有行动、现金方向、系统候选、完整路径、摘要业务性质、证据状态和强制检查原因全部相同才合并。参数化改变任一字段，断言批次数从1变为2。

- [ ] **步骤4：运行测试并确认失败。**

  ```bash
  python -m pytest tests/test_review_queue_partition.py tests/test_ai_and_materiality.py tests/test_consistency_policy.py -q
  ```

- [ ] **步骤5：做最小实现。**

  重要事项沿用逐判断单位独立批次；低金额批次使用七项稳定键分组，`component_ids`按来源顺序保存，`cash_delta_cent`等于全部组成现金变化合计。管线分别保存`important_review_batches`和`low_amount_review_batches`，旧`review_batches`仅作为重要事项兼容读取。

- [ ] **步骤6：运行任务测试。**

  运行步骤4命令；预期全部通过。

---

### 任务5：重写人工表输出粒度并新增“低金额批量处理”

**文件：**

- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/trace_output.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 新建：`tests/test_review_sheet_details.py`
- 修改：`tests/test_workbook_output.py`
- 修改：`tests/test_workbook_decision_trace.py`
- 修改：`tests/test_pipeline.py`
- 修改：`tests/test_tianwei_acceptance.py`

**接口：**

- `WorkbookModel`新增`low_amount_review_batches`。
- 人工表共用：

  ```python
  def write_review_queue_sheet(
      worksheet,
      batches: Sequence[ReviewBatch],
      trace_rows: Sequence[Mapping[str, object]],
      *,
      batch_mode: bool,
  ) -> ReviewSheetLayout:
      ...
  ```

- [ ] **步骤1：备份现有输出和测试文件。**

- [ ] **步骤2：写十三张表和分表失败测试。**

  `SHEET_NAMES`必须包含独立的“低金额批量处理”；低金额事项不再出现在“重要待复核事项”。

- [ ] **步骤3：写一行一个金额失败测试。**

  构造一个业务组成对应三条现金分配明细，分别验证借方、贷方、流量金额、本行分配现金变化和单笔金额：

  ```python
  assert detail_amounts == [-60.0, -25.0, -15.0]
  assert all(isinstance(value, (int, float)) for value in detail_amounts)
  assert all("、" not in str(value) for value in numeric_cells)
  ```

  主行合计为-100.0，三条明细合计同样为-100.0。

- [ ] **步骤4：写批次一次选择和防重复测试。**

  主行有唯一蓝色下拉；所有明细最终项目公式引用主行；正表公式只汇总标记为明细的行。改变一次主行选择后，三条明细全部生效，正表只变化100.0而不是200.0。

- [ ] **步骤5：运行定点测试并确认失败。**

  ```bash
  python -m pytest tests/test_review_sheet_details.py tests/test_workbook_output.py tests/test_workbook_decision_trace.py tests/test_pipeline.py -q
  ```

- [ ] **步骤6：做最小实现。**

  `REVIEW_HEADERS`增加“行类型”和“所属批次”等必要控制列。每个批次先写主行，再按`trace_rows`逐条写现金分配明细；主行数字只用于核对，明细行设置`计入正表(技术)=1`，主行设置0。删除金额字段调用`review_fact()`后拼接的路径，文字字段也优先逐明细展示。

  两张人工表只在主行提供下拉，并移除普通事项的“明确排除”选项；需要用户确认调整时使用任务3的结构化专门入口，不在普通项目选择中混入排除。

- [ ] **步骤7：更新公式、首页状态和结构验收。**

  正表人工调整同时引用两张人工表的明细行；全量留痕按业务组成编号取得所属主行选择；使用说明、筛选、打印区域、折叠、隐藏技术列和下拉验证同步适配十三张表。

- [ ] **步骤8：运行任务测试。**

  运行步骤5命令，再运行：

  ```bash
  python -m pytest tests/test_tianwei_acceptance.py -q
  ```

  预期工作簿结构、数值类型、公式引用和人工选择全部通过。

---

### 任务6：让未决金额参与现金变动桥接并区分两种成功状态

**文件：**

- 修改：`src/cashflow_direct/statement.py`
- 修改：`src/cashflow_direct/pipeline.py`
- 修改：`src/cashflow_direct/workbook_output.py`
- 修改：`src/cashflow_direct/validation.py`
- 新建：`tests/test_pending_cash_reconciliation.py`
- 修改：`tests/test_workbook_output.py`
- 修改：`tests/test_final_readiness.py`

**接口：**

- `ReconciliationResult`新增：`classified_net_cent`、`pending_net_cent`、`confirmed_adjustment_cent`、`bridge_difference_cent`、`final_difference_cent`、`pending_component_ids`。
- `reconcile_cash()`调整为：

  ```python
  def reconcile_cash(
      statement: StatementResult,
      opening_cent: int | None,
      closing_cent: int | None,
      fx_cent: int | None,
      *,
      components: Sequence[CashflowComponent] = (),
      decisions: Sequence[ClassificationDecision] = (),
      confirmed_adjustment_cent: int = 0,
  ) -> ReconciliationResult:
      ...
  ```

- [ ] **步骤1：备份现有文件。**

- [ ] **步骤2：写三类输入失败测试。**

  分别覆盖原项目完整、全部为空和部分为空。核心守恒式：

  ```python
  assert (
      result.classified_net_cent
      + result.pending_net_cent
      + result.fx_cent
      + result.confirmed_adjustment_cent
      + result.bridge_difference_cent
      == result.closing_cent - result.opening_cent
  )
  ```

- [ ] **步骤3：写状态失败测试。**

  桥接差异为0但`pending_net_cent != 0`时必须显示“现金变动桥接相符、现金流量表尚待分类”；只有未决为0、必做人工决定完成且最终差异为0时显示“最终现金流量表勾稽成功”。

- [ ] **步骤4：写原项目基线测试。**

  未决事项有有效原项目时，正表继续保留原项目金额并计入已分类净额；原项目为空的未决事项进入尚待分类净额。人工改选只在项目之间搬移，不改变桥接总额。

- [ ] **步骤5：运行定点测试并确认失败。**

  ```bash
  python -m pytest tests/test_pending_cash_reconciliation.py tests/test_workbook_output.py tests/test_final_readiness.py -q
  ```

- [ ] **步骤6：做最小实现。**

  `aggregate_statement()`对未决但原项目有效的决定使用`original_standard_item_id`作为基线；原项目无效的未决现金按`cash_delta_cent`进入`pending_net_cent`。确认调整按同一现金正负号进入桥接。工作簿勾稽页分别列示现金余额变动、已分类、尚待分类、汇率影响、已确认调整、桥接差异和最终差异。

- [ ] **步骤7：运行任务测试。**

  运行步骤5命令；预期全部通过。

---

### 任务7：同步领域上下文、README、Skill和决策记录

**文件：**

- 修改：`README.md`
- 修改：`SKILL.md`
- 修改：`CONTEXT.md`
- 新建：`docs/adr/0013-未决现金进入桥接且最终成功要求未决归零.md`
- 新建：`docs/adr/0014-低于实际执行重要性的空白原项目采用公开兜底定序.md`
- 新建：`docs/adr/0015-真实现金排除采用受控授权并校验运行状态.md`
- 修改：`docs/requirements/2026-08-26-cas-cashflow-direct本次问题修复需求-草案.md`
- 修改：`docs/superpowers/specs/2026-08-26-cas-cashflow-direct东方农博运行问题修复-design.md`

**接口：** 文档必须与代码和工作簿使用完全相同的术语、Sheet数量、来源顺序和成功条件。

- [ ] **步骤1：检查`SKILL.md`现有未提交差异，区分用户已有内容和本任务内容。**

  先运行`git diff -- SKILL.md`并备份当前文件；只在当前内容上增量修改，不覆盖用户已有改动。

- [ ] **步骤2：备份全部现有文档。**

- [ ] **步骤3：更新领域术语和操作说明。**

  明确“尚待分类现金净额”“现金变动桥接相符”“最终现金流量表勾稽成功”“兜底来源”“受控排除”“已确认调整”；删除固定十二张表、全部人工事项进入重要表、低金额人工继续未决、直接存货重复采购标志和自由选择明确排除等旧口径。

- [ ] **步骤4：写三份ADR。**

  每份记录背景、决定、被否决方案和后果；金额阈值只写“实际执行重要性”，东方农博本次12.5万元仅放验收输入说明。

- [ ] **步骤5：运行文档一致性检查。**

  ```bash
  python scripts/validate_skill.py
  rg -n "固定包含十二张|工作簿恰有十二张|低于12.5万元|低于125000|一个单元格.*多个金额" README.md SKILL.md CONTEXT.md docs/adr docs/requirements docs/superpowers
  ```

  预期：`SKILL_VALID=True`；现行说明中不存在仍可执行的旧口径，历史更新记录若保留必须明确标为历史。

---

### 任务8：普通回归、真实工作簿验收、复核、提交和推送

**文件：**

- 新建：`docs/validation/2026-08-26-东方农博现流表修复验收.md`
- 只读输入：`C:\Users\27651\Desktop\东方农博2025.xlsx`
- 新建输出：桌面新的隔离运行目录及其最终工作簿。

**接口：** 只有普通测试、真实验收、文件打开检查和输入哈希全部通过后才允许提交。

- [ ] **步骤1：运行全部非性能回归。**

  ```bash
  python -m pytest -q --ignore=tests/test_100k_performance.py
  ```

  预期：0失败；记录通过、跳过和子测试数量，不沿用历史数字。

- [ ] **步骤2：运行Skill结构和编译检查。**

  ```bash
  python scripts/validate_skill.py
  python -m compileall -q src tests
  ```

  预期：`SKILL_VALID=True`，编译退出码0。

- [ ] **步骤3：记录真实输入哈希并新建隔离运行。**

  使用已确认参数：整体重要性25万元、实际执行重要性12.5万元、明显微小错报临界值1.25万元、自动修改最低证据分45、期初1,289,314.23元、无外币影响；期末按已确认现金范围计算；富掌柜和P云停车账户排除；四条尾差继续隔离。

- [ ] **步骤4：先统计直接存货入口旧条件影响。**

  输出被旧额外采购标志阻断的业务组成数量、金额、原项目状态和原动作，单独列示`周转材料_低值易耗品`样例；不得把全部人工事项归因于该规则。

- [ ] **步骤5：完成真实运行并检查工作簿。**

  验证：

  - 恰有十三张Sheet；
  - “低金额批量处理”与“重要待复核事项”分开；
  - 所有人工金额列均为单一Excel数值；
  - 主行合计等于明细合计且正表只计算一次；
  - 来源1固定路径、来源2固定摘要；
  - 未决净额、桥接差异和最终状态文案符合实际；
  - 无外部链接、无公式错误、筛选/折叠/下拉可用；
  - 输入哈希前后一致。

- [ ] **步骤6：使用Microsoft Excel完整重算、保存并重新打开。**

  不把程序写入成功当作验收。实际打开最终文件，确认公式缓存、首页状态、正表、两张人工表和勾稽页均可读；未决不为0时必须保持“现金变动桥接相符、现金流量表尚待分类”或相应待处理状态，不冒充最终可使用。

- [ ] **步骤7：写验收报告并进行最终代码复核。**

  报告列明测试数字、真实运行目录、输入哈希、输出哈希、工作簿检查、未决事项、无法解释差异和已知限制。运行：

  ```bash
  git diff --check
  git status --short
  git diff --stat
  ```

  核对没有调试文件、临时脚本、桌面PPTX或无关用户文件进入暂存范围。

- [ ] **步骤8：按用户已授权范围提交并推送。**

  只暂存本任务代码、测试、README、SKILL、CONTEXT、需求、设计、计划、ADR和验收报告；被`.gitignore`忽略但属于本任务的正式文档使用明确文件路径强制暂存。提交前再次检查暂存差异。

  ```bash
  git commit -m "fix: 完善直接法现流分类复核与现金桥接"
  git push origin main
  ```

  预期：提交成功，推送到`origin/main`成功；最终报告提交号和远端分支。

## 自检结果

- 需求五个根因和两项追加要求均有对应任务和失败测试。
- 任务依赖顺序为：语义与来源 → 低金额决定 → 排除控制 → 人工队列 → Excel输出 → 现金桥接 → 文档 → 全量验收。
- 所有重要性比较使用运行参数，没有固定12.5万元逻辑。
- 人工判断单位、现金分配明细单位和正表汇总单位已经分离。
- 未决现金、确认调整和无法解释差异各有独立出口。
- 提交和推送只在完整验收后执行，符合用户本次明确授权。
