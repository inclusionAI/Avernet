# evolution_verify — 验证服务（07-verification）

实现 [07-verification.zh-CN.md](../../../../../backend/docs/bot-evolution/07-verification.zh-CN.md)：
套件/用例/划分、验证配置、配对执行、比较器、判定规则与策略可见视图。

| 文件 | 章节 | 内容 |
| --- | --- | --- |
| `enums.py` | §2.1–§2.6 | `Split`（含 `MUST_PASS_SPLITS`）、`SanityResult`、`OverfitFlag`、`VerdictReasonCategory`、`CallerView` |
| `models.py` | §2.1–§2.6 | `Suite`、`Case`、`VerificationProfile`、`RolloutRecord`、`SplitResult`、`Verdict`、`StrategyVerdictView` |
| `compare.py` | §3 第 4 步 | 纯比较器：按 (case, seed) 配对、均值差、置信区间、胜率、新失败用例 |
| `policy.py` | §4.1 | `decide()` 纯函数、种子升级判定、过拟合标记 |
| `service.py` | §4、§5、§6、§9 | `verify`（pending→最终、按候选幂等）、`strategy_view`（仅 validation 聚合）、`resume_pending`（重启补跑）、训练评估、holdout 审计 |
| `store.py` | §4.2 | `VerdictStore`：`evolution_verdict`，跨重启读取，终态不改写 |

## 不变量

- **只有 validation 决定改进。** `regression`/`safety` 只能阻断；`train` 绝不用于接受判断。
- **封存。** 策略永远拿不到 holdout、回归与安全的逐用例细节或 id；`strategy_view`
  只返回 validation 的 `{cases, seeds, mean_delta, ci}` 与原因分类。
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
  重跑一遍（只补完没跑完的那次，不改写终态）。
- 执行器/评分器的真实实现仍在部署层：simulator 目前是**规则型**（读 revision 文件、
  按用例标记打分），不是 LLM judge，也不是质量评测的终点。
