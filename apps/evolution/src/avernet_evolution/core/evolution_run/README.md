# evolution_run — canonical runtime (01–06)

Bindings, triggers, Run/Job/Operation, leases, budgets, candidate submission and
execution evidence live here. This core is transport-free and consumes ports;
no Backend/platform SDK imports are allowed. Independent assembly, configuration,
HTTP paths and persistence limitations are owned by the
[app guide](../../../../README.md).

## Context Boundary

```yaml
purpose: Execute registered strategies with durable inputs, fenced jobs and bounded capabilities.
provides:
  - EvolutionApi
  - EvolutionRunner
  - RunService
  - JobChannel
  - StrategyContext
consumes:
  - GenomePort
  - StrategyRegistryPort
  - GovernancePort
  - KillSwitchesPort
  - ExperienceQueryFactory
  - JobWorkerSandbox
internal_dependencies: []
```

### Change impact

Strategy SDK consumers, Worker transports and platform providers depend on these
contracts. Storage/lease changes require restart, idempotency and stale-fence
regressions. Preserve wire compatibility; do not restore Backend-owned execution.

## 组成

| 文件 | 对应文档 | 内容 |
| --- | --- | --- |
| `enums.py` | §2.4–§2.6、§11.1 | `RunStatus`、`EndReason`、`OperationKind`、`ChargeSource`、`RiskCeiling` |
| `budget.py` | §2.1 | `Budget` / `BudgetUsage`，逐维度比较与耗尽判定 |
| `bindings.py` | §2.2、§3、§4 | `Binding`、`BotBindings`、触发器与五字段 cron 校验 |
| `records.py` | §2.4–§2.8 | `Run`、`Job`、`Operation`、`Workspace`、`RunCandidate` |
| `ports.py` | §13、01–08 | 本服务依赖的端口（Genome、StrategyRegistry、Verifier、Promotion、Ledger、Experience、KillSwitches、Governance） |
| `data_scope.py` | 内置手动 RSI | 从已验证回执或持久 Run 恢复任务局部操作人，不向 Worker 暴露服务凭据 |
| `events.py` | 运行可观测性 | SQLite 元数据事件日志，按 run/job/worker/sandbox 关联，不存任意日志正文 |
| `store.py` | §12 | SQLite 持久化：绑定、运行、幂等键、作业、操作、工作区、候选、费用、触发记录 |
| `bindings_service.py` | §3.2、§13 | 八项绑定检查、PUT/GET 的 ETag 语义 |
| `run_service.py` | §5、§6、§13 | 提交（漂移检查、冻结验证配置及所属 Binding 的晋升上限、预留预算）、幂等、查询、取消、候选 |
| `job_service.py` | §7、§13 | 认领、心跳、完成、失败、租约过期重派发、宿主兜底重派发（不校验 fencing token） |
| `operation_service.py` | §8、§13 | 幂等启动、按 id 查询、取消、结束回填与计费 |
| `budget_service.py` | §11、§13 | 预留的上下限检查、计费累计与 `budget_exhausted` |
| `context.py` | §5.1、§9 | 真实 `StrategyContext`：workspace、candidates、models、experience、operations、budget、log、artifacts、cancelled |
| `host.py` | §9 | 进程内宿主：认领租约、心跳续期、执行 `run(ctx)`、映射结束状态 |
| `cron.py` | §4.1 | 五字段 cron 解析与 UTC 分钟匹配（含 DOM/DOW 的“或”语义） |
| `scheduler.py` | §4 | 触发器调度：按 (binding, fire_key) 幂等触发，跳过冻结/漂移/忙碌并留痕 |
| `sandbox.py` | §10 | 沙箱端口与规格：digest 固定镜像、env 白名单、出网仅作业端点、资源与 TTL |
| `container_host.py` | §10 | 容器宿主：无排队作业即空转；否则建沙箱、执行 worker、等待作业排空；worker 退出却留下开放作业 → 按 §7.2 重派发；结束或异常时尝试销毁，销毁失败向上传播 |
| `submission.py` | §5.4 | 候选提交流水线（底线→记录→实验记录→验证），上下文与作业通道共用 |
| `job_protocol.py` | §7 | 作业协议：HMAC 令牌认领、心跳、文件、提交、判定、完成/失败（传输无关） |
| `job_wire.py` | §7 | 与框架无关的作业协议负载编码（文件字节 base64），供路由与 worker 客户端共用 |
| `worker.py` | §7、§10 | 容器内 worker：经作业协议认领作业，用本地工作区执行策略并回写 |
| `runner.py` | §10 | 执行循环：`Lifecycle` 参与者，每轮「补齐已落定但下游缺写的判定 → 回收过期租约 → `scheduler.tick()` → `host.run_once()`」，消费进化作业队列（由独立服务配置控制） |

`api.py` 是传输无关的公共 API 门面 + 错误信封（§14、09）。

## 执行沙箱（§10）

策略是处理不可信数据的不可信代码，所以生产路径**不在 Backend 或 Evolution 控制面进程里执行它**：

- `ContainerHost` 只在有排队作业时创建一次性沙箱；`worker_id` 属于沙箱，令牌由
  `JobTokenIssuer` 按 worker 签发。注入的环境变量只有 `EVOLUTION_JOB_ENDPOINT` /
  `_JOB_TOKEN` / `_WORKER_ID` 三个（`ALLOWED_ENV_KEYS`），不挂载任何密钥。
- 镜像必须按 digest 固定（`…@sha256:…`），CPU/内存来自规格；出网白名单只有作业
  协议端点。
- **作业归属在沙箱内**：容器里的 `Worker` 经 `JobChannel` 自行认领作业、心跳、
  提交候选并完成/失败；宿主只做生命周期监管——作业与队列都排空即返回，且
  **确认销毁后才重派发开放作业**。销毁失败向上传播并记录失败；
  API 不可用等情况下仍可能残留资源，必须由控制器/运维核查清理，不宣称绝对无泄漏。
- **崩溃恢复（§7.2）**：worker 退出时若仍留下开放作业，宿主把它当作租约丢失，
  为同一运行排队 `attempt + 1` 的新作业（不是取消运行）；`exec` 超时/异常同理，
  由 `EvolutionRunner` 每轮的 `expire_leases()` 回收。没有剩余尝试时运行以
  `max_attempts` 失败。
- `JobChannel` 与 `Worker` 都只依赖 `WorkerTransport` 协议，HTTP 适配器只需把
  通道方法映射成路由。
- `rootfs_readonly` 在规格里声明，但 Backend 平台的 `SandboxRuntimeClient.create_sandbox` 没有
  逐沙箱开关，只读根文件系统依赖镜像/模板策略（ARCA 侧）保证。

### 内置手动 Run 的操作人恢复

启用内置数据通道时，持久 Run 与触发信息一起记录经 Backend 验证的操作人。
作业取文件、取会话、交候选和待验证恢复都从 Run 设置作用域，而不是从 Worker 请求、
浏览器 Cookie 或 Bot owner 推断操作人。旧 Run 无此字段，保留原平台委托；不会回填，
定时/事件触发也不沿用某个历史操作人。Backend 对每个回调重新检查权限。

### 候选提交的持久化恢复

- 首次提交先预留候选和稳定的 `seq`，不把空修订占位当成成功。
- Registry 已提交但返回失败时，重试依靠 Registry 的同 run/同 patch 幂等语义恢复；
  运行库已有修订则跳过再次 commit。修订和 `candidate_count` 在同一 SQLite 事务写入，
  数量只统计已有修订的候选，不用最大序号替代，也不覆盖运行的状态或累计费用。
- Registry、账本以及已配置的后续调用全部成功后，才持久化 `submission_complete`。
  异常向上传播；后续重试可补齐账本。此标志不是评测完成标志。
- 旧运行库启动时自动补充 `submission_complete` 列，旧记录默认为未完成；只有再次提交
  同一候选时才执行补齐，不会后台扫描或自动重启已经结束的运行。
- 实验条目与首次 `submitted` 事件同事务提交，事件写入失败不会留下可被误判为成功的条目。


## Ownership and verification

- Independent composition: `avernet_evolution.composition`; Worker HTTP:
  `avernet_evolution.adapters.jobs`; Worker entrypoint:
  `python -m avernet_evolution.plugins.entrypoint`.
- Without a 07 verifier, candidates remain pending; `completed` only means the
  strategy finished, not quality improvement, publication or promotion.
- Neutral regressions: `apps/evolution/tests/unit/evolution_run/` and
  `apps/evolution/tests/contracts/`; restart coverage: `tests/test_cli_restart.py`.
- Backend retains only platform integration and explicitly invoked acceptance
  assembly. See its [acceptance guide](../../../../../backend/src/agentclaw/community/di/evolution_acceptance/README.md) for Registry/Engine smoke
  prerequisites and limitations, not production startup instructions.

## Frozen mutation scope

A new strategy version may declare `mutation_capability`. Its Binding can select
Persona-only, Local Skill-only, or both with `mutation_scope`, for example:

```json
{
  "mutation_scope": [
    {"gene": "persona", "include": ["SOUL.md"], "operations": ["update"]},
    {"gene": "skills", "origin": "local", "include": "all", "exclude": ["frozen-helper"], "operations": ["update"]}
  ]
}
```

This is a fragment of a Binding; strategy, profile, trigger and budget are still
required. Selectors contain raw entry names, not gene paths. Whole-package
add/remove, SkillSet, Center and repo Skill mutation are not in this contract.
`allowed_genes` may be omitted for a scope-only Binding; when nonempty it remains
an additional ceiling. A legacy registration without a capability cannot accept
an explicit selector. Its old Bindings/Runs continue using path-only checks.

At submit, resolve capability ∩ Binding ∩ existing parent inventory/ownership ∩
parent/platform mutable policy, minus pins. An empty result refuses before budget
reservation or enqueue. The Run stores concrete `targets`, `pins` and
`operations: ["update"]`; its `allowed_genes` mirrors those targets. Later Binding
edits, new Skills or a moved `active` cannot expand that Run. API/claim/worker and
restart preserve the same frozen scope. SQLite startup adds a nullable column;
old rows remain `NULL` and are neither reinterpreted nor backfilled.

Submission checks every op/member before candidate reservation. Registry repeats
policy/scope/parent ownership checks before writing any content or revision.
The built-in Backend callback binds the frozen scope digest into its signed
DataProof; dropping or replacing it refuses with 403, even if the request body
itself has a valid fingerprint. This adds no Worker-held signing credentials.

Evidence: `tests/unit/evolution_run/test_mutation_scope.py`,
`tests/unit/test_mutation_scope.py`, `tests/test_scope_identity.py`, and the
[Backend cross-service matrix](../../../../../backend/tests/community/adapters/http/openapi_v1/test_evolution_mutation_scope.py).
These use synthetic data and do not establish deployment or Bot quality.

## Protected final strategy output

`GET /openapi/v1/bots/{bot_id}/evolution/runs/{run_id}/strategy-output` returns
`{output: StrategyOutput | null}` with schema owned by
[`evolution_strategy/output.py`](../evolution_strategy/output.py). It shares Bot
read authorization with Run details and verifies Run ownership **before** reading
artifact bytes. It reads only the fixed output name for the Run's current attempt,
never arbitrary artifacts or a previous attempt's proposal. No DB migration or
new Worker capability is needed; existing durable `artifacts.put` is used.

The response is `Cache-Control: no-store`. Output content is separate from
metadata-only `/evidence` and event logs. `null` means no output was persisted for
this attempt, **not** that the model chose not to change anything. Historical
outputs cannot be reconstructed. Corrupt stored output produces a safe read error,
not an empty proposal or an error echoing the private artifact.
