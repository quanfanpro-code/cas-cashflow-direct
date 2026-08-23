# cas-cashflow-direct 评分与自动处理入口登记

> 基准日期：2026-08-23
> 权威设计：`C:\Users\27651\Desktop\2026-08-22-cas-cashflow-direct全面重构-design.md`
> 目的：证明评分、动作路由、强制检查和自动改表只有一个正式入口。

## 一、正式版本

| 判断项 | 当前版本 | 唯一生产入口 |
|---|---|---|
| 证据评分 | `2026-08-23-two-source-independence-v12` | `src/cashflow_direct/decision_policy.py` |
| 动作矩阵 | `2026-08-23-authoritative-matrix-v13` | `src/cashflow_direct/decision_policy.py` |
| 重要性及可靠同类累计 | `2026-08-23-single-item-plus-reliable-group-v1` | `src/cashflow_direct/materiality.py` |
| 强制检查 | `2026-08-23-forced-checks-v1` | `src/cashflow_direct/decision_policy.py` |
| 运行结构 | `3.7` | `src/cashflow_direct/versions.py` |

上述版本在运行开始时一并写入状态；任一版本不一致时拒绝续跑，旧运行记录只读，不与新规则混用。

## 二、评分来源登记

一笔业务最多只有两个业务证据来源：本行摘要完整语义、本行完整对方科目路径完整语义。原项目、现金方向、科目编码、规则编号、公司规则和AI意见均不增加来源数或分数。

| 入口 | 职责 | 红线 |
|---|---|---|
| `evidence.py::EvidenceAssessment` | 校验来源质量、独立性和离散分数组合 | 70或90分必须恰有两个独立来源，否则立即拒绝 |
| `classification.py` | 从结构化摘要和完整路径形成候选与来源事实 | 不能把同一路径层级拆成多个来源；弱候选不能伪装成中质量唯一候选 |
| `decision_policy.py` | 计算十种离散评分并命中唯一动作格 | 不接受旧阈值、连续分值或调用方自行改分 |
| `ai_review.py` | 重新解释两项原始证据 | AI不增加来源、不直接决定分数和权限 |

正式离散分值为：`0、10、20、25、35、45、50、55、70、90`。仓库中的其他数字只能作为测试数据、金额、行号或历史文字，不得形成第二套评分表。

## 三、动作与自动修改入口登记

| 动作 | 正式产生位置 | 正式消费位置 | 约束 |
|---|---|---|---|
| `automatic_keep` | `decision_policy.py` | `classification.py`、`pipeline.py` | 原项目有效且修改举证不足时保留 |
| `automatic_fill` | `decision_policy.py` | `classification.py`、`pipeline.py` | 只用于原项目空白或无法标准化的补列格 |
| `automatic_change` | `decision_policy.py`及完成规定AI举证后的`ai_review.py` | `pipeline.py` | 不得由旧标签、关键词或工作簿写入路径直接触发 |
| `ai_review`、`double_ai_review` | `decision_policy.py` | `ai_review.py`、`pipeline.py` | 按动作格和最多三次技术提交执行 |
| `human_batch`、`low_amount_human_batch`、`human_decision` | `decision_policy.py`或规定失败出口 | `pipeline.py`、`workbook_output.py` | 在同一最终工作簿闭环 |

`workbook_output.py`只展示正式决定并用公式承接人工选择，不重新评分、不重新分类。`trace_output.py`只输出同一正式决定，不产生动作。

## 四、强制检查入口

输入非法、现金范围未确认、没有已确认现金腿、来源冲突、业务冲突、现金方向不相容、公司规则过期或越界、新冲减模式和整体重要性等强制检查，统一先于正常动作表执行。各调用方不得用“默认保留原项目”绕过硬冲突。

## 五、重要性与累计入口

单笔重要性只保存`single_level`，可靠同类累计只保存`cumulative_level`。同类键严格由方向、候选项目、标准一级科目、业务对象、用途或项目组成，每个字段只出现一次。旧`effective_materiality`和始终等于单笔层级的`effective_level`已从生产判断中移除。

## 六、兼容对象与非入口项目

| 项目 | 当前结论 | 理由 |
|---|---|---|
| `legacy_key` | 保留 | 兼容旧确认键，不参与评分或动作计算 |
| `apply_duplicate_decisions` | 保留 | 仍有测试覆盖，是否移除需单独兼容性决定 |
| `allowed_operations` | 保留 | 动作路由对象的防御性字段，不是第二套动作表 |
| 测试夹具中的`FALLBACK`文字 | 保留 | 仅验证旧输入兼容展示；生产规则数据中的空兜底规则已移除 |
| 根目录`results.tsv`及历史ADR | 不处理 | 已列入待删除/移动清单，未经用户单独确认不得移动或删除 |

## 七、扫描结论

全仓使用点扫描后，没有发现仍在生产代码中生效的旧`effective_materiality`、`effective_level`或空`FALLBACK`规则；所有自动补列、自动修改、AI复核和人工决定均回到上述正式入口。最终通过状态以《全面复核问题闭环》中的新鲜测试结果为准。
