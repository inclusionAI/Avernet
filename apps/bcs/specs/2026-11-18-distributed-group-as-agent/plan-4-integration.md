# Distributed Group A2A Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 Gateway/Worker/Connector 与持久参考 provider 跑通 Group A2A 首期能力，交付恢复、访问成本和发布门槛的可复验证据。

**Architecture:** composition root 选择角色及插件，HTTP adapter 只装在 Gateway；Worker/Connector 的生命周期与上游连接无关。分布式 Session 受管删除首期关闭；I02/I03 只能验证旧删除入口明确拒绝且无副作用。新旧会话按 source/backend 分流，不允许同 Sid 被两套 Runtime 执行。

**Tech Stack:** 现有 BCS bootstrap、配置验证、Tokio、Axum、本地可控下游 A2A Server、SQLite 参考和宿主生产 provider。

---

前置：[Gateway](plan-1-gateway.md) G01～G06、[Runner](plan-2-runner.md) R01～R03、[存储/内核](plan-3-storage-runtime.md) S00～S07 可装配。D0 还必须完成 bcs-dist-model 0.2.0、bcs-service-api 7.0.0 的版本/构造者/锁文件传播、持久 v2/旧 v1 拒读测试、R25 harness 和包含模型的公开 API gate。未交付的依赖不以 Noop 代替。Judge 若未完成 R04，集成能力校验明确拒绝含 Judge 群。

## I01：配置、真实角色装配与后端分流

**Files**

- Create：`apps/bcs/crates/bootstrap/bcs/src/{dist_group_wiring,config/dist_group}.rs`。
- Modify：`apps/bcs/crates/bootstrap/bcs/src/config/{root,mod,validation}.rs`、`apps/bcs/crates/bootstrap/bcs/src/server/{server_run,lifecycle_wiring,server_construct}.rs`、`apps/bcs/crates/bootstrap/bcs/Cargo.toml`。
- Modify：`apps/bcs/crates/service-api/bcs-services-container/src/services.rs`，只注入应用服务；不开 adapter 对 Core/repo 的直接访问。
- Create：`apps/bcs/crates/bootstrap/bcs/tests/{dist_group_config,dist_group_wiring}.rs`。
- Modify：`apps/bcs/configs/bcs-config-local.toml`、`apps/bcs/configs/bcs-config-example.toml`，新入口默认关闭。

- [ ] **1. 写配置和装配失败用例。** enabled 但无真实 provider/认证/外部基地址必须启动失败；worker-only 不挂 HTTP；旧 Session 删除入口遇到 distributed/group_a2a source 必须先返回 DELETE_UNSUPPORTED，spec/ckpt/round slot 均不变；gateway-only 不启动 worker；关闭新受理但仍恢复旧义务。tests 确认传给 HTTP state 的只是 application。
- [ ] **2. RED：** `cargo test -p bcs --test dist_group_config --test dist_group_wiring`。
- [ ] **3. 新增强类型配置，在 bootstrap 解释实现选择。** 例如：

```toml
[distributed_group]
enabled = false
roles = ["gateway", "worker", "connector"]
accept_new = false
http_wait_ms = 60000
execution_ms = 1800000
closing_ms = 60000
heartbeat_ms = 15000
recovery_page_size = 64
history_default_length = 20
```

provider identity、namespace、advertised_base_url 和 auth 配置使用已有系统来源；无缺省生产私有 endpoint。后端名必须对应可用插件，不能未知值落回 memory。公共 single-process 参考使用 file-backed provider，重启路径位于显式测试临时目录，不写 repo runtime state。

history_default_length 必须为 0～100，否则启动失败。预算 v1 的 100 条/8 MiB 硬上限由 D01 合同固定，不允许配置绕过；application/Core/store/Blob 与 adapter 注入同一预算版本，持久请求保存实际采用值。模型 0.2.0/持久 v2 的 provider 遇到旧 v1 明确拒绝而非猜 group/source；未交付有界读取的 Blob provider 不得装配公开结果读取。

```text
build providers including file Blob adapter + BlobLifecycleRepo -> validate capability/health/schema -> build ports
-> build Core + application + pure Runner
-> build Worker/Connector engines with shared ports
-> mount Gateway adapter if gateway role enabled
-> start role loops, Accepted/Abandoned obligation recovery and bounded Blob GC
-> inject shared C01 validator instance/profile into Card/admission/Runner
shutdown: stop new admissions -> drain or retain obligations -> close readers -> provider teardown
```

由 composition 的 Router 合并独立 Gateway router，不让 `bcs-http` 获得新 Runtime 核心引用。source=group_a2a/distributed 的 Sid 不调用旧 Session/Run 执行；已有旧入口、A2A 管理/出站保持原有行为。

- [ ] **4. GREEN 与提交：** 角色矩阵、默认禁用、无 provider 启动失败、drain 不丢待调度义务通过；提交 `feat(bcs): compose distributed group roles behind gateway configuration`。

## I02：全接口用户故事与 golden 协议验证

**Files**

- Create：`apps/bcs/crates/bootstrap/bcs/tests/{dist_group_e2e,dist_group_e2e_cancel}.rs`。
- Create：`apps/bcs/crates/bootstrap/bcs/tests/dist_group_support/{mod,server,downstream,fixtures}.rs`。
- Create：`apps/bcs/crates/bootstrap/bcs/tests/dist_group_fixtures/{two_nodes.yaml,fanout.yaml,task_completed.json}`。
- Modify：`apps/bcs/crates/bootstrap/bcs/Cargo.toml` 的 dev-dependencies，仅复用 workspace 依赖。

- [ ] **1. 建可控本地 A2A 下游 fixture 和真实角色 world。** fixture 提供暂停/恢复、按稳定 messageId 查询 Task、主动断流、取消已接收/已停止/未知三种行为；所有请求记录到受测服务可观察的计数器。world 的 Gateway/Worker/Connector 和 repo 都是真实实现，不导入 Script 预填响应。`pause_before_round_release()` 安装 Worker 命名 barrier 并返回可 resume 的测试 handle；`wait_terminal(tid)` 只轮询公共终态，不等待解锁；`wait_round_released(tid)` 在测试内等待真实 GroupInvocation 的持久释放确认，有测试期限，不新增公共解锁查询 API。`expect_send_next_busy(context, message)` 发真实 HTTP 并断言 409/CONTEXT_BUSY、Task/Input 数不增加；所有等待用 barrier/通知或有界轮询，不用固定 sleep。

```rust
#[tokio::test]
async fn two_rounds_keep_one_context_and_two_artifacts() {
    let world = dist_group_support::start_file_backed_world().await;
    let release_barrier = world.pause_before_round_release().await;
    let first = world.send_new("round-one", true).await.unwrap();
    let completed = world.wait_terminal(&first.id).await.unwrap();
    assert!(!completed.artifacts.is_empty());
    world.expect_send_next_busy(&first.context_id, "round-two").await;
    release_barrier.resume();
    world.wait_round_released(&first.id).await.unwrap();
    let second = world.send_next(&first.context_id, "round-two").await.unwrap();
    assert_eq!(first.context_id, second.context_id);
    assert_ne!(first.id, second.id);
    assert_eq!(world.downstream.start_count_for("round-one"), 1);
}
```

- [ ] **2. RED：** `cargo test -p bcs --test dist_group_e2e --test dist_group_e2e_cancel`。
- [ ] **3. 跑每条真实 HTTP 用户故事。** Card、send(true)/get、默认阻塞/超时、stream/subscribe 首帧和终态、分页列表、busy、重复/冲突/过期、文本+JSON、多 Artifact、同 context 两轮；后端读写都通过真实服务。

必须包含：

| 故事 | 断言 |
| --- | --- |
| 原响应丢失后群从 text 改 JSON-only | 原请求重试复用原 Task；新 context 用新能力 |
| 默认值/扩展集合更新 | 同请求按保存 basis 比较，内容变化冲突；权限撤销仍拒绝 |
| 省略配置的原请求重试，默认 returnImmediately/historyLength 已变更 | HTTP 仍按 GroupSubmitResult 的原有效值等待及裁剪历史；不读取当前默认值覆盖；显式请求值仍优先于保存的缺省值 |
| submit 已成功，任务在 observe 建立前或首帧后/follow 前完成 | 阻塞 send 返回完整终态；message:stream 给出终态快照或随后终态事件并关闭；公开终态 subscribe 仍拒绝 |
| 两 Gateway 同消息和不同消息并发 | 同消息一个逻辑轮次；同 context 不同消息一方 busy 且无新 Task/Input |
| 首轮省略 contextId / 显式未知、跨群、跨主体、旧来源 contextId | 仅省略时生成 Sid；其余统一 CONTEXT_NOT_FOUND，无 reserve/claim/Task/Input，不替换 Sid；原省略 context 请求恢复仍使用原 Sid |
| 同请求 claim 成功但响应丢失 | 再次 claim 返回 AlreadyOwned，复用原记录/generation；SlotHeld 是记录状态，已释放原轮不重新占用 |
| Lost→其他请求占用→Busy，随后释放并运行恢复器 | 被拒准备不会自动开始；同 messageId 新 revision 可重试，旧恢复者不能影响它 |
| A 以 T0 准备，B 完成 T1 后 A 重新 claim | A 条件刷新为 T1 背景及相应 generation；请求指纹/ID/deadline 不变，刷新竞争不绕过上限 |
| 归档终态已可见，before_release_round barrier 未放行 | 下一轮明确 Busy；放行并确认持久 release 后，用同 messageId 重试成功 |
| 同 Sid 两轮 SQL 归档 | tid1/tid2 的 archive 与各自产物均保留，不被 sid 唯一约束阻断 |
| typed Start/Finish | 延迟和接管保留绝对 deadline，Artifact IDs/position/媒体模式/digest 不由 Worker 改写 |
| shared validator 不支持定义 | Card、Gateway 准入和 Runner compile 同结果，reserve/claim/Inbox 调用数均为 0 |
| 旧删除入口访问新 Session | 明确拒绝且无存储副作用，不存在删除后强制解锁路径 |
| 同 caller 跨群/其他主体访问和游标 | get/list/subscribe/cancel 均不泄露，过滤在分页前 |
| Get/List 的历史、时间、context/status 与分页参数 | historyLength=0 无历史且存储不变；statusTimestampAfter 在分页前筛选，同时间按 tid 稳定分页；游标不可换群/筛选条件 |
| historyLength 默认/0/100/101 与整页预算 | 默认20，101受理/读正文前拒绝；100 Task × 100 history 共用8 MiB，连同Artifact/JSON转义超限明确错误、不静默截断 |
| 已受理 send 或 SSE 后续帧超预算 | 保留原 Task/执行，响应含安全 ID；后续 SSE 标准错误后关闭 reader，不取消、不截断 JSON |
| ListTasks 页元数据 | 空页/末页保留有效 pageSize，totalSize 是授权筛选后分页前的总量；hot/archive 同 Tid 不重计，其他主体数据不影响数量 |
| ListTasks 的 includeArtifacts 缺省/false/true | 缺省及 false 不返回或加载产物；true 批量加载当前页，SQL/对象读取数不出现逐任务 N+1 |
| 不支持 HumanInput/Judge/Loop | 受理前拒绝，无 Task/占用；部署未交付能力不出 Card |
| 取消在途节点 | receipt 不是 CANCELED，直到全部停止；重复取消同身份 |
| 取消意图已受理→响应丢失→Task终态→再次 cancel | 返回同一持久 cancel_request_id 对应的真实 Task，wire 不新增 ID 字段，Inbox/派发次数不增加；无既有意图的终态首次取消才 TaskNotCancelable |
| cancel/finish 竞争 | 一个终态；旧消息不公开、旧 release 不解锁新轮次 |
| 公开进度含 thinking/tool/stack | get/list/history/Artifact/SSE 一致过滤 |
| 多 Part 各带 filename/metadata，Artifact 顶层另有 metadata | 允许公开的属性分别通过归档/get/list/SSE 保留；输入属性改变参与冲突判定，私有属性不泄漏 |
| SSE 慢消费者/断线 | 无业务取消、不续期；重订阅快照校准，不承诺逐 token 重放 |
| archive 保存完整、Feed 未追平 | GetTask 完整终态；旧 SSE 先排空边界，再产物/终态 |

- [ ] **4. GREEN：** 两 target 非零通过；golden JSON 使用项目固定 SDK 的标准字段和错误结构。提交 `test(bcs): exercise group A2A through real distributed roles`。

## I03：跨进程恢复、持久 provider 与故障矩阵

**Files**

- Create：`apps/bcs/crates/bootstrap/bcs/tests/{dist_group_restart,dist_group_processes}.rs`。
- Create：`apps/bcs/crates/bootstrap/bcs/tests/dist_group_support/{processes,fault_barriers}.rs`。
- Create：`apps/bcs/scripts/test-dist-group-recovery.sh`。
- Create：`apps/bcs/specs/2026-11-18-distributed-group-as-agent/validation.md`（执行时记录实际证据）。

- [ ] **1. 测试进程 world：两个 Gateway、至少两个 Worker 和两个 Connector，共享持久参考存储。** process wrapper 传独立角色配置/临时端口，用显式就绪信号和命名 barrier 代替固定长 sleep；停止进程后保留临时数据供重启，日志写仓库外。
- [ ] **2. RED：** `cargo test -p bcs --test dist_group_restart`；进程测试创建为显式 ignored，用 `cargo test -p bcs --test dist_group_processes -- --ignored --nocapture` 真正执行，禁止将 ignored 数当通过。
- [ ] **3. 各故障点执行 action → kill/restart → assert durable outcome。** 每个 case 建独立数据目录和固定种子，可复验命令写 validation。

```text
fault points:
  after claim / after Inbox / after Accepted+pending, before reschedule
  observe-before-license with settle-before-reschedule
  reschedule-before-stale-settle, including equal due score
  waiting-license takeover; Start commit succeeded but proof confirmation lost
  Worker commit applied but response lost; stale owner write
  Connector sent but acceptance lost; cancel accepted but not confirmed
  Gateway cancel intent/admission durable but HTTP response lost -> terminal -> retry same cancel
  new-context preparation without SessionSpec -> original omitted-context retry; supplied unknown Sid rejected
  archive committed before checkpoint; partial Feed append; snapshot before release
  Inbox Quarantined/ContentConflict -> abandon/discard; activate vs abandon race
  claim Lost generation churn; abandon applied before Drop reschedule or response lost
  Lost-to-Busy rejection response lost; stale rejected preparation replayed after next revision
  background refresh after an intervening successful round; refresh/claim result unknown
  abandon with no spec/input -> seal ClosedAbsent -> absence confirmation lost
  append-before-seal / seal-before-append; duplicate inputs through sealed drain boundary
  manifest prepared -> put applied -> archive not committed; ref add vs GC claim
  file delete applied but acknowledgement lost; pending writer put still unknown
  result cleanup / blob cleanup failed; old release after next round
  checkpoint compacted after 500 rounds -> late event/effect/old release
```

每例断言稳定 Sid/Tid/EffectId、最多一个逻辑业务轮次、原 deadline 不变、未确认停止不释放；Lost 的恢复必须使用匹配 generation 的背景，已拒准备不得被恢复为执行。无输入 abandon 完成后不再主动扫描/重调度，且没有假 spec/task/checkpoint；封口后旧 append 被拒，封口前副本排空后才确认 Drop。终态可见但 release 未完成时下一轮仍 Busy，不用测试 sleep 隐藏该窗口。不把“外部可能已执行”误写成 exactly-once。生产 provider 用同样 consumer suites 和进程矩阵跑，TBase 复制/版本/故障域语义单独确认。

若基础 provider 仅支持单进程，必须如实将 cross-process gate 标为失败/未完成，不能绕过实例锁或改用 memory 继续报告通过。

- [ ] **4. GREEN 后记录并提交：** `bash scripts/test-dist-group-recovery.sh` 应执行上述命令、保留每例退出码、用例数量、provider 与 Git SHA；提交 `test(bcs): verify durable group recovery across role failures`。

## I04：访问成本、完整 CI、知识维护和发布门槛

**Files**

- Create：`apps/bcs/crates/bootstrap/bcs/tests/dist_group_load_bounds.rs`。
- Create：`apps/bcs/crates/bootstrap/bcs/tests/dist_group_checkpoint_bounds.rs`，覆盖 S07 500 轮压缩。
- Modify：`apps/bcs/README.md`、`apps/bcs/specs/README.md`、`apps/bcs/docs/flows/README.md`、`apps/bcs/docs/diagnostics/README.md`。
- Create：`apps/bcs/docs/flows/distributed-group-a2a.md`、`apps/bcs/docs/diagnostics/distributed-group-a2a.md`。
- Modify：`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/distributed/README.md` 及各实现 CONTEXT。
- Modify：旧 Group 草案追加采用范围/结果链接；分布式计划的任务接管导航已在本计划 §2 确定，此处只补实际交付链接，保留旧证据；本目录 validation.md 记录实现/测试/宿主验证边界。

- [ ] **1. 写最大尺寸/持续失败成本测试，另在同一 Sid 连续执行 500 轮并按 S07 的固定上限对比 1/100/500 轮 checkpoint 字节、读取行数、提交写量。** 基线增长仅作诊断，RED 必须是目标成本上限断言失败。使用 S06 recording provider 统计实际 SQL/插件调用及行数/字节；记录 normal、max-page、129 pending 公平扫描、1000 历史任务、1 MiB 输入、大结果引用、慢 SSE、连续 provider 错误。额外覆盖 historyLength 默认20/0/100/101、100 Task × 100 history + includeArtifacts=true、8 MiB ±1 和转义膨胀，确认读取/分配/编码在预算内或在大量物化前拒绝；验证 get_bounded 真实 IO 及每 token 不读许可证、不写 OB。
- [ ] **2. RED：** `cargo test -p bcs --test dist_group_load_bounds --test dist_group_checkpoint_bounds`；应在 N+1、过量扫描或无限内存时失败。
- [ ] **3. 完成边界调整后只跑必要回归，再跑整体验证。**

```bash
cargo test -p bcs-dist-model -p bcs-service-api -p bcs-dist-store
cargo test -p bcs-dist-blob-local --test conformance_blob_store
cargo test -p bcs-app-dist-gateway -p bcs-dist-gateway -p bcs-dist-gateway-http
cargo test -p bcs-dist-runner-fsm -p bcs-dist-worker -p bcs-dist-connector
cargo test -p bcs --test dist_group_e2e --test dist_group_e2e_cancel --test dist_group_restart --test dist_group_load_bounds --test dist_group_checkpoint_bounds
cargo test -p bcs-test-support --test distributed_harness --test conformance_group_call
cargo check --workspace --all-targets
bash scripts/ci/check-r25-conformance.sh
bash scripts/ci/tests/public_api_version_regression.sh
bash scripts/ci/check-public-api.sh
bash scripts/ci/arch-check.sh
```

另跑现有 `bcs-http`/`bcs-session`/`bcs-collaboration-runtime`/`bcs-a2a-http` 受影响测试，证明旧 API/Runtime 未被错误分流。真实 MySQL 迁移和进程测试按 S04/I03 单独执行，不以本段命令替代。现有 warning、baseline failure、skip 分开记录，禁止放宽 hook/gate 使验证表看起来通过。R25 入口名称必须按 trait_snake 规则；PR propagation analysis 同时声明模型 0.2.0、Service API 7.0.0、持久 v2/旧格式拒读和外部构造者迁移。确认 public API gate 实际扫描 contracts/bcs-dist-model，不能用旧脚本的三 crate 结果替代。

- [ ] **4. 更新诊断与交付记录。** 诊断至少区分受理未知、pending license、pending scheduling、archive 等待、terminal publication、停止未知、毒会话隔离；按 request/tid/effect/commit 关联，不输出秘密。文档命令只能使用公开依赖和本地端口，运行态留仓库外。
- [ ] **5. 发布检查点与提交。** 验证记录列 provider/配置、Git SHA、命令、退出码、测试数量和未验证项。默认新入口关闭；通过 D0/组件/I1/I2/真实 provider gate 后才允许部署开关评审，不能由功能测试推断 10 万 TPS、RPO=0 或恢复 SLA。提交 `docs(bcs): record distributed group validation and operation boundaries`。

本计划的完成条件是功能和对应验证证据齐全；如缺真实生产 provider，只能报告公共参考实现和本地集成完成，并明确生产门槛尚未完成。
