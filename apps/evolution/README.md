# Evolution — 独立的 RSI 控制面（01–06）

实现 [ADR 0021](../../docs/adr/0021-evolution-control-plane-in-apps-evolution.zh-CN.md)。
Canonical Python 包为 `avernet_evolution`，不依赖 Backend 的 Python 包。
历史规格仍在 [bot-evolution](../backend/docs/bot-evolution/README.zh-CN.md)；
本次迁移记录和验收边界见 [migration report](../../docs/specs/2026-11-17-evolution-service-migration/report.md)。

**当前状态（2026-11-18）：独立控制面、持久化恢复和 Bot RSI UI 已实现；旧版 Simulator 隔离 Worker 已执行，02脱敏会话快照读取成功，但旧示例候选被 Genome 以 `already_recorded` 拒绝。新版 `identity-hardening@1.1.0` 已按独立镜像部署并通过离线测试，尚未从真实 Bot 前端触发新版 Run；不能宣称新版候选、Ledger 或 01–06 端到端验收成功。**
以上是历史运行记录；本次源码已改为复用 Gateway 登录和 Backend Bot 权限，
不再通过浏览器直连 Evolution 或开发登录桥接。新链路尚未部署，不能据此宣称
远端登录、用户名称显示或真实任务验收已修复。
`local` 是明确的测试夹具，不是真实 Bot、聊天导出、LLM、Agent Swarm 或远端沙箱。
`production` 必须提供经过评审的 Platform 插件，缺失时启动失败，不回退本地夹具。
07–09 见下文「判定、门禁与评审面」：目前只有规则型验证 + 门禁决策 + 评审 API，
**apply 未启用**，`accept` / `needs_review` 都不代表 bot 已经改变。

## Context Boundary

```yaml
purpose: Own Evolution orchestration, strategy SDK, jobs and durable execution evidence independently of Backend.
provides:
  - EvolutionApi
  - EvolutionRunner
  - StrategyContext
  - Platform
  - AccessPort
  - create_app
consumes:
  - GenomePort
  - GovernancePort
  - ExperienceQueryFactory
  - KillSwitchesPort
  - JobWorkerSandbox
internal_dependencies: []
```

### Change impact

策略作者依赖 SDK 和 Worker 协议；前端依赖 Backend 的 Bot 控制 API；
Backend 复用系统登录和 Bot 权限，Evolution 验证内部授权回执；平台提供者实现
Genome、经验及沙箱端口。变更这些边界必须更新兼容性/消费方测试。Backend 原导入
路径暂保留同一模块对象的兼容别名；它们不是第二份 Evolution 实现。

## 归属与执行链路

```text
Bot 聊天「+ → RSI任务」/ API 客户端
  → 同源 Gateway：现有登录 → Backend：现有 principal + Bot 权限
  → Backend 薄适配：权威 BotRef / 实际提交者 + 60秒内部授权回执
  → 独立 Evolution HTTP：验证服务回执及请求范围（不再次登录用户）
  → Binding 校验 / ETag → 提交 Run / 幂等键
  → 冻结 parent、策略版本、参数、预算 → 原子持久化 Run + Job
  → Runner：回收过期租约 → 调度触发器 → 派发
  → local: InProcessHost / production: ContainerHost → 隔离 JobWorker
  → Job claim / fencing / heartbeat → async run(ctx) -> RunSummary
  → Bot 范围内读取 experience、物化工作区、产生 patch、提交 candidate
  → 平台 Genome 保存候选（不移动 active）+ Ledger / 执行证据
  → Run completed / failed / cancelled / budget_exhausted
```

- **Evolution 拥有**：Binding、触发器、Run/Job/Operation、工作区元信息、预算、
  Strategy Registry、SDK、Ledger 及执行证据。现有 Verification 代码仅迁出，独立
  组合根不接入评测/晋升流程。
- **Backend/平台拥有**：Bot 配置、Genome 内容/修订与发布晋升；引擎拥有会话导出、
  实际文件投影和运行环境。不得从新服务导入 `agentclaw.*` 偷接平台内部实现。
- **Agent Swarm**：可作为 `ctx.operations` 的子操作执行提供者；本次没有把
  EvolutionRun 改为 Swarm Task。Swarm workspace 与 Genome workspace 的映射、
  子操作适配、取消级联和遗留沙箱清理仍待专门接入。
- **策略接口不变**：`async run(ctx) -> RunSummary`。SDK 位于
  `avernet_evolution.core.evolution_strategy`；从 `ctx` 访问运行输入、工作区、候选、
  经验、模型、操作、预算、日志与 artifacts。服务安装并注册策略后才可被运行，
  前端不会上传任意 Python 代码。

## 目录

| 位置 | 责任 |
| --- | --- |
| `src/avernet_evolution/core/evolution_run/` | Run/Job、存储、调度、宿主、SDK 上下文、Worker 协议 |
| `src/avernet_evolution/core/evolution_strategy/` | 策略注册、契约校验、现有示例策略 |
| `src/avernet_evolution/core/evolution_ledger/` | 实验记录 |
| `src/avernet_evolution/core/evolution_verify/` | 已有验证模型/实现，当前独立组合根不启用 |
| `src/avernet_evolution/adapters/` | 用户控制 HTTP 与内部 Worker HTTP，分别维护契约 |
| `src/avernet_evolution/plugins/` | Worker HTTP 客户端、独立 worker 入口及现有运行时适配器 |
| `config.py`, `bootstrap.py`, `composition.py`, `plugin_api.py` | 配置、生命周期、显式组合与平台接缝 |
| `local.py` | 严格限定的公开本机夹具 |
| `scripts/evolution_events.py` | 只读持久化事件 JSONL 监控，不启动 Backend/DI |
| `tests/unit/`, `tests/contracts/` | 中立领域回归、Worker HTTP/沙箱端口契约 |
| `deploy/`, `Dockerfile`, `scripts/ci_test.sh` | 部署模板与独立 CI |
| `../../packages/genome-contracts/` | 两服务共享的 Genome DTO、编解码及确定性纯函数 |

## 本机独立启动（无需 Backend）

仓库根目录，Python 3.12 + uv；状态/密钥放 Git 外。以下仅生成**本机夹具凭据**，
不能用于生产，不要复制真实 IAM Cookie 到文件或命令历史。

```bash
uv sync --project apps/evolution --frozen
umask 077
state=$(mktemp -d "${TMPDIR:-/tmp}/avernet-evolution-local.XXXXXX")
python3 -c 'import secrets,sys; from pathlib import Path; p=Path(sys.argv[1]); (p/"worker-key").write_text(secrets.token_hex(32)); (p/"local-token").write_text(secrets.token_hex(32))' "$state"
export EVOLUTION_DATABASE="$state/control.db"
export EVOLUTION_SIGNING_KEY_FILE="$state/worker-key"
export EVOLUTION_LOCAL_TOKEN_FILE="$state/local-token"
export EVOLUTION_PROFILE=local
export EVOLUTION_HOST=127.0.0.1
export EVOLUTION_PORT=8091
uv run --project apps/evolution --frozen evolution serve
```

保留同一 `state` 和签名密钥，重启同一命令即可恢复。不要误删临时目录；长期使用需
改用受保护的持久路径。可用 `GET /healthz` 检查启动完成，不能据此断言队列有进展。
本机夹具 Bot=`local-bot`，归属选择器=`local-user`；HTTP 使用 `Authorization:
Bearer <local-token 文件内容>`。夹具策略 `local/identity-hardening@1.0.0`，
只修改 persona 内容、只保存候选、不发布；经验为合成样本，不是用户真实02数据。
自动化同等操作可运行 `tests/test_service.py`；真实 CLI 强杀重启见
`tests/test_cli_restart.py`。

### 配置（环境变量前缀 `EVOLUTION_`）

未知配置项（含拼错的 `EVOLUTION_` 环境变量）启动即拒绝，不能靠拼写错误静默使用默认值。

| 名称 | 默认/要求 |
| --- | --- |
| `PROFILE` | `local`；生产必须 `production` |
| `DATABASE` | 必填，持久化 SQLite 的绝对路径；禁止 `:memory:` |
| `SIGNING_KEY_FILE` | 必填，至少32字节；重启不能静默换钥 |
| `LOCAL_TOKEN_FILE` | local 必填，至少32字符，仅本机夹具使用 |
| `BACKEND_PRINCIPAL_KEY_FILE` | production 必填；部署提供既有平台 principal 签名材料的只读文件，至少32字节；不是 IAM Token，也不是 Worker key |
| `BACKEND_TENANT` | production 必填；必须匹配 Backend 的系统租户，一个库只属于一个租户 |
| `BACKEND_DATA_ORIGIN` | 默认关闭；内置手动 RSI 的 Backend 私有 HTTP(S) origin，由平台工厂传给 `BotHttp`，不经 Gateway |
| `PLATFORM_FACTORY` | production 必填，可信 `module:async_factory`，返回 `Platform` |
| `HOST` / `PORT` | `127.0.0.1` / `8091`；local 只允许回环地址 |
| `CONSUMER` | `true`，控制独立服务自动消费；不是旧 Backend 的 `EVOLUTION_CONSUMER_ENABLED` |
| `POLL_INTERVAL_S` | `1` |
| `LEASE_SECONDS` / `HEARTBEAT_S` | `60` / `10`，心跳须小于租约的一半 |
| `JOB_ENDPOINT` | 默认本机8091；生产必须是 Worker 实际可达的内部地址，包含 `/openapi/v1/evolution/jobs` |

Worker 镜像由 Platform 的每个策略注册版本提供，必须按 digest 固定；隔离宿主按注册版本
分组派发，镜像自身应只声明对应的策略版本。旧版与新版可以共存，不可替换同版本镜像。
Worker 签名材料、控制面 profile/tenant 和策略注册定义/镜像摘要的身份存入数据库，冲突会拒绝启动；
策略修改须提升版本。未记录 control-scope 的历史库首次升级前必须离线核对原租户；
首次写入只能固定部署声明，不能反向证明历史数据属于该租户。不要把 local 夹具库
直接当生产库；已固定的 profile/tenant 不允许通过改环境变量切换。
进程内代码字节本身尚未做内容寻址，不能以元数据校验替代供应链校验。

## Service API（v1，用户控制面）

以 `/openapi/v1/bots/{bot_id}/evolution` 为 Bot 路径前缀。
生产的浏览器入口位于 Backend，复用 Gateway 登录、签名 principal、Bot admission 与
协作者权限；不新增 RSI 登录、Cookie 文件或用户配置。`user_id` 由前端共享客户端
从系统身份提供，Backend 与已验证 principal 比对；`entity_id`（兼容 `owner_id`）
只是资源选择器，**不构成身份凭证**。读接口沿用 MEMBER，保存/取消沿用显式 MEMBER；
启用内置数据通道后，手动启动要求显式 ADMIN（或 Owner），与 Genome 候选写入一致。
仅有 Space 合成的查看/聊天权限不会自动获得操作权限。

Backend 从权威 Bot 解析结果取得稳定 entity 和当前 owner，独立记录实际 actor，
不把 owner 当提交者。内部回执使用既有平台 principal 签名材料、固定 issuer/audience、
60秒有效期，绑定租户、BotRef 及 HTTP 方法/路径/query/body/CAS/幂等键。
Evolution 生产控制面只消费这个授权结果，不消费浏览器 Cookie 或自带的用户身份。
回执是服务间信任，不是另一套用户登录；必须限制内部端口及密钥读取权限。
`AccessPort.authorize` 仅服务显式 local 夹具；production 的 `Platform.access` 可为 None。
其他 provider 仍按 BotRef 隔离，不可复用全局租户查询。
Backend 配置与故障边界见 [Bot RSI 控制入口](../backend/docs/flows/bot-evolution-control.md)。

| 方法 / 后缀 | 输入 → 输出 |
| --- | --- |
| `GET /strategies` | → 已注册策略目录（仍检查此 Bot 权限） |
| `GET /bindings` | → `{bindings, etag}`，响应头 `ETag` |
| `PUT /bindings` | `{bindings: [...]}` + `If-Match: "<etag>"` → 新文档；缺头428、冲突412 |
| `POST /runs` | `{binding_id, params?, budget?}` + `Idempotency-Key` → 202 `{run: run_id}` |
| `GET /runs?page=1&page_size=20` | → `{runs, total}` |
| `GET /runs/{run_id}` | → `{run}` |
| `POST /runs/{run_id}:cancel` | `{reason?}` → 取消结果 |
| `GET /runs/{run_id}/candidates` | → `{candidates}`（本次候选判定 pending） |
| `GET /runs/{run_id}/evidence?after=0` | → `{jobs, events, next_cursor}`，不返回 token |

策略目录使用上述 Bot 路径下的 `/strategies`。独立服务保留旧无 Bot 路径别名以兼容
本机夹具，Backend 不代理此别名。目录列出注册事实，并不保证所有 needs 已有可用运行时 provider。

控制接口信封 `{code: HTTP状态码 * 1000, message, data, request_id}`；
`X-Trace-Id` 与 request_id 对应，异常不会把凭据或 provider 细节回显给客户端。
相同幂等键只能配相同请求，未知提交结果请复用键确认，不能换键重试。
当前历史请求指纹沿用原序列化行为，调用方应保持同次请求参数结构/顺序。

一个手动 Binding（先 GET 取得 ETag，再 PUT 完整列表）：

```json
{
  "id": "identity", "strategy": "local/identity-hardening@1.0.0",
  "trigger": {"manual": true}, "parent": "active",
  "allowed_genes": ["persona"], "verification_profile": "rules-file@1",
  "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
  "params": {}, "auto_promote_ceiling": null
}
```

## 判定、门禁与评审面（07–09）

状态（2026-11-24）：07 有可运行的**规则型**执行器（`plugins/verifiers/rules.py`：读
revision 文件、按用例打分；simulator 只负责选用），08 只做决策，09 的报告/队列/
approve/reject 已可调用。
**apply 仍未启用**：revision 目前只捕获 persona 等部分文件，不是 bot 的完整快照；
`promote()` 显式拒绝 apply；approve 只记录人工决定，不写回 bot 或移动 `active`。

| 方法 / 后缀 | 输入 → 输出 |
| --- | --- |
| `GET /candidates/{candidate_id}` | 候选报告：diff 指针、判定与 splits、门禁、评审状态；判定仍 pending 时 `gate` / `review` 为 `null` |
| `GET /review-queue?status=open&min_tier=T1&page=1&page_size=20` | `{total, items}`；`status` 可取 `open` / `approved` / `rejected` / `superseded` / `all` |
| `POST /candidates/{candidate_id}:approve` | `{reason}` + `Idempotency-Key` → 记录批准；响应 `promotion` 为 `null`，`promotion_note` 说明 apply 未启用 |
| `POST /candidates/{candidate_id}:reject` | `{reason}` + 可选 `Idempotency-Key` → 记录拒绝，条目变为 `rejected` |

错误码沿用 09 §2.1 的信封：未知候选/条目 `404 not_found`；不在队列
（自动晋升条件满足或不可晋升）`409 not_reviewable`；已决策 `409 already_decided`。
同一 Bot 的多个 Run 若历史上保存了同一哈希，只有哈希的候选报告和审批路径返回
`409 candidate_ambiguous`，不会任选一个 Run。可从 Run 的候选和证据列表定位历史记录。
旧应用委托通道的 `/genome/drift` 返回 404 表示进化应用不可见该 Bot
（`409 evolution_app_not_granted`）。内置通道复用操作人的 Bot 权限，不查应用授权；
当 Bot 可见但找不到 `active` 时，保存绑定或启动 Run 返回 `409 no_genome_baseline`。
基线必须通过独立 Genome 流程建立，RSI 不会自动捕获或提升；冷启动走基因组侧的
`POST /openapi/v1/bots/{bot_id}/genome/baseline:initialize`（只记录当前配置，不 apply，ADMIN），前端保存绑定时会自动调用一次。Chat 的 `missing login cookie`
属于独立登录链路，这里不修改。

验证器是插件：
`build_rule_verifier(genome)`（`plugins/verifiers/rules.py`）演示了最小实现——
用例（`cases_for`）、执行器（`execute`，当前只读文件）、评分器（`grade`，标记匹配 +
密钥扫描）。接 LLM judge 或真实 rollout 时替换这几个回调即可，
`compare.py` / `policy.py` / 门禁 / 09 接口都不用改；同时要把 `agreement` 接上，
否则 T2+ 候选的“多评分器集成”要求不会被执行。

数据落在与 Run 同一个 SQLite：`evolution_verdict` 按 `(run_id, candidate_id, profile)` 幂等，
终态永不改写；`evolution_gate_decision` 与 `evolution_review_item` 按 `(run_id, candidate_id)`
隔离。审批状态、`review.decided` 证据和 `evolution_review_request` 的幂等响应同事务提交。
同一 owner/Bot 内，同键同动作、候选及原始 `reason` 返回第一次的响应；复用键但修改请求
返回 `409 idempotency_key_reused`。

门禁规则（08 §4.4）：判定非 `accept`、safety 新失败、回归超容差、超预算 →
`not_promotable`；被容忍的回归、冻结、事件 → `needs_review`；风险等级在绑定
`auto_promote_ceiling` 之内且非 T3 才 `auto_promote`。上限在提交 Run 时从当前 Run 所属的
Binding 冻结，与同一 Bot 的其他 Binding 无关，之后修改配置不影响已提交 Run。
等级在提交时按补丁操作算一次
（persona → T2，resource/memory → T1，`memory.set_mode=replace` → T3，未知 → T2）。

进程内验证流水线不跨重启：启动时会把库里停在 `pending` 的判定重跑一遍
（`LocalVerificationService.resume_pending`）。已经落定但还未写全下游效果的判定，不重跑
评测；它在启动以及启用 consumer 时的轮询中补齐 Ledger、候选状态、门禁/评审单和证据。
若日志出现 `evolution.final_delivery_failed`，按 Run ID 检查 Ledger 和运行库，不要重新提交
候选绕过失败；下游缺写时不会标记为已投递。

### 07–09 旧运行库升级

停止旧实例及写入方，备份 Run 数据库及配对的 Ledger（含尚未 checkpoint 的 SQLite 状态），
再启动新实例。旧 verdict、gate、review 原表保留为 `*_legacy`；只迁移能与 Run、Bot、候选和
冻结 profile 核对的判定、门禁及评审记录。旧 Run 没有可证明的上限，按 `null` 处理：旧
`auto_promote` 结论只保留为历史，新结论由对应终态判定补算，审批过的合法条目不重开。
无法核实归属或从未写过独立判定的历史候选留待人工核对；报告没有本 Run 的判定时显示
`pending`，不会借用另一个 Run 的结果，也不会后台批量重验旧候选。
旧幂等表没有原始请求指纹，不返回归档响应；同一旧操作的原键重试返回
`409 idempotency_key_reused`，请先读取评审状态，不要直接重发审批。回滚旧程序须恢复升级
前的备份，不可直接用旧实例打开新 schema。升级和回放均不启用 apply。

### Worker Plugin API（内部作业面）

独立于用户控制面认证。前缀 `/openapi/v1/evolution/jobs`，历史相对路径保留，
所以完整 claim 路径是 `/openapi/v1/evolution/jobs/jobs:claim`；
Worker HTTP 客户端会自动拼接，勿擅自合并两个 `jobs`。
HMAC worker token 用于 claim，之后写入/心跳/完成使用当前 Job 的 fencing token。
协议实现 `adapters/jobs.py`、`core/evolution_run/job_protocol.py` 与 `plugins/job_http.py`；
不是控制面 HTTP*1000 信封。此入口不应暴露公网，须限制网络范围、TLS 和出网白名单。
旧协议有 query token，应用及示例前端代理关闭相关访问日志；外层网关、APM 也必须屏蔽。

### 候选写入拒绝（01–06，不是07评测结论）

`GenomePort.commit_patch` 对已知的内容冲突抛出领域错误
`CandidateRejected(ALREADY_RECORDED)`。HTTP provider 仅将候选创建端点的
HTTP422、业务码422000、且所有 reasons 都是 `already_recorded` 的响应映射为此错误；
未知、混合、畸形或超过64KiB的错误保留为安全的通用失败，不透传正文或路径。
同一 Run/patch 的幂等补交与跨 Run/来源的内容碰撞不能混为一谈，不能篡改历史 provenance。

- 用户 evidence 增加平台事件 `candidate.rejected`，details 为 `candidate_id` 和
  白名单字段 `rejection_code: "already_recorded"`；不含上游错误正文。
- 内部 Worker 候选接口返回HTTP422：
  `{"error":{"code":"candidate_rejected","reason":"already_recorded"}}`。
  这是失败响应的增补；现有 Worker 仍可按非2xx失败处理，无需重定义已注册策略镜像。
- 拒绝向上传播，不记 `candidate.persisted`，不增加 candidate_count、不写成功 Ledger，
  不移动 active、不生成评测结论。候选列表中空 revision 的 pending 行是提交占位，
  **不是已保存的候选或成功状态**。已有通用重试策略可能仍重试到 max_attempts。
- 重复运行同一确定性示例不会产生新内容。合法的新输出应通过有意义的策略/参数变更产生；
  修改策略实现必须提升版本并规划部署身份迁移，不能删旧候选、重置数据库或加随机标记凑成功。


### 已知缺口（不要当成已验收）

- **apply 未启用**：`promote()` 显式拒绝 apply；approve 只记录决定，`active` 不会移动。
- **静态底线未接线**：`check_floor` 返回 `not_run`。结构、可变基因、允许的基因与大小
  已由提交路径和 Genome 注册表强制；密钥 / PII / 出站 URL 扫描器尚未接入，
  因此门禁里没有 `static_floor` 这一项。
- **holdout 与预算对齐基线**未接入门禁（07 还没有产出 holdout 结果）。
- **revision 完整性**：revision 只覆盖 persona、本地 skills 与 resources，
  不是 bot 的完整快照——这也是 apply 保持关闭的原因。

## 持久化、恢复与观测

- `DATABASE` 存 Run/Job、绑定、预算、候选索引、工作区元数据、事件、幂等键和
  artifacts。SQLite WAL + FULL、事务认领/重派发、提交 Run+首 Job 原子落库。
- `${DATABASE}.ledger` 独立实验库；local 另有 `${DATABASE}.fixture` 模拟 Genome。
  生产 Genome 内容仍属平台；工作区元数据不代表远端沙箱物理文件已备份。
- artifacts 按 Run 归属存 SQLite，单份最多5MiB，重启可读，写失败向上传播。
  当前仅内部 Store 读取，尚无公开下载 API；不要把任意报告/会话正文当元数据日志输出。
- 一实例/一进程/本地持久卷，进程文件锁拒绝第二权威。暂不支持多副本或 NFS 共享写入。
  **历史 Backend/显式 acceptance 不遵守新进程锁，不能让它同时连接同一库。**
- 过期租约重新排队，下次 attempt 重新执行策略入口，保留原 Run 与冻结输入；
  默认最多3次。不是 Python 指令级 checkpoint；外部副作用必须幂等。
- 旧 fence、过期 fence 不得写回；终态不能被过期租约重新复活。Controller 强杀后
  远端孤儿沙箱的接管/销毁尚未实现，需要 provider TTL 与后续清理，不应承诺零残留。
- `evidence` 区分平台事件与 `worker-reported`，通过真实 claim 关联 Worker/沙箱；
  “策略打印了某条日志”不是平台确认沙箱创建的证据。事件是元数据白名单，不含任意正文。
- 状态提交与事件 append、Ledger 写入**不是跨库原子事务**；失败报错，但仍可能留下
  已提交状态/不完整 journal。不能以此实现严格 exactly-once 审计。
- HTTP500只记录 trace_id 与异常类型；健康检查仅存活。当前没有完整队列告警大盘，
  应检查 Run 状态、租约、attempt、events 进度，并为无进展/反复重派发建立部署侧告警。

## 平台接入、部署与回滚

`PLATFORM_FACTORY` 是可信部署代码，不接受 HTTP 指定模块。实现
`plugin_api.Platform`：Genome 契约客户端、Bot-scoped 脱敏会话导出、治理/停机开关、
策略注册及隔离沙箱。用户授权由 Backend 的默认链路提供，不由插件重复登录。
先部署支持内置回调的两端，在 Evolution 设置私有 `EVOLUTION_BACKEND_DATA_ORIGIN`
并由平台工厂将 origin、现有 principal 签名材料和租户传给 `BotHttp`；随后在 Backend
把 `evolution_control.builtin_data_tenant` 设为相同租户。不要向 Gateway/Worker 暴露
内部回调或签名材料。内置手动 Run 保存经回执认证的发起人，重启后仍以该人回查当前 Bot
权限；撤权或归属变更即拒绝后续数据访问，不降级到应用委托。候选仍只写入候选修订，
不发布、不 apply。旧 Run 不迁移，应用调用和未启用内置通道的执行仍使用正式服务委托。
任何通道都不能把浏览器 Cookie 或裸 user_id/owner_id 当成授权。

HTTP provider 的凭据回调现返回 `PlatformCredential(headers, acting_user_id)`，
不是旧的 headers 字典。acting_user_id 必须由服务委托解析，不回退到 Bot owner。
每次请求的 user_id 使用该委托身份、entity_id 使用稳定 Bot 范围；部署插件必须同步
适配并验证该凭据对目标 Bot 的权限。用户提交者仍保留为 Run actor，两者不可混用。原 Backend 平台适配器仍在原处，
**没有**复制进独立应用或用内部 import 绕过契约。

构建从仓库根：`docker build -f apps/evolution/Dockerfile .`。默认入口是控制面
`evolution serve`；Worker 镜像/Pod 必须显式选择
`python -m avernet_evolution.plugins.entrypoint`（acceptance 自检用
`avernet_evolution.plugins.smoke_entrypoint`），不能把默认 controller 当 Worker 启动。
`deploy/deployment.example.yaml` 是模板，须供应实际平台镜像/工厂、Secret、PVC、
内部路由和 NetworkPolicy 后验证；不是已部署资源。

切换步骤：

1. 先停旧 Backend 的 Evolution consumer/调度器并排空或明确冻结任务。
2. 停止全部写入，备份控制库、Ledger、平台 Genome 及密钥；SQLite 必须 checkpoint
   并干净关闭后复制，或使用 SQLite backup API，不能运行中只拷主 `.db` 忽略 WAL。
   多库快照需停写保证一致。备份权限限制、密钥与数据分开保护。
3. 将状态副本放独立持久卷（保留原库只读），用同一签名材料和策略版本试启动。
   从旧部署迁移时必须确认原签名算法/字节一致，并核实队列及候选引用可解析。
4. 在独立环境验证真实鉴权、02会话、Worker 镜像、候选写入、取消/超时/恢复；
   特别核查既有策略的 models/operations provider，不能由目录列表推断可执行。
5. 用户控制 API 保持经 Gateway → Backend，配置 Backend 的 `evolution_control`
   内部 origin；仅内部 Worker 网络连接独立 Evolution 作业面。不得由浏览器绕过
   默认登录直连 Evolution。前端容器只需 `RSI_ENABLED=true` 开放入口，继续使用
   既有 Gateway 上游；移除旧直连路由和 `EVOLUTION_UPSTREAM`，开发代理同理。
   开发 runtime-config 的 RSI 开关仍需显式注入；Genome/Promotion 原路由不变。
6. 回滚先关 UI 和新 consumer、停止全部写入。用迁移前备份恢复旧服务；若新服务
   已产生候选/写回平台，须核对和保留增量，不可直接丢弃新库或同时启动新旧消费者。

**待切流条件**：真实 Platform 插件与内置回调或服务委托、Swarm 子操作/workspace 映射、
孤儿沙箱治理、singlebox 新服务接线及真实页面验收。SkillSet 捕获/编辑继续为
[RSI-25](../backend/docs/bot-evolution/work-items.zh-CN.md#rsi-25-genome-兼容-skillset-的捕获与编辑)，不包含在此次迁移内。

## 验证

```bash
apps/evolution/scripts/ci_test.sh          # uv frozen + ruff + 格式 + 独立测试
apps/evolution/scripts/ci_test.sh --lint
apps/evolution/scripts/ci_test.sh --test
```

`tests/test_cli_restart.py` 用真实本机 HTTP/CLI 进程验证强杀→重启→租约重派发，
不操作真实 Bot。Backend 兼容性回归与前端请求/部署脚本测试见 migration report；
未验证项必须与通过项分别记录。

## 源码归属整理（2026-11-18）

领域说明与代码同址：[运行时](src/avernet_evolution/core/evolution_run/README.md)、
[策略 SDK](src/avernet_evolution/core/evolution_strategy/README.md)、
[账本](src/avernet_evolution/core/evolution_ledger/README.md)、
[已有验证器](src/avernet_evolution/core/evolution_verify/README.md)。

Backend 默认组装不再安装历史 Evolution Runtime/Jobs 模块，也不挂载 Worker HTTP；
旧 `EVOLUTION_CONSUMER_ENABLED` 不能在默认 Backend 中启动 RSI。仍须先停止可能运行
旧镜像的消费者，源码变化不会停掉已部署进程。生产启停与恢复只由本应用负责。

Backend 保留 Bot 权限入口、Genome/Promotion 和真正依赖平台内部契约的适配器；
[显式验收组合](../backend/src/agentclaw/community/di/evolution_acceptance/README.md)
只为旧版跨平台测试保留，不是第二份生产 runtime。旧 core/Worker import 别名用于
兼容，新增实现和测试只能写 canonical 包。旧 `core/evolution_run/genome_registry_port`
与 Backend Worker router/旧 DI module 路径已移除，仓内调用者均已迁移；外部显式验收
调用者须按上述目录更新，不应重新挂回 Backend 默认启动。

只读事件观察（数据库路径由部署提供，内容可能受限）：

```bash
apps/evolution/.venv/bin/python apps/evolution/scripts/evolution_events.py \
  --database '/absolute/private/path/control.db' --run-id '<run-id>' --follow
```

这是 SQLite `mode=ro` 读取；`--after` 接续、去掉 `--follow` 导出。事件中 Worker 自报
起止不等于独立沙箱证明，仍需核对租约、候选、Ledger 和沙箱生命周期。

### Simulator 接线的证据边界

独立服务与 Backend 的真实路由/回执/Binding 存储接线由
[双服务测试](../backend/tests/community/adapters/http/openapi_v1/test_evolution_service_wiring.py)
覆盖；具体分层验收见 [Bot 控制入口](../backend/docs/flows/bot-evolution-control.md#分层接线验收)。
内置通道用例还覆盖 Run 重开、候选和规则验证。这是合成身份、临时数据库、关闭 consumer 的隔离测试，不证明宿主登录、镜像组装或
真实 Platform 已部署。企业宿主应保留自己的构建/配置/只读检查脚本；公共仓不引入企业
端点、浏览器 Cookie 或机器状态。仅打开 UI 开关或安装本包不能作为真实任务执行证明。
