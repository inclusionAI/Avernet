# evolution_promote — 晋升服务（08-promotion）

实现 [08-promotion.zh-CN.md](../../../../docs/bot-evolution/08-promotion.zh-CN.md)：
平台底线、风险分级、门禁、评审队列与引用移动。它是唯一被允许移动 bot
的 `active` / `previous` / `canary` 的组件（DR-2）。

| 文件 | 章节 | 内容 |
| --- | --- | --- |
| `enums.py` | §2、§4、§5、§7 | `RiskTier`（T0–T3）、`GateOutcome`、`ReviewStatus`、`FloorCheckName`、`PromotableRef` |
| `models.py` | §2 | `TierAssessment`/`OpTier`/`TierRaise`、`GateCheck`/`GateDecision`、`ReviewItem`、`Promotion` |
| `risk.py` | §5.1 | 操作→等级表、取最大值、`rewrite`/`guardrail_touch`/所有者覆盖的提升 |
| `floor.py` | §4.2 | 提交时的静态底线：可变基因与固定项、允许的基因、密钥扫描插件、大小与重写 |
| `gate.py` | §4.4 | `decide()` 纯函数：检查 1–5 决定可晋升性，检查 6 决定是否需要人 |
| `service.py` | §6、§7、§9 | `check_floor`/`decide`/`reconsider`/`review_queue`/`approve`/`reject`/`promote`/`candidate_report` |

## 不变量

- **策略永不移动 `active`。** 自动晋升的参与者是 `gate_policy`；人批准的参与者是用户。
- **T3 永不自动**，即使绑定把上限设为 T2。
- **软条件转评审**：动用回归容差、holdout 事件、bot 被冻结、达到每日上限，都会把
  `auto_promote` 降为 `needs_review`。
- **漂移只允许所有者推翻**：`drift(bot)` 非空时，非用户的晋升以 `Drifted` 拒绝。
- **CAS**：`active` 不是候选的父版本时以 `StaleParent` 拒绝，除非调用方显式覆盖
  （所有者的调和路径）。

## 简化（明确记录）

- 密钥/PII 与出站 URL 扫描以可注入的扫描器实现，真实规则与仓库 pre-push hook 共享。
- 底线的 schema/base 两项由基因组注册表在记录候选时强制，这里不重复解析。
- 应用（Manifest apply / 发布流程）通过注入的 `apply` 回调完成；本模块只记录
  `Promotion` 与投递答复，不保存所应用文档的内容。
