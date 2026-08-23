# cas-cashflow-direct 全面复核问题闭环

> 日期：2026-08-23
> 权威设计：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
> 当前状态：存在一项尚未修复的致命阻断问题。摘要语义处理没有真正形成分类答案，但正式导入程序仍可把整批空结果标记为完成，导致候选、评分、自动决定和差异报告失真。该问题修复并通过真实样本全流程验证前，本 Skill 不得标记为可用，也不得用于正式业务。

## 一、35项问题闭环矩阵

| 编号 | 设计节号 | 修改文件 | 直接测试或证据 | 当前结果 |
|---|---|---|---|---|
| A-1 | 17.8、19、20 | `workbook_output.py` | `test_workbook_output.py`、`test_duplicates.py` | 通过（阶段4） |
| A-2 | 6.2、17.0 | `components.py`、`pipeline.py` | `test_components.py`、`test_pipeline.py` | 通过（阶段4） |
| A-3 | 2.3、26.7、29.4 | `test_tianwei_acceptance.py` | 客户特例源码扫描、真实摘要非退化门禁 | 通用门禁通过；旧真实结果被拒绝 |
| A-4 | 10、11.5 | `classification.py`、分类规则 | `test_classification.py` | 通过（阶段4） |
| A-5 | 8、11 | `decision_policy.py`、`evidence.py` | `test_decision_policy.py`、`test_classification.py` | 通过（阶段4） |
| A-6 | 6.4、17.0、26.5 | `classification.py`、`pipeline.py` | `test_pipeline.py`、`test_classification.py` | 通过（阶段4） |
| A-7 | 11.1 | `evidence.py`、`decision_policy.py` | `test_decision_policy.py`、`test_final_readiness.py` | 通过（阶段4） |
| A-8 | 7.3、14.4 | `pipeline.py` | `test_pipeline.py`、结构AI测试 | 通过（阶段4） |
| A-9 | 14.4、19 | `ai_review.py`、`pipeline.py` | `test_pipeline.py`、`test_structured_ai_resolution.py` | 通过（阶段4） |
| A-10 | 7、26.4 | `components.py` | `test_components.py` | 通过（阶段4） |
| A-11 | 17.4、21.4 | `pipeline.py` | `test_pipeline.py` | 通过（阶段4） |
| A-12 | 9.4、17.4、18.2 | `classification.py`、`ai_review.py` | `test_classification_routing.py` | 通过（阶段4） |
| A-13 | 13.3 | `materiality.py` | `test_materiality_policy.py`、`test_classification_routing.py` | 通过（阶段4） |
| A-14 | 17.0、19、20、21.6 | `pipeline.py`、`workbook_output.py` | `test_pipeline.py`、`test_workbook_output.py` | 通过（阶段4） |
| A-15 | 22、25 | `decision_policy.py`、登记文档 | 全仓入口扫描 | 通过（阶段4） |
| A-16 | 24.2 | `versions.py`、`pipeline.py` | `test_pipeline.py`、`test_release_bundle.py` | 通过（阶段4） |
| A-17 | 9.2、9.3、21 | `trace_output.py`及语义流程 | `test_structured_ai_review.py`、留痕测试 | 通过（阶段4） |
| A-18 | 22、26.7、26.8 | README、正式追踪文档 | 文档存在性、更新记录、矩阵完整性 | 通过（阶段5） |
| A-19 | 9.2、14.1 | `ai_review.py`、`pipeline.py` | `test_structured_ai_review.py` | 通过（阶段4） |
| A-20 | 8、11.2、21.2 | `classification.py` | `test_classification.py` | 通过（阶段4） |
| A-21 | 21.5、21.8 | `trace_output.py`、`workbook_output.py` | 留痕及工作簿测试 | 通过（阶段4） |
| A-22 | 20.4 | `validation.py`、`pipeline.py` | `test_final_readiness.py` | 通过（阶段4） |
| A-23 | 26 | 测试套件 | 分数、同类组、NOTE、AI终态、工作簿红线 | 通过（阶段4） |
| A-24 | 22至24 | 最小实现及待处理清单 | 全量测试、使用点扫描 | 通过（阶段4） |
| B1 | 6.2、17.0 | `components.py` | `test_components.py`、`test_pipeline.py` | 通过（阶段4） |
| B2 | 15 | `decision_policy.py`、`ai_review.py` | `test_decision_policy.py`、AI结果测试 | 通过（阶段4） |
| B3 | 16 | `decision_policy.py` | `test_decision_policy.py` | 通过（阶段4） |
| B4 | 17.6 | `ai_review.py`、`pipeline.py` | `test_pipeline_decision_routing.py` | 通过（阶段4） |
| B5 | 11.5、17.6 | `classification.py` | `test_classification.py` | 通过（阶段4） |
| B6 | 10、26.1 | 动作表测试 | `test_classification.py`、`test_decision_policy.py` | 通过（阶段4） |
| S-3 | 14.4 | `ai_review.py`、`pipeline.py` | `test_ai_and_materiality.py` | 通过（阶段4） |
| S-4 | 17.5、18.2 | `classification.py`、`ai_review.py` | `test_classification_routing.py` | 通过（阶段4） |
| S-6 | 19、24 | `pipeline.py`、存储层 | `test_pipeline.py` | 通过（阶段4） |
| S-7 | 6.2、7 | `components.py` | `test_components.py` | 通过（阶段4） |
| S-10 | 10、11.5 | 分类规则、`classification.py` | `test_classification.py` | 通过（阶段4） |

## 二、四项严重错误闭环

| 问题 | 根因归属 | 最小修复 | 当前证据 |
|---|---|---|---|
| U列“单笔金额”是文本/格式错误 | 执行错误：工作簿写出层错误复用了文字留痕 | 直接写入业务现金变化绝对值，沿用金额格式 | 构造测试通过；真实结构冒烟111行均为数值且与H/I/K数值金额格式一致 |
| AK列混入“采用系统首选项目” | 执行错误：展示清单和AL控制清单复用了同一选项元组 | AK只拼接具体标准项目和“明确排除” | 构造测试通过；真实结构冒烟111行均未出现该操作指令 |
| AL除首行外缺少“采用系统首选项目” | 设计原文有原则但系统首选回退顺序不够明确，执行又只在新候选非空时加入操作项 | 权威设计第29.2节明确顺序；每行AL固定首项，没有新候选时回退原标准项目，确无首选时提示改选 | 构造测试通过；真实结构冒烟111行均恰有一个下拉且首项正确 |
| 差异表只有一行且该行像0分修改 | 差异表范围本身没有错误；执行错误在于把内部划转按普通评分解释，旧真实摘要又退化导致没有形成应有的70/90分系统决定 | 差异表继续拒绝待人工；内部划转写“评分不适用”和配对依据；真实摘要增加非退化门禁 | 真实结构冒烟仍为1行但已明确内部划转评分不适用；0条有效摘要语义不能用于证明70/90分自动改项 |

## 三、致命阻断问题（已闭环）

| 编号 | 问题 | 已确认根因 | 业务影响 | 当前状态 | 闭环条件 |
|---|---|---|---|---|---|
| P0-1 | 摘要语义处理未真正完成，但整批空分类答案被当成完成 | 旧摘要结果文件的5,355条记录均未填写具体项目或候选项目；正式导入程序只逐条检查格式，没有拒绝整批空结果；真实验收测试此前也只检查任务编号和摘要是否匹配 | 111条待复核业务的摘要来源质量全部为0分，其中68条因完整对方科目路径也未形成有效证据而显示“未形成候选”；70/90分、自动修改和差异报告均失去可信基础 | 已闭环：正式入口改为固定摘要语义解析和整批非退化门禁；旧关键词及旧占位结果不再兜底；新运行取得非零质量、70/90分和自动改项 | 新隔离运行处理5,355项摘要；质量分布0/10/25/45分别为552/3,761/512/530；70/90分分别为348/521；自动修改2项、差异表3行，逐项抽查证据与设计一致 |

旧自动测试曾经全部通过，只能说明旧测试没有覆盖正式导入漏洞。当前闭环依据不是沿用旧结果，而是共同根因修复、新增回归检查和新隔离真实运行三项证据同时成立。

## 四、自动验收与独立代码复核

执行结果：

- 最终完整自动测试：`634 passed, 92 subtests passed`，0失败；其中100,000行规模检查已通过，收尾阶段未重复执行；
- 真实文件通用管线测试：`1 passed`，读取14,399条原始分录、形成6,184个业务组成和5,355项唯一摘要；
- 三项同级需求、四项工作簿严重错误及相关流程回归均为0失败；
- `python -X utf8 -m compileall -q src tests scripts`：通过；
- `python -X utf8 scripts/validate_skill.py`：`SKILL_VALID=True`；
- `git diff --check`：通过，仅有Git对未来换行转换的提示，无空白错误；
- 全部JSON文件均可按UTF-8解析；中文文本扫描未发现Unicode替换字符或连续问号乱码；
- 生产代码和测试未发现LM Studio、端口12345或其他模型服务调用残留；摘要语义本次全部由固定程序完成，Agent任务为0。分类AI的54项任务仅使用明确的空技术失败注入验证失败出口，没有调用任何模型，也没有形成业务证据；
- Ponytail复核结论为当前修复复用既有批次、统一行动中心、差异生成器和工作簿写出层，没有新增依赖或第二套判断中心。

## 五、真实验收门禁复核（当前结果）

- 旧14:55运行目录继续只作为缺陷复现材料；其中614项通过、111项人工决定、8,753,981.55元勾稽差异和十三张表工作簿不作为当前证据。
- 当前新运行目录为`C:\Users\27651\Desktop\真实测试案例\真实验收输出_最终规则_20260824_0019\运行_20260824_000831_920d28d5`。系统重新读取两份真实输入，得到14,399条原始分录、6,184个业务组成和5,355项唯一摘要；运行前后输入SHA-256分别保持`B644A9E6...D53F0`和`231799DF...392B`不变。
- 摘要语义由固定程序逐项形成，本次Agent任务为0；摘要质量0/10/25/45分分别为552/3,761/512/530。最终总分70分348项、90分521项，共869项取得双来源高分证据。
- 系统自动修改2项，均为70分且经逐项核对：360元车船税改入经营税费；25元手续费退款按同项反向处理。差异表共3行，除上述2项外仅有1项内部划转，并明确显示评分不适用及配对依据。
- 最终工作簿恰有十二张表；U列金额格式、AK展示清单、AL逐行下拉、公式错误扫描和主要页面视觉检查全部通过。
- 程序和真实样本自动化冒烟验收通过，P0-1解除。由于工作簿仍有75项待人工确认和8,438,340.55元勾稽差异，业务状态必须保持“待完成人工确认”，尚不能称为“最终可使用”。

## 六、保留事项

`results.tsv`、ADR和兼容接口均未删除或移动。经用户明确确认，已取消并通过Windows回收站移出`src/cashflow_direct/materiality_group_workbook.py`和`references/直接法分类规则.json`；历史运行、真实输入和旧工作簿均未删除。当前可以提交并推送代码和文档，但新工作簿在75项人工确认完成、Microsoft Excel重算保存且勾稽差异归零前仍不能标记“最终可使用”。
