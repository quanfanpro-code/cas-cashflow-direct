# cas-cashflow-direct 全面复核问题闭环

> 日期：2026-08-23
> 权威设计：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
> 当前状态：阶段4自动验收和阶段5独立代码复核已完成；旧真实案例结果被非退化门禁拒绝，真实案例验收仍待有效摘要语义输入。本结论允许交付代码修复，但不宣称取得新的真实案例验收通过。

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
| U列“单笔金额”是文本/格式错误 | 执行错误：工作簿写出层错误复用了文字留痕 | 直接写入业务现金变化绝对值，沿用金额格式 | 数值类型、金额及格式专项测试通过 |
| AK列混入“采用系统首选项目” | 执行错误：展示清单和AL控制清单复用了同一选项元组 | AK只拼接具体标准项目和“明确排除” | AK排除操作指令专项测试通过 |
| AL除首行外缺少“采用系统首选项目” | 设计原文有原则但系统首选回退顺序不够明确，执行又只在新候选非空时加入操作项 | 权威设计第29.2节明确顺序；每行AL固定首项，没有新候选时回退原标准项目，确无首选时提示改选 | 有候选、原项目回退、确无首选三种路径测试通过 |
| 差异表只有一行且该行像0分修改 | 差异表范围本身没有错误；执行错误在于把内部划转按普通评分解释，旧真实摘要又退化导致没有形成应有的70/90分系统决定 | 差异表继续拒绝待人工；内部划转写“评分不适用”和配对依据；真实摘要增加非退化门禁 | 差异边界、内部划转说明及旧占位结果拒绝测试通过 |

## 三、自动验收与独立代码复核

执行结果：

- 最终执行`python -m pytest -q`：`616 passed, 2 skipped, 93 subtests passed`，耗时181.65秒；两个跳过项分别是未设置真实验收目录、未设置两份等价明细的比较参数；
- 四项严重错误定向测试、相关工作簿/流程测试及全量回归均为0失败；
- `python -X utf8 -m compileall -q src tests scripts`：通过；
- `python -X utf8 scripts/validate_skill.py`：`SKILL_VALID=True`；
- `git diff --check`：通过，仅有Git对未来换行转换的提示，无空白错误；
- 4个JSON文件均可按UTF-8解析；中文文本扫描未发现Unicode替换字符或连续问号乱码；
- 生产代码和测试未发现LM Studio、端口12345或其他模型服务调用残留；Ponytail复核结论为当前修复已复用既有批次、差异生成器和工作簿写出层，没有新增依赖或第二套判断中心。

## 四、真实验收门禁复核

- 直接把14:55运行目录的`摘要语义判断结果_匹配当前输入.jsonl`送入新增门禁，实际失败信息为：“摘要语义结果全部为空候选或空分类事实，属于占位结果，不能用于真实验收”。
- 因此旧记录中的614项通过、111项人工决定、8,753,981.55元勾稽差异和十三张表工作簿全部降级为修复前复现材料，不再写作阶段5通过证据。
- 本次四项修复、自动回归和交付结论没有采用任何模型输出，没有生成新的摘要语义结果，也没有覆盖旧工作簿。缺少有效真实摘要语义时，真实测试按规则跳过，真实案例验收状态明确为“待完成”。
- 当前阶段5结论仅包括：代码独立复核通过、非退化门禁有效、旧错误结论已撤销。后续如提供有效摘要语义结果，应另行重跑并核对70/90分、自动修改、待人工、正表核对报告和差异明细。

## 五、保留事项

`results.tsv`、ADR和兼容接口均未删除或移动，详见《待删除或移动清单》。未取得用户确认的删除项保持原状。代码修复的交付、提交和推送按用户既有授权执行；真实案例验收继续保持“待完成”，两者不得混写。
