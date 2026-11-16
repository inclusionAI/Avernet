# evolution_run — 进化运行服务（06-evolution-run）

Bot 进化控制面里执行策略的那部分：绑定、触发器、运行状态机、租约作业、长时操作、
沙箱工作区、预算与紧急停止。实现
[06-evolution-run.zh-CN.md](../../../../docs/bot-evolution/06-evolution-run.zh-CN.md)
的领域模型（§2）、生命周期（§5–§8）、服务接口（§13）与存储表（§12）。

本模块**传输无关**：不 import 任何 HTTP 框架，不绑定任何厂商容器 SDK；HTTP 路由与
ARCA 沙箱分别在 `adapters/http/` 与 `plugins/evolution/` 里适配。

## 组成

| 文件 | 对应文档 | 内容 |
| --- | --- | --- |
| `enums.py` | §2.4–§2.6、§11.1 | `RunStatus`、`EndReason`、`OperationKind`、`ChargeSource`、`RiskCeiling` |
| `budget.py` | §2.1 | `Budget` / `BudgetUsage`，逐维度比较与耗尽判定 |
| `bindings.py` | §2.2、§3、§4 | `Binding`、`BotBindings`、触发器与五字段 cron 校验 |
| `records.py` | §2.4–§2.8 | `Run`、`Job`、`Operation`、`Workspace`、`RunCandidate` |
| `ports.py` | §13、01–08 | 本服务依赖的端口（Genome、StrategyRegistry、Verifier、Promotion、Ledger、Experience、KillSwitches、Governance） |
| `store.py` | §12 | SQLite 持久化：绑定、运行、幂等键、作业、操作、工作区、候选、费用、触发记录 |
| `bindings_service.py` | §3.2、§13 | 八项绑定检查、PUT/GET 的 ETag 语义 |
| `run_service.py` | §5、§6、§13 | 提交（漂移检查、冻结输入、预留预算）、幂等、查询、取消、候选 |
| `job_service.py` | §7、§13 | 认领、心跳、完成、失败、租约过期重派发、宿主兜底重派发（不校验 fencing token） |
| `operation_service.py` | §8、§13 | 幂等启动、按 id 查询、取消、结束回填与计费 |
| `budget_service.py` | §11、§13 | 预留的上下限检查、计费累计与 `budget_exhausted` |
| `context.py` | §5.1、§9 | 真实 `StrategyContext`：workspace、candidates、models、experience、operations、budget、log、artifacts、cancelled |
| `host.py` | §9 | 进程内宿主：认领租约、心跳续期、执行 `run(ctx)`、映射结束状态 |
| `cron.py` | §4.1 | 五字段 cron 解析与 UTC 分钟匹配（含 DOM/DOW 的“或”语义） |
| `scheduler.py` | §4 | 触发器调度：按 (binding, fire_key) 幂等触发，跳过冻结/漂移/忙碌并留痕 |
| `sandbox.py` | §10 | 沙箱端口与规格：digest 固定镜像、env 白名单、出网仅作业端点、资源与 TTL |
| `container_host.py` | §10 | 容器宿主：无排队作业即空转；否则建沙箱、执行 worker、等待作业排空；worker 退出却留下开放作业 → 按 §7.2 重派发；任何路径必然销毁沙箱 |
| `submission.py` | §5.4 | 候选提交流水线（底线→记录→实验记录→验证），上下文与作业通道共用 |
| `job_protocol.py` | §7 | 作业协议：HMAC 令牌认领、心跳、文件、提交、判定、完成/失败（传输无关） |
| `job_wire.py` | §7 | 与框架无关的作业协议负载编码（文件字节 base64），供路由与 worker 客户端共用 |
| `worker.py` | §7、§10 | 容器内 worker：经作业协议认领作业，用本地工作区执行策略并回写 |
| `runner.py` | §10 | 执行循环：`Lifecycle` 参与者，每轮「回收过期租约 → `scheduler.tick()` → `host.run_once()`」，消费进化作业队列（默认关闭，需 `EVOLUTION_CONSUMER_ENABLED=1`） |
| `genome_registry_port.py` | §10 | 生产基因组适配：`GenomeRegistryProtocol` → 作业通道所需的快照与修订解析 |

`api.py` 是传输无关的公共 API 门面 + 错误信封（§14、09）。

`plugins/evolution/` 是运行时侧适配：

- `sandbox_worker.py` — 把 `JobWorkerSandbox` 接到平台 `SandboxRuntimeClient`（ARCA/BaaS）；
  `create_sandbox` / `exec_command` / `destroy_sandbox` 经 `asyncio.to_thread` 挪出事件循环，
  core 不 import 厂商 SDK。
- `job_http.py` — worker 镜像侧的 httpx 客户端，实现 `WorkerTransport`，对平台
  `/openapi/v1/evolution/jobs` 发起请求。
- `entrypoint.py` — 镜像入口：`python -m agentclaw.community.plugins.evolution.entrypoint`。
  策略表由镜像经 `EVOLUTION_WORKER_STRATEGIES`(`ref=module:attr` 逗号列表) 声明；
  只读三个白名单变量 (`EVOLUTION_JOB_ENDPOINT` / `_JOB_TOKEN` / `_WORKER_ID`)，运行到队列排空。

## 执行沙箱（§10）

策略是处理不可信数据的不可信代码，所以生产路径**不在 backend 进程里执行它**：

- `ContainerHost` 只在有排队作业时创建一次性沙箱；`worker_id` 属于沙箱，令牌由
  `JobTokenIssuer` 按 worker 签发。注入的环境变量只有 `EVOLUTION_JOB_ENDPOINT` /
  `_JOB_TOKEN` / `_WORKER_ID` 三个（`ALLOWED_ENV_KEYS`），不挂载任何密钥。
- 镜像必须按 digest 固定（`…@sha256:…`），CPU/内存来自规格；出网白名单只有作业
  协议端点。
- **作业归属在沙箱内**：容器里的 `Worker` 经 `JobChannel` 自行认领作业、心跳、
  提交候选并完成/失败；宿主只做生命周期监管——作业与队列都排空即返回，且
  **任何路径都不留下沙箱**。
- **崩溃恢复（§7.2）**：worker 退出时若仍留下开放作业，宿主把它当作租约丢失，
  为同一运行排队 `attempt + 1` 的新作业（不是取消运行）；`exec` 超时/异常同理，
  由 `EvolutionRunner` 每轮的 `expire_leases()` 回收。没有剩余尝试时运行以
  `max_attempts` 失败。
- `JobChannel` 与 `Worker` 都只依赖 `WorkerTransport` 协议，HTTP 适配器只需把
  通道方法映射成路由。
- `rootfs_readonly` 在规格里声明，但 `SandboxRuntimeClient.create_sandbox` 没有
  逐沙箱开关，只读根文件系统依赖镜像/模板策略（ARCA 侧）保证。

## 当前续开发记录

01–06 的核心运行模型、Job Protocol、策略执行和候选提交流程已落地，并有针对性单测及一次有限真实 Bot 冒烟。当前冒烟不证明真实候选落库、drift、apply 或 MCP/CLI 能力；继续开发前先看 `docs/bot-evolution/README.zh-CN.md` 的状态说明。

## 使用方式

### 1. 本地 / 测试：构造运行时并直接驱动

```python
from agentclaw.community.core.evolution_run import (
    build_evolution_runtime, ContainerHost, EvolutionRunner,
)

runtime = build_evolution_runtime(
    genome=...,
    registry=...,
    governance=...,
    kills=...,
    strategies={"acme/identity-hardening@1.0.0": SomeStrategy},
    profiles={"default@1": VerificationProfile()},
    cases_for=...,
    execute=...,
    grade=...,
    experience=...,
    heartbeat_s=3600,
    sandbox_worker=...,          # 你的 JobWorkerSandbox 实现（测试可用 FakeSandbox）
    strategy_images=lambda _run: "registry...@sha256:...",
)

# 单次人工驱动（测试常用）
assert await runtime.host.run_once("worker-1") is True

# 或用执行循环自驱动（生产接近的形态，待 DI 全装配后接入应用生命周期）
runner = EvolutionRunner(host=runtime.host, scheduler=runtime.scheduler, poll_interval_s=15.0)
await runner.startup()
# ... 应用运行中 ...
await runner.shutdown()
```

### 2. HTTP 层（已落地）

主应用已挂载 `adapters/http/evolution_jobs/router.py`（前缀
`/openapi/v1/evolution/jobs`），DI 解析器每请求解析一次 `JobChannel`；非法令牌映射
为 401。worker 镜像通过 `plugins/evolution/job_http.py:HttpWorkerTransport` 与之通信。

### 3. 生产自动消费（DI 已接上，默认关闭）

`di/modules/evolution_runtime_module.py` 把「调度 + 沙箱派发」接进应用生命周期：
`TriggerScheduler` → `ContainerHost` → `EvolutionRunner`（`Lifecycle` 参与者，
startup 起后台循环、shutdown 取消）。

**后台消费默认关闭**，不受影响，也不会给已有部署引入后台任务。要开启：

| 环境变量 | 含义 | 缺省 |
| --- | --- | --- |
| `EVOLUTION_CONSUMER_ENABLED` | `1` = 起后台消费循环 | 关闭 |
| `EVOLUTION_WORKER_IMAGE` | worker 镜像，必须 `…@sha256:…` 固定 | 无（派发记配置错误） |
| `EVOLUTION_STRATEGY_REFS` | 本进程负责派发的策略 `ref` 逗号列表 | 空（不派发作业） |
| `EVOLUTION_JOB_ENDPOINT` | worker 回连的作业协议端点 | 本机 8000 |
| `EVOLUTION_POLL_INTERVAL_S` | 空闲轮询间隔（秒） | `15` |
| `EVOLUTION_MAX_JOBS_PER_TICK` | 单轮最多派发几个作业 | `1` |
| `EVOLUTION_DB_PATH` | 进化存储 SQLite 路径 | `:memory:` |
| `EVOLUTION_WORKER_TEMPLATE_ID` | 沙箱模板 id（ARCA） | 空 |

worker 镜像侧另需 `EVOLUTION_WORKER_STRATEGIES`（`ref=module:attr` 逗号列表），
声明该镜像服务哪些策略；它是镜像的构建期声明，不进入沙箱 env 白名单。

最小开启方式（本地 simulator）：

```bash
export EVOLUTION_CONSUMER_ENABLED=1
export EVOLUTION_WORKER_IMAGE='registry.test/evolution/worker@sha256:<digest>'
export EVOLUTION_STRATEGY_REFS='acme/identity-hardening@1.0.0'
export EVOLUTION_JOB_ENDPOINT='http://127.0.0.1:8000/openapi/v1/evolution/jobs'
export EVOLUTION_DB_PATH='/var/tmp/evolution.db'   # 多进程必须共享同一个库
```

## 维护手册

### 加一个新策略

1. 在 `core/evolution_strategy/strategies/` 新增策略类/工厂，遵循
   `core/evolution_strategy/protocols.py`。
2. 在 `build_evolution_runtime(...)` 的 `strategies` 参数里按 `ref` 注册它（进程内）；
   走 job-worker 时，把它加进 worker 镜像的 `EVOLUTION_WORKER_STRATEGIES`
   （`ref=module:attr`），并让 `EVOLUTION_STRATEGY_REFS` 包含同一个 `ref`。
3. 在 `catalog.py`（如需要新能力条目）或绑定的 `allowed_genes` 里确认它被允许
   触碰的基因；策略补丁的 `base` 必须等于运行的 `parent_revision`，否则提交被拒绝。

### 改沙箱行为

- 改 `core/evolution_run/sandbox.py` 的 `JobWorkerSpec` / `job_worker_spec`（规格）。
- 改 `plugins/evolution/sandbox_worker.py` 的厂商适配（ARCA `create_sandbox` / `exec_command` / `destroy_sandbox`）。
- 测试用 `tests/community/unit/evolution_run/test_sandbox.py` 里的 `FakeSandbox`。

### 改调度频率 / 消费方式

- `EvolutionRunner.poll_interval_s`（默认 15s）与 `max_jobs_per_tick`（默认 1）是
  主控旋钮；不再消费时按此间隔休眠，`shutdown()` 会立即打断休眠。
- 单次 tick 先 `jobs.expire_leases()`（回收崩溃 worker 的租约，运行回到 queued），
  再 `scheduler.tick()`（cron → queued），最后最多 `max_jobs_per_tick` 次
  `host.run_once()`（queued → 沙箱）。

### 快速验证

```bash
cd apps/backend
uv run pytest tests/community/unit/evolution_run -q -p no:warnings \
  --confcutdir=tests/community/unit/evolution_run
uv run pytest tests/community/architecture -q -p no:warnings
```

## 当前边界与待办

核心 + 测试 + ASGI 端到端 + DI 装配已完备：

- **DI 闭环已接上**：`di/modules/evolution_runtime_module.py` 绑定
  `EvolutionRunner`（`Lifecycle` 参与者）、`ContainerHost`、`TriggerScheduler`、
  `RunService`、`BindingsService`、`BudgetService`、`OperationService`，并按需退化为
  三个端口（策略注册表 / 治理 / 停止开关）的默认实现，生产可覆盖。
- **后台消费默认关闭**，设 `EVOLUTION_CONSUMER_ENABLED=1` 开启，避免给已有部署
  带来后台任务副作用。
- **§7 崩溃恢复已闭环**：租约过期 → 运行回 `queued` 并排 `attempt + 1` 作业
  （`EvolutionRunner` 每轮先回收）；可重试失败会插入新作业行，因此重新排队后真的
  能被再次认领；宿主发现 worker 退出却留下开放作业时按 §7.2 重派发而不是取消。
- **尚未完备**：worker 镜像部署（入口与策略声明已就绪：
  `python -m …plugins.evolution.entrypoint` + `EVOLUTION_WORKER_STRATEGIES`，
  等镜像构建与 digest 固定）、07 验证的生产执行器/评分器（ClawBench、执行器留给
  部署层）、simulator/k8s 端到端验通（需真实 ARCA 沙箱与跨进程网络）、以及
  `EVOLUTION_DB_PATH` 生产下必须指向共享库（`multi-process` 下 `:memory:`
  认领对不上）。

## 与文档的取舍

- 策略返回的 `RunSummary` 采用 03 的形状 `{summary, metrics}`，存进 `Run.summary`；
  06 §2.8 的 `{rounds, notes, extra}` 是同一事实的另一种投影，未另立类型。
- `evolution_run` 记录同时保存 `entity_id` / `engine_type` / `bot_type`，因为 `BotRef`
  需要它们参与 §3.2 的引擎检查。

## 未包含（路线后续）

- 09 的 HTTP 交付层：`api.py` 已备好门面，需在 `api/` 增 Protocol、在
  `adapters/http/evolution/` 增路由并接 DI（作业通道 `/openapi/v1/evolution/jobs` 已先行落地）。
- 调度器的独立进程部署（当前是可被 tick 调用的纯对象）。
- 07 的真实执行器/评分器插件与 holdout 定期审计作业：本地实现由组合根注入
  executor/grader，判定规则与治理不变量已完整。
