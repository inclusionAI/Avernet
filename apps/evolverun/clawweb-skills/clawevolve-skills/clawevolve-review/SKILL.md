---
name: clawevolve-review
description: "ClawEvolve 结构化 Review v2。根据 post-Tune optimization、aggregate validation、acceptance 与 Tune manifest，提出需要区分的机制假设、替代原因、区分信号和保护行为；只写 review_decision.json，由 runner 渲染 evolution.spec.v1。"
allowed-tools: Read, Bash, Write
---

# ClawEvolve Structured Review v2

## 职责边界

Review 只回答：

1. 需要区分什么 failure mechanism？
2. 有哪些仍然 plausible 的替代原因？
3. 什么 capability-level 行为信号能区分这些解释？
4. 哪些 accepted baseline 行为必须保护？
5. 当前 hypothesis 是 suspected、testing、supported 还是 falsified？
6. 什么新证据出现后应重新考虑？

Review 不回答：

- 具体怎么改；
- 使用哪个 mutation operator；
- 修改哪个文件；
- exact patch / proposed change；
- 增加、删除或重排什么 Block/anchor/步骤文案。

具体实现决策属于 Tune。

## 可读取输入

- objective.md；
- 输入 evolution.spec.v1 的 experiment questions、protected behaviors 和 contracts；
- post-Tune full candidate optimization report；
- candidate effect/protected/full-opt gate；
- Tune report、change manifest 与 diff；
- aggregate validation summary；
- acceptance / replication decision；
- runtime experiment ledger 和 failure registry 中的机制级信息。

## Validation 防火墙

- validation 只用于 promotion/generalization 门禁，不是下一轮训练数据。
- 禁止读取 validation resultPath、逐 task report、transcript、case/template、rubric breakdown。
- 禁止输出 validation task id、单 case 分数、expert/rubric 字段、答案、原文或 case-specific workaround。
- 只能使用 runner 注入的 aggregate validation 与抽象 acceptance decision。

## 效果解释原则

- `accepted/rejected` 描述的是 promotion 结果，不等同于 `effective/ineffective`。即使未晋升，也要分别记录已经观察到的正向行为、回归行为和证据等级。
- 不得只根据 aggregate score 判断机制。分别审视：
  - `reasoning_quality`：识别、准确度、证据和建议；
  - `delivery_reliability`：最终输出完整性、确认、stage completion、非重复和一次性交付。
- 中段推理提升但尾段交付消失，应描述为 `reasoning-only improvement with delivery regression`，不能写成整体成功。
- 高分但存在重复区块、尾段未完成或 judge/breakdown 缺失时，必须保留 scorer blind spot / observability alternative cause。
- 单次 targeted observation 默认只记为 `observed_once`；只有重复、跨任务或 held-out aggregate 证据才逐步升级为 `replicated`、`cross_task_positive`、`heldout_positive`。
- Review 应把已观察到的成功机制转成 capability-level protected behavior，避免后续 Tune 在解决新问题时无意撤销；但不得把候选的具体 patch 文案写入 spec。

## 唯一输出

只写：

```text
{round_dir}/spec/review_decision.json
```

不得直接写 spec-vN.md、spec-vN.json 或 spec_update_report.md；这些由 runner 确定性生成。

Schema：

```json
{
  "schema_version": "evolution.review_decision.v2",
  "round_id": 1,
  "acceptance_decision": "needs_replication",
  "summary": "机制级短总结",
  "confidence": "low",
  "hypotheses": [
    {
      "hypothesis_id": "HYP-001",
      "failure_signature": "complex_case_stage_abandonment",
      "status": "suspected",
      "claim": "复杂任务中关键路径过长可能导致后续阶段放弃",
      "alternative_causes": ["instruction_density", "output_budget", "tool_latency"],
      "disambiguation_signal": "保持业务目标不变时，stage_completion 是否稳定恢复",
      "protected_behaviors": [{"behavior_id": "complete_output_contract", "description": "保持完整输出契约", "metric": "output_contract_complete", "direction": "maintain", "min_value": 1, "max_drop": 0}],
      "confidence": "low",
      "revisit_condition": "独立重复实验产生一致证据"
    }
  ],
  "direction_decisions": [
    {
      "direction_id": "DIR-001",
      "decision": "keep|strengthen|split|freeze|reject|defer",
      "confidence": "low",
      "rationale": "机制级理由",
      "revisit_condition": "重新考虑条件"
    }
  ]
}
```

## 判定规则

- 单次 observation 默认只能得到 suspected/testing。
- 只有可复现、身份可比的证据才可标记 supported/falsified。
- 不得从单轮结果宣布永久 retire、方向耗尽或模型天花板。
- 当前不支持的方向可临时 freeze，并必须写 revisit_condition。
- optimization evidence 必须来自 post-Tune candidate optimization；不得把 pre-Tune accepted baseline 当成本轮 candidate 结果。
- candidate effect gate 失败时，重点更新 failure mechanism 和替代原因，不设计补丁。

## 安全校验

以下字段或语义一旦出现必须判非法：

- operator
- target_file
- exact_change
- proposed_change
- patch
- implementation
- Suggested Direction
- Active Optimization Directions
- “改成 Block A/B/C”“增加选择性锚点”等实现方案
- validation task id / expert field / case answer

完成后回复末尾输出：`SPEC_COMPLETE`
