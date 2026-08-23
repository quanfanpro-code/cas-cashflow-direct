# cas-cashflow-direct 复核问题实施追踪

> 日期：2026-08-23
> 状态：用户已于2026-08-23确认（Requirement Workflow 检查点2已通过）
> 性质：权威设计的实施落点与验收追踪，不是第二份设计文档

## 一、唯一设计基准

本轮直接使用：

`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`

该文件已经完成方案比较、统一结构、证据模型、动作表、强制检查、工作簿闭环、版本迁移和测试设计。本轮不另写设计稿，不按当前代码反推设计；用户确认四项严重错误的根因后，直接在同一权威设计中补充第29节纠偏规则。

设计方案固定为第3节方案B“统一判断中心”：保留现有模块边界，把证据解释、离散评分、重要性、强制检查和动作路由集中到统一判断中心；不继续分散打补丁，也不改造成全量事件账本。

现有ADR `0005-四档证据质量与十种离散组合.md`、`0006-统一判断中心.md`、`0007-运行记录版本不得混用.md`、`0008-修改原项目由系统举证.md` 已记录难以逆转的核心决定。本阶段没有新的架构选择，不新增ADR。

## 二、共同根因与落点

| 共同根因 | 权威设计 | 主要实现落点 | 处理原则 |
|---|---|---|---|
| 旧标签仍能制造现金腿或拆分依据 | 6.2、17.0、25 | `components.py`、`pipeline.py` | 现金范围和已确认现金腿是唯一金额事实；旧项目只作比较 |
| 多行业务组成没有保持原始行和金额守恒 | 7、21.1、21.8 | `components.py`、`component_structure_ai.py` | 按唯一金额关系连接；不唯一就形成结构确认任务，绝不整单合并 |
| 证据质量、独立性和动作路由仍有旧口径 | 8至17、22 | `evidence.py`、`classification.py`、`decision_policy.py` | 两来源、四档质量、十种分数和两张动作表只有一个计算入口 |
| AI失败、轮次和输入边界未形成终态 | 14 | `ai_review.py`、`component_structure_ai.py`、`pipeline.py` | 每个任务最多三次；漏答也计失败；普通任务不看候选池；按格退出 |
| 运行状态、数据库与工作簿状态不一致 | 19、20、24 | `pipeline.py`、`storage.py`、`workbook_output.py` | 决定状态先正式持久化，工作簿消费同一状态；人工在同一工作簿闭环 |
| 版本、留痕和追踪不完整 | 21、22、24、26 | `versions.py`、`trace_output.py`、`validation.py`、文档 | 所有判断版本固定；原文位置、排除原因、问题闭环均可验证 |

## 三、逐项问题追踪

### 3.1 A类问题

| 编号 | 设计节号 | 主要代码落点 | 测试落点 | 关闭证据 |
|---|---|---|---|---|
| A-1 | 17.8、19、20 | `duplicates.py`、`workbook_output.py` | `test_workbook_output.py`、`test_duplicates.py` | 未决定业务的重复组可生成工作簿，调整公式随最终人工项目联动，不再查询空编号 |
| A-2 | 6.2、17.0 | `components.py`、`pipeline.py` | `test_components.py`、`test_pipeline.py` | 无已确认现金腿时生成逐行清洗请求；旧项目和旧流量金额不能绕过 |
| A-3 | 2.3、26.7 | 无生产代码专用改动 | `test_tianwei_acceptance.py` | 真实验收只检查通用不变量，测试中不存在客户名称、金额、行号或关键词门槛 |
| A-4 | 10、11.5 | `classification.py`、分类规则数据 | `test_classification.py` | 裸“咨询费、服务费”最高为弱或歧义，不取得中质量唯一候选 |
| A-5 | 8、11 | `decision_policy.py`、`evidence.py` | `test_decision_policy.py`、`test_classification.py` | 来源独立性双向判断，任一来源增加独立分类事实均被识别 |
| A-6 | 6.4、17.0、26.5 | `normalization.py`、`semantic_mapping.py`、`classification.py` | `test_pipeline.py`、`test_classification.py` | 空、断层、错序、别行和仅现金路径均有产生方、异常状态和出口 |
| A-7 | 11.1 | `decision_policy.py`、`validation.py` | `test_decision_policy.py`、`test_final_readiness.py` | 70或90分而独立来源数不是2时立即停止 |
| A-8 | 7.3、14.4 | `component_structure_ai.py`、`pipeline.py` | `test_component_structure_ai.py`、`test_pipeline.py` | 结构任务第三次失败形成终态并立即转Agent结构确认，不再新建结构轮次 |
| A-9 | 14.4、19 | `pipeline.py` | `test_pipeline.py`、`test_structured_ai_resolution.py` | 漏答、非法答和越界答统一累计；三次后执行当前动作格失败出口且队列清空 |
| A-10 | 7、26.4 | `components.py` | `test_components.py` | 多业务凭证保持逐行业务身份；大凭证退化为分配不明确而非整单强证据 |
| A-11 | 17.4、21.4 | `pipeline.py`、`cli.py`、运行状态 | `test_pipeline.py` | M3新冲减模式展示并保存主体、期间、范围、笔数、金额和后续影响 |
| A-12 | 9.4、17.4、18.2 | `ai_review.py`、`classification.py`、运行状态 | `test_classification_routing.py` | “仅本次采用”只命中当前业务，不进入后续业务的活动规则集合 |
| A-13 | 13.3 | `materiality.py` | `test_classification_routing.py`、`test_materiality_policy.py` | 同类键只使用方向、候选、标准一级科目、业务对象、用途或项目，各字段各出现一次 |
| A-14 | 17.0、19、20、21.6 | `pipeline.py`、`workbook_output.py` | `test_pipeline.py`、`test_workbook_output.py` | 能可靠保留现金腿和金额的非法输入进入同一工作簿，人工选择后公式更新状态和正表 |
| A-15 | 22、25 | `decision_policy.py`、`classification.py`、`pipeline.py`、登记文档 | 动作表枚举、全仓旧值扫描 | 旧分数、旧阈值和所有自动改表入口都有登记；未登记残留为零 |
| A-16 | 24.2 | `versions.py`、`pipeline.py` | `test_pipeline.py`、`test_release_bundle.py` | 运行版本包含重要性及累计规则版本、强制检查版本，任一不一致拒绝续跑 |
| A-17 | 9.2、9.3、21 | `models.py`、`semantic_mapping.py`、`classification.py`、`trace_output.py` | `test_structured_ai_review.py`、`test_workbook_decision_trace.py` | 每个语义判断保存原文位置并进入完整留痕 |
| A-18 | 22、26.7、26.8 | 本追踪文件、最终闭环报告、`README.md`、`.gitignore` | 文档存在性、Changelog和追踪完整性检查 | N-01至N-33、A/B/S问题均能追到设计、代码、测试和结果，README更新记录反映本轮新鲜结果 |
| A-19 | 9.2、14.1 | `semantic_mapping.py`、`pipeline.py` | `test_structured_ai_review.py` | 摘要任务和结果包含否定、不确定、条件性表达及原文位置 |
| A-20 | 8、11.2、21.2 | `classification.py` | `test_classification.py` | 来源冲突保留真实来源数和独立性，不固定写成两个独立来源 |
| A-21 | 21.5、21.8 | `trace_output.py`、`workbook_output.py` | `test_trace_output_filtering.py`、`test_workbook_decision_trace.py` | 共同列顺序为来源定位、原始业务、标准化、系统、AI、人工、技术字段 |
| A-22 | 20.4 | `validation.py`、`pipeline.py` | `test_final_readiness.py` | 生成前统一拒绝任何没有原因的明确排除 |
| A-23 | 26 | 现有测试套件 | 对应各测试文件 | 评分正反例、55分边界、同类拆组、NOTE状态、AI终态、工作簿闭环均有直接测试 |
| A-24 | 22至24、最小实现原则 | `decision_policy.py`、`materiality.py`、`classification.py`、规则数据、ADR和根目录遗留物 | 全量测试、使用点扫描 | 删除或合并仅限能够证明无生产或兼容用途的项目；文件删除另行确认 |

### 3.2 B类修订

| 编号 | 设计节号 | 实现与测试落点 | 关闭证据 |
|---|---|---|---|
| B1 | 6.2、17.0 | `components.py`、`test_components.py`、`test_pipeline.py` | 单边明细只能由明确指向已确认现金账户的对方科目做代理现金腿 |
| B2 | 15 | `decision_policy.py`、`ai_review.py`、`test_decision_policy.py` | 55分、候选不同、M2进入一次AI后必要的A/B互盲，只有双方同一新项目且各自70或90才修改 |
| B3 | 16 | `decision_policy.py`、`test_decision_policy.py` | 原项目空白或无法标准化、70或90、M0直接自动补列，不调用AI |
| B4 | 17.6 | `ai_review.py`、`test_pipeline_decision_routing.py` | M2代扣个税已完成的A/B结果在本笔后续路由复用，不重复安排 |
| B5 | 11.5、17.6 | `classification.py`、`test_classification.py` | “缴纳税款”中档正例明确排除代扣个税语境并有专门测试 |
| B6 | 10、26.1 | `test_classification.py`、`test_decision_policy.py` | 不新增“保持原项目”的评档抽查；以动作表边界测试防回归 |

### 3.3 原存疑项的明确差异

| 编号 | 设计节号 | 实现与测试落点 | 关闭证据 |
|---|---|---|---|
| S-3 | 14.4 | `ai_review.py`、`pipeline.py`、`test_ai_and_materiality.py` | 普通AI任务不包含候选池；方向和冲减强制任务才可包含过滤后的相容候选 |
| S-4 | 17.5、18.2 | `ai_review.py`、`classification.py`、`test_classification_routing.py` | 过期、越界和冲突规则均转人工，不静默忽略 |
| S-6 | 19、24 | `pipeline.py`、`storage.py`、`test_pipeline.py` | M3转待人工状态同时写入运行状态和数据库，工作簿读取同一状态 |
| S-7 | 6.2、7 | `components.py`、`test_components.py` | 被排除账户的旧现流标签不驱动拆分 |
| S-10 | 10、11.5 | 分类规则数据、`classification.py`、`test_classification.py` | 只有“应付账款”一级最高为弱且候选不唯一 |

### 3.4 四项严重错误纠偏

| 事项 | 设计节号 | 实现与测试落点 | 关闭证据 |
|---|---|---|---|
| U列金额类型与格式 | 29.2 | `workbook_output.py`、`test_workbook_output.py` | 直接写业务现金变化绝对值，数值类型及金额格式专项测试通过 |
| AK列选项污染 | 29.2 | `workbook_output.py`、`test_workbook_output.py` | 展示列不含“采用系统首选项目” |
| AL列首项和系统首选回退 | 29.2 | `pipeline.py`、`workbook_output.py`及流程/工作簿测试 | 每行固定首项；新候选、原项目回退及确无首选三种状态均有明确出口 |
| 差异表范围及0分误解 | 21.7、29.3、29.4 | `differences.py`、`test_differences.py`、`test_tianwei_acceptance.py` | 待人工拒绝进入；内部划转显示评分不适用；占位摘要被真实验收门禁拒绝 |

## 四、独立复核所列七项未决事项的处理

这些事项不再向用户重复询问；权威设计已给出业务答案，剩余部分属于实现事实或第五阶段验证事实。

| 事项 | 权威设计裁定或本轮处理 |
|---|---|
| 55分M1一次AI失败后的门槛 | 第16节55分M1：一次AI未达到55分唯一候选后转A/B，必要时C轮；仍无多数时人工决定 |
| 运行时语义解释是否确认 | 第24节：运行开始固定全部判断版本；任何版本变化重新运行，旧记录只读，不混用 |
| 路径断层、错序和属于别行的机械规则 | 第6.4节：只连接输入给出的明确层级；缺层、顺序不明按非法输入，明显属于别行或结构不可能按无效；不得推测修复 |
| 被排除组成的现金行是否进入全量留痕 | 第6.2、21.8节：被排除账户行不单独占行，只作为相关现金行的路径证据；实际参与计算的纳入范围现金行进入留痕 |
| M0批量与M1逐笔如何交互 | 第7.3节：结构仍不唯一时，M0由Agent批量展示，M1逐笔确认；这是分类前结构门禁，不在最终项目工作表替代 |
| 多份一致AI结果选哪份作为代表 | 第14.4节：每份结果独立保存并分别重算；系统只消费共同支持的候选和动作条件，不合并成新的代表证据或代表分数 |
| 天微真实验收是否实际完成 | 14:55旧运行实际执行过，但摘要语义是全量空候选、空分类事实的占位结果，现已被非退化门禁拒绝；本轮没有生成新摘要，故当前真实验收明确为待完成 |

## 五、状态不变量

1. **现金事实不变量**：没有已确认现金腿，就没有现金业务、业务组成、评分或AI任务。
2. **金额守恒不变量**：每分钱只分配一次，业务组成合计等于现金变化，原始行不得重复使用。
3. **证据不变量**：最多两个业务来源；公司规则、AI、原项目、方向和规则编号不增加来源或分数。
4. **动作唯一不变量**：每个合法组合恰好命中一个动作；强制检查优先于正常动作表。
5. **AI终态不变量**：成功、三次技术失败或规定后续出口完成后均有终态；最终工作簿不得存在等待AI。
6. **决定持久化不变量**：运行状态、数据库、留痕和工作簿消费同一正式决定状态。
7. **工作簿闭环不变量**：等待人工项目只在同一工作簿完成；人工选择通过公式更新正表、勾稽和最终状态。
8. **版本不变量**：影响判断的全部版本在运行开始固定；版本不一致拒绝续跑。

## 六、最小实现边界

1. 先修共同根因，不在每个调用方重复加补丁；
2. 复用现有数据类、运行状态、SQLite存储、XlsxWriter和OpenPyXL，不增加依赖；
3. 不新建第二个决策中心、第二套版本结构或第二个工作簿生成流程；
4. `legacy_key`、`apply_duplicate_decisions`、`allowed_operations` 在兼容对象未查清前保留；
5. `results.tsv`、空规则、历史ADR重命名和确认无调用的历史接口形成待删除/移动清单，用户确认前保持原状；
6. 阶段4继续封存天微真实文件；阶段5只读取旧摘要结果验证非退化门禁，不调用模型、不生成新摘要、不覆盖旧工作簿。
7. 自动验收和独立代码复核通过、README如实披露真实验收待完成后进入阶段6；最终全量验证通过再提交并推送`origin/main`。不得把代码交付写成真实案例验收通过。

## 七、阶段2检查结论

权威设计已经回答全部设计验收问题，本轮没有新的业务或架构选择。检查点2通过后，只需进入阶段3编写逐文件、逐步骤、可验证、可回退的实施计划；实施计划确认前不修改业务实现。
