# evolution_verify — 验证服务（07-verification）

实现 [07-verification.zh-CN.md](../../../../../backend/docs/bot-evolution/07-verification.zh-CN.md)：
套件/用例/划分、验证配置、配对执行、比较器、判定规则与策略可见视图。

| 文件 | 章节 | 内容 |
| --- | --- | --- |
| `enums.py` | §2.1–§2.6 | `Split`（含 `MUST_PASS_SPLITS`）、`SanityResult`、`OverfitFlag`、`VerdictReasonCategory`、`CallerView` |
| `models.py` | §2.1–§2.6 | `Suite`、`Case`、`VerificationProfile`、`RolloutRecord`、`SplitResult`、`Verdict`、`StrategyVerdictView` |
| `compare.py` | §3 第 4 步 | 纯比较器：按 (case, seed) 配对、均值差、置信区间、胜率、新失败用例 |
| `policy.py` | §4.1 | `decide()` 纯函数、种子升级判定、过拟合标记 |
| `service.py` | §4、§5、§6、§9 | `verify`（pending→最终、按 Run/候选/profile 幂等）、`strategy_view`（仅 validation 聚合）、`resume_pending`（重启补跑）、`reconcile_finalized`（补齐下游）、训练评估、holdout 审计 |
| `store.py` | §4.2 | `VerdictStore`：`evolution_verdict`，跨重启读取，终态不改写 |

## 不变量

- **只有 validation 决定改进。** `regression`/`safety` 只能阻断；`train` 绝不用于接受判断。
- **封存。** 策略永远拿不到 holdout、回归与安全的逐用例细节或 id；`strategy_view`
  只返回同一 Run 的 validation `{cases, seeds, mean_delta, ci}` 与原因分类；不按哈希跨 Run 回查。
- **判定不可变。** 状态从 `pending` 变为 `accept`/`reject`/`inconclusive` 后不再改变；
  `reject`/`inconclusive` 是带证据的正常结果，不是 API 错误。
- **执行器与评分器是验证器插件**，由组合根注入；策略代码不参与评分。

## 简化（明确记录）

- 置信区间用正态近似（z 由配置的 confidence 取 1.645/1.96/2.576），未引入 scipy。
- 种子升级为「判定前补足种子」的两段式，不做在线序贯检验；`escalate_within`
  与 `seeds.max` 的语义保留。
- 执行器/评分器的具体实现（沙箱、ClawBench 评分）留给部署层。

## 接入状态（2026-11-24）

- 组合根在平台提供 `Platform.verification` 时接上：`attach_store` → 判定落定回调
  （写 Ledger + `verdict.finalized`）→ 08 门禁。平台不提供时行为不变，候选停在
  `pending`（01–06 兼容路径）。
- 进程内流水线不跨重启；启动时 `resume_pending()` 会把库里停在 `pending` 的判定
  重跑一遍（只补完没跑完的那次，不改写终态）。已落定但下游缺写时，仅重放下游写入。
  升级和故障诊断见 [应用指南](../../../../README.md#判定门禁与评审面0709)。
- 执行器/评分器的真实实现仍在部署层：simulator 目前是**规则型**（读 revision 文件、
  按用例标记打分），不是 LLM judge，也不是质量评测的终点。

- `LocalVerificationService.profile_cases_for` 可按冻结 profile 选择独立用例源，
  未显式配置的 profile 和 train 延用原用例源。可选 `skill-candidate-file@1`
  仅读取固定样例 `skills/rsi-candidate-smoke/SKILL.md`，验证三条规则及完整样例文本；
  不执行 Skill，不是通用安全扫描或行为评测，默认不注册。

## Gene-method composition

`plugins/verifiers/composed.py` 提供 `VerificationMethod` 和
`build_composed_verifier`，按候选相对父版本**实际改动的 Gene 类别**组合平台方法，
不识别策略名或具体 Skill 名。方法具有 `(gene, versioned name)` 身份、固定平台用例和
独立 executor/grader；用例 id 全局唯一，不接收 train/holdout 作为接受依据。

每个受影响类别必须同时有 validation、regression、safety 覆盖；缺覆盖、读取失败或
provider 异常均落为 `inconclusive`，不回退到不相关的 marker profile。执行仍对父/候选
按 case/seed 配对，只有 validation 证明改进，新增 regression/safety 失败阻断接受。
默认 profile 为 `behavior-quality@1`；新失败零容差。每项必过用例保留自身
`grading.pass_threshold`，不借用其他方法或类别的阈值。策略看不到独立测试的逐项细节。
`candidate_cases_for` 是平台侧选例扩展；`profile_candidate_cases_for` 可按冻结 profile
覆盖该扩展，让新 composed profile 与历史规则配置共存；旧 `profile_cases_for` 与 train 路径保持原义。

部署示意（`persona_quality`、`skill_quality` 为部署方实现的独立评测方法，不是策略产物）：

```python
from avernet_evolution.plugins.verifiers.composed import build_composed_verifier, markdown_secret_scan

platform.verification = build_composed_verifier(
    platform.genome,
    methods=[
        persona_quality,
        skill_quality,
        markdown_secret_scan(platform.genome, "persona"),
        markdown_secret_scan(platform.genome, "skills"),
    ],
)
```

质量方法须提供 validation/regression 用例及真实 rollout/judge，不能拿通用 secret scan
代替。`markdown_secret_scan` 每次重新读经权限检查的快照，仅检查 Markdown 的
`BEGIN PRIVATE KEY` / `sk-` 字面前缀，不是完整安全或行为评测器，单独使用不能接受候选。
接线回归中的模型和质量方法是确定性文件断言，只证明组合、判定、门禁和人工审核链路；
真实 Bot 回复改善、线上模型和隔离 Worker 镜像仍须单独验收。历史 smoke profile 不替换。
