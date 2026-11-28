# Explicit RSI platform acceptance assembly

This directory is **not installed by Backend startup**. It retains only the
platform Registry/Engine wiring required by explicit integration tests and the
separate 01–06 smoke CLI; it is not a second runtime implementation.

The obsolete `runtime.py` composite builder and Backend `core/evolution_*`
domains have been removed. Full lifecycle tests use the production
`avernet_evolution.composition.compose` directly, including verifier persistence
and gate/review behavior. Review alone never moves the active baseline;
manual authorization and Apply remain separate production operations.
- `jobs_module.py`, `runtime_module.py`: explicit Injector assembly only. Their
  process-random keys, `:memory:` default and permissive governance/kill-switch
  fallbacks are **not production configuration**. Even setting legacy
  `EVOLUTION_CONSUMER_ENABLED` does not install them in Backend startup.
- `../evolution_smoke.py`, `../evolution_smoke_sandbox.py`: platform-dependent
  acceptance composition; no production activation or automatic baseline update.

Production lifecycle/configuration belongs to
[apps/evolution](../../../../../../evolution/README.md). Platform facts are still
resolved through Backend contracts. CLI owner selectors are not a user login;
provider credentials must already be valid. Never use a chat-supplied user ID to
authenticate or impersonate someone.

The following explicit procedure can write candidate records and sensitive
snapshots. It requires separate environment authorization; it is **not** an
instruction to deploy as part of source refactoring. The retired Backend Worker
Dockerfile must not be used; the independent image's default is the controller,
so the Worker command must be explicitly selected (see platform adapter guide).

### 可重复的跨进程验收

在仓库根目录运行下面的测试，不需要真实 Bot、模型密钥或 simulator；需要允许监听
本机回环端口。它使用真实 Registry/持久化实现与共享 DI，而不是伪造提交成功：

```bash
DEPLOY_PROFILE=test apps/backend/.venv/bin/python -m pytest \
  apps/backend/tests/community/core/bot_genome/test_evolution_run_integration.py \
  -q -p no:warnings
```

`di/evolution_smoke.py:run_smoke` 也可接入部署的真实 `GenomeRegistryProtocol`。
独立启动脚本位于 `apps/backend/scripts/evolution_smoke.py`，只在新进程构造部署 DI；
不会在正在服务的 Backend 内重建 injector。从已配置好 Registry/数据库和 Bot 读取能力
的 **Backend 工作目录**运行，例如：

```bash
DEPLOY_PROFILE=singlebox .venv/bin/python scripts/evolution_smoke.py \
  --bot-id '<disposable-bot-id>' --owner-id '<owner-id>' \
  --confirm-test-bot '<disposable-bot-id>' \
  --workdir '/tmp/evolution-01-06-unique-run'
```

前置条件与副作用必须明确：

- 仅允许 `test` / `singlebox` / `local-k8s` profile，并校验真实 Bot 的 owner；
  `--confirm-test-bot` 必须与 Bot ID 相同，只对可丢弃测试 Bot 使用。
- Registry 使用的五张 `ac_genome_*` 表必须已部署；更新 Backend 镜像不等于升级数据库。
  表缺失属于 schema 前置条件失败，应先按下面的独立准备说明处理，不绕过 Registry 校验。
- 必须已存在 `active` 基线且当前配置无漂移。脚本**不建立或移动基线**；缺失时在写入前失败。
  此阶段不要为凑通流程放宽 Registry/Binding 的 `active` 约束。
- Bot 引擎须被 identity-hardening 策略支持，允许修改 persona，且 SOUL.md（或回退的 CLAUDE.md / AGENTS.md）中至少缺一条
  该参考策略会补入的 core truth；否则无候选或绑定校验失败，不算验收通过。
- 输出目录必须全新；使用独立文件型运行库/账本，有限预算、手工触发、无验证器/晋升器。
  默认 02 经验仍使用显式 Bot 作用域夹具；传 `--session-id` 才启用下面的 Engine
  快照接入。conformance 保持注册后的 `pending`，不伪造为已通过。
- Worker 是独立本机进程，环境仅传运行必需项和一次性作业令牌，不继承 Backend 的
  环境凭据；这不是容器、文件系统隔离或出网限制的证明。
- 读取真实配置产生的 capture 及 candidate 记录会保留在 Registry；不 apply、不晋升，
  前后检查配置和完整 `active/previous` 引用未变。成功写出 `report.json`，包含 run、
  candidate、revision、pending 判定及检查结果，不包含 persona 正文或令牌。
  运行库/账本可能含受限 Bot 内容，**不要提交输出目录**。

Simulator 必须使用与待验收代码匹配的构建版本，并具备以上测试 Bot 条件。源码改动或
本机 smoke 通过不代表现有 simulator 已升级；容器派发及网络隔离需要另行验收。

#### 独立准备：Genome schema 与初始基线

先检查数据库是否具备 `ac_genome_revision`、`ac_genome_ref`、`ac_genome_ref_event`、
`ac_genome_patch`、`ac_genome_promotion`。模型由
[`bot_genome/repository/models.py`](../../core/bot_genome/repository/models.py) 定义；现有
[`bot_genome/sql/2026_10_19_bot_genome.sql`](../../core/bot_genome/sql/2026_10_19_bot_genome.sql)
包含 OceanBase 专用语法，不能作为通用 MariaDB 建表脚本直接执行。

对已明确授权的本地 simulator，若仅缺这些表，可在**独立的部署准备进程**中，使用部署
DI 提供的数据库连接，按上述五个 ORM 模型逐表调用
`model.__table__.create(bind=connection, checkfirst=True)`。只补缺表，不执行全量
`metadata.create_all`、清库或重置，也不把这段逻辑放进 smoke。`checkfirst` 不会迁移已有表；
已有 schema 不兼容时应停止并走正常迁移流程，建表异常必须向上传播。

schema 就绪后，若专用测试 Bot 尚无 `active`，独立捕获真实当前配置并经 Registry 的
compare-and-swap 建立初始引用，记录测试准备的 actor/reason；已有 `active` 则保留并检查
漂移，不覆盖以凑通验收。这是显式测试准备，不是候选晋升或配置 apply。
smoke 只验证运行前后的配置及保护引用不变，不负责撤销准备产生的初始基线。

### Simulator 的真实会话快照（02 最小接入）

只验证 02 到 01–06 的数据通路，不启用 07–09。入口额外传入会话 ID：

```bash
DEPLOY_PROFILE=local-k8s .venv/bin/python scripts/evolution_smoke.py \
  --bot-id '<disposable-bot-id>' --owner-id '<owner-id>' \
  --confirm-test-bot '<disposable-bot-id>' \
  --session-id '<real-engine-session-id>' --include-draft-experience \
  --workdir '/tmp/evolution-02-unique-run'
```

这里的 `--include-draft-experience` 明确选择 draft runtime 并允许查询其测试片段；
不传该参数则选择 service Bot 的 online runtime。不会把 draft 伪装成 service。
运行前先检查已有 `active`，再读取/持久化会话。没有基线时，先通过独立的、显式授权
的测试准备建立基线，**不要让 smoke 自动设置 `active`**。

步骤与验收点：

1. 在正常已认证账号下新建专用测试 Bot，等待创建/Manifest apply 与 runtime 就绪。
   使用依赖真实 Passport 的部署时，任意虚构的本地登录名不代表可申请 Bot 身份；
   `OWNER_USER_NOT_FOUND` 是账号前置条件失败，不要关闭鉴权或替换全局身份实现。
2. 通过产品 Session/Chat 接口在该 Bot 的 draft 中创建一条真实会话，完成至少一个
   合成 user/assistant 往返。不要写数据库或塞造假的 Engine history 来替代。
3. 完成上面的独立基线准备，确认当前配置无漂移，记录明确的 Engine session ID。
4. 按 simulator 自身 runbook，用待验收 Avernet 工作区重建 **Backend** 并滚动更新，
   比对 Pod `imageID` 与本地镜像；不要全量重启或清库。镜像未携带 `scripts/` 时，
   可在 Avernet 根目录把**启动脚本**经 stdin 交给 Backend 的 Python，业务实现仍
   必须来自新镜像，不热拷贝 package：

   ```bash
   kubectl -n simulator exec -i deployment/backend -c backend -- \
     /home/admin/run/bin/python - \
     --bot-id '<disposable-bot-id>' --owner-id '<owner-id>' \
     --confirm-test-bot '<disposable-bot-id>' \
     --session-id '<real-engine-session-id>' --include-draft-experience \
     --workdir '/tmp/evolution-02-unique-run' \
     < apps/backend/scripts/evolution_smoke.py
   ```

5. 脚本经 Engine relay 读取会话，脱敏后保存 `experience.db`；策略通过独立 Worker 的
   Job HTTP 端口查询同一 Bot 的片段、提交候选。`run/report.json` 必须证明候选修订
   已落 Registry，账本里的 `episode:ep_…` 与真实摄取片段精确一致，候选 `pending`，
   配置与完整 `active/previous` 引用均未改变。任何前置条件或校验失败都不算通过。

参考 identity-hardening 策略会读取经验并引用 episode，但变更是补入预定义 core truths，
**不是从会话语义学习、模型质量提升或评测通过的证明**。provider 的已知限制见
[平台适配说明](../../plugins/evolution/README.md)：快照不是完整的
`session-export/v2`，未知历史 revision 保留 null，反馈未支持。仅显式 acceptance
组合启用，不改变生产 DI 或 ADR 0021 的最终服务归属。

### 一次性 Kubernetes Worker 验收（显式开启）

默认 smoke 仍是独立本机进程。要验证真实 Pod 的调度、Job HTTP 认领、提交与销毁，
在上面的 CLI **同时**加入以下六项；平台实现须来自匹配的 Backend 镜像，Worker 实现须来自独立 Evolution 镜像：

```bash
--worker-image '<worker-image>@sha256:<digest>' \
--sandbox-kube-api 'https://<cluster-api>' \
--sandbox-namespace '<dedicated-acceptance-namespace>' \
--sandbox-token-file '/tmp/rsi-controller/token' \
--sandbox-ca-file '<trusted-cluster-ca-file>' \
--job-bind-ip '<routable-backend-pod-ip>'
```

- 在独立 namespace 使用短期、namespace 限定的控制器凭据：仅 pods 的 create/get/list/delete
  与 networkpolicies 的 create/get/delete。不改 Backend 的 ServiceAccount，不给 Worker
  挂载控制器令牌；凭据文件必须 `0600`，验收后撤销并删除。不要传生产集群凭据。
- 独立 Worker 构建入口见 [平台适配说明](../../plugins/evolution/README.md)；不继承 Backend 镜像或配置。
  `KubernetesJobWorker.create` 安装策略后创建固定入口 Pod，`exec` **等待此 Pod 退出**，
  不是远程 shell。其职责仅为 acceptance，不替换生产 SandboxRuntimeClient。
- Pod 非 root、只读 rootfs、禁提权、drop ALL、RuntimeDefault、无 SA token、仅限量
  `/tmp`；镜像按 digest 固定。入口在认领前实际检查 UID、rootfs 写入失败与无 SA token，
  输出 `worker.isolation.checked` JSON，可用独立 `kubectl logs -f <worker-pod>` 观察。
- NetworkPolicy 仅放行该次 Job 端点 IP/端口，拒绝入站及其他出站（含 DNS）。必须在
  **具备策略执行能力的 CNI** 上验收：对同一已知监听端点做「无策略可达／受策略不可达」
  对照，再检查合法 Job 端口可达。存在 policy 对象不等于网络隔离生效。
- 运行目录额外保存 `sandbox-evidence.json`（Pod UID、imageID、退出码、销毁确认等），
  必须与独立观察的 Pod/日志交叉核对。只有 Pod 创建或 Pending 不算 Worker 执行通过。
- 必须先确认真实 Bot 当前配置与 `active` 一致。若返回 `cancelled/out_of_band_change`，
  `run-id.txt` 与 `run.cancelled` 仍可检查，但尚未创建 Worker。首次会话也可能更改引导文件；
  不自动移动基线、恢复配置或跳过漂移检查，重新建立测试基线需独立授权。

### 按 run_id 观察执行，而不是相信一行“成功”

运行库的 `evolution_event` 表是持久化的**元数据执行日志**。从仓库根目录读取：

```bash
apps/evolution/.venv/bin/python apps/evolution/scripts/evolution_events.py \
  --database '/tmp/<acceptance-dir>/run/runs.db' \
  --run-id '<run-id>' --follow
```

在 Backend Pod 中用同一脚本与容器内的绝对数据库路径读取（或备份后本机读取）；脚本
以 SQLite `mode=ro` 打开，不启动 DI、不登录，也不创建数据库。`--after <event-id>`
支持断点接续，去掉 `--follow` 导出 JSONL。实际路径以该次 `run-id.txt` 所在目录为准；
没有 session 时运行库直接位于输出目录，带 session 时位于其 `run/` 子目录。

正常序列含 `run.queued`、`sandbox.created`、`job.claimed`、
`worker.strategy.started`、`experience.query.completed`、`candidate.persisted`、
`ledger.submission.persisted`、`worker.strategy.finished`、`job.completed`、
`sandbox.exited` 和 `sandbox.destroyed`；失败可观察 `sandbox.execution_failed`、
`sandbox.destroy_failed`、`job.failed/requeued` 或漂移取消。心跳在运行足够长时出现，
并非每个短作业必有。宿主事件仅通过 Worker **实际认领**来关联 run，不猜测队首作业；
未认领就崩溃的 Pod 只能结合整个事件表与 sandbox evidence 核查。

- `authority=platform` 表示平台端记录的动作/结果，不代表外部事实已独立验证；
  `worker-reported` 的策略起止/日志仅是 Worker 声明。任意日志正文不保存，只存 SHA256。
  经验事件只含 episode ID/count，不打印会话正文、密钥、完整模型响应。
- `experience.snapshot.persisted` 是摄取结果；`experience.query.completed` 是此作业
  实际查询结果。结合账本里的 `episode:<id>` 才能核对数据通路，不能据此推断语义学习。
- **不是原子审计/恰好一次事件流**：目前状态提交后再写事件，事件失败会报错，但先前
  状态可能已提交；重试也可能重复记录。日志缺失/不完整不算成功，须同时检查 run、
  candidate、ledger、sandbox evidence。租约定时过期等所有转移尚未逐项写入此日志。
- `kubectl exec` 的 stdout 不会自动进入 Deployment 日志；用此只读 monitor 观察持久化
  事件，Worker 的隔离自检看 Worker Pod 日志。`report.json` 只在完整断言通过后生成。
  输出可能含受限内容，保存在私有临时目录，不提交 Git；Pod `/tmp` 也不跨 rollout 保留。

当前 Bot RSI UI 走 Backend 薄入口和独立 Evolution 服务；本页 CLI 是另一条显式验收路径，
不代表真实页面已验收。completed/pending 仍不代表评分通过或已晋升。
