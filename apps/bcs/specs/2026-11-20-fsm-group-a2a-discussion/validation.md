# T1 验证记录

状态（2026-11-25 范围调整）：**D0 冻结仍待复核；按用户决定拆分 provider 交付，允许继续后续功能实现，不代表 Stage 2 已完成或真实后端验收已通过。** 当前开发授权以 plan 的“交付范围与执行授权”为准；以下原状态裁决是调整前记录，测试结果不变。 当前修复基线为 `564a2b7e30`；下列第一至第五轮为历史记录，不代表当前工作树已通过。最新证据与状态见“第六轮”。未执行合并、部署或 push。

## 2026-11-27：公开路由与停止证明复核

基线 `a19a107251` 的四个反例均使用同进程真实角色装配重现：下游仍在 Sending 就提交终态；在已完成的 context 发第二轮返回 503；运行中取消返回 503；公开 Get 返回 Completed 但没有最终节点的 Artifact。以下测试检查调用方可见的结果，不把仓库直接受理或测试桩的执行次数当作 REST 验收。

- `group_a2a_rest_bridge`：从公开入口连续完成两轮，无须重启 Worker；Busy 不改调度版本，原键重投返回原 Tid。常驻 Worker 只在核对同 owner 的严格许可证、提交回执和 checkpoint 摘要后接收更新；不能丢弃结果不明的本地写。此项取代第七轮的 resident-slot 功能缺口，仍不代表跨进程部署验收。
- `group_round_recovery` 和 `group_a2a_rest_cancel`：已登记取消但下游未确认停止时，Get 保持非终态、下一轮保持 Busy；收到原尝试的停止证明后才能完成。若需追加取消批次，在替换原 SaveResult outbox 前验证归档并保留结果引用。
- `group_a2a_rest_bridge` 的最终产物断言核对评审节点的实际文本，同时回写旧 Working 快照模拟 Feed 落后；已确认的终态和产物不随 Feed 回退，未取得许可的任务不可见。
- `uncertain_send` 使用真实 Connector 和可控下游覆盖新发及接管后的不确定结果。当前解析接口不提供可验证的去重域与保证期限，因此不能用冻结 binding 或调用 deadline 授权重发；无可用查询键的首发由核实通道保留，不伪造停止。
- `dist_agent`、`gateway`、`task_reader` 另覆盖有序输入与前驱结果的实际 A2A 消息、旧 RPC 列表来源隔离及同一结果集过期后重新分页。`bcs-dist-model` 0.2 增加持久化 `FinalAnswer` 类型，旧 reader 不认识新枚举值；启用写入新格式前须同步升级读取方。`bcs-service-api` 9.1 增加专用取消入口，未实现的 provider 默认拒绝。

本轮最终回归（BCS 工作区，`--locked -j1 -- --test-threads=1`）：12 个受影响 crate 完整测试共 **933 passed / 0 failed / 0 ignored**；另跑 `bcs` 的 `group_a2a_companion`、`group_a2a_rest_bridge`、`group_a2a_rest_cancel` 三个目标，**17 passed / 0 failed**。`cargo check --workspace --all-targets --locked`、端口纯度和存储边界检查、相对 `a19a107251` 的真实 public API 差异和旧格式 wire 检查均通过。不是全 workspace 测试或真实后端验收。

**功能仍缺：跨轮节点历史。** `NodeHistoryRepo` 单独的存储契约可用，但受理尚未固定每个节点的已提交历史边界，派发与接管尚未绑定可重放的输入 pin，实际下游交互和所属轮次结局也没有贯穿运行路径。连续两轮成功、同一节点不泄露其他节点数据，并不能证明“第二轮继承自己的历史”。真实持久 provider、真实 MySQL 和跨进程验收仍独立，不以本记录放行。

## 第六轮（2026-11-25）：审查修复与状态重新核定

完整修复映射、RED→GREEN、实际命令与限制见 [修复验证记录](review-fixes-2026-11-25.md)。受影响 8 crate 完整套件 **1078 passed / 0 failed / 6 ignored，exit 0**；API 脚本回归 4 passed。真实 MySQL、P、当前完整 API/R25 与全量 workspace 测试不以这些结果替代。**D0 原冻结结论暂停；Stage 2 未完成退出验收，不放行 Stage 3。**

## 第七轮（2026-11-26，plan T9）：装配、注入故障、可上线范围记录

本节是 plan T9 的交付记录：功能装配、配置接缝与验收程序。**不回填 Stage 2；T1 节头不动。** 全部结论以同进程功能证据为层级；跨进程矩阵、真实 MySQL、容量认证与 P 认证按 2026-11-25 拆分裁定继续 **blocked(P)**，不以任何同进程结果替代。

### 实际角色与挂载

| 角色/接缝 | 实际形态 |
| --- | --- |
| 持久 provider | 同进程 Model（`bcs_test_support::store_model`，测试专用实现），经 `GroupA2aProvider` 注入五类 store plugin 端口；Result/Blob 走既有 SQLite + 迁移链。**生产 P 端口零实现，生产启用=启动失败**（`mount_production` refuse，`resolve_production` 无认证实现注册）。 |
| 认证/授权 | Host 注册的 D0 策略 fixture（published/invoke/read/cancel 四项开关），经 `GroupA2aHost.policy` 注入；无匿名回退。 |
| 群目录 | Host 注册的目录 port fixture（research→review 两节点工作流），经 `GroupA2aHost.directory` 注入；冻结修订校验按 T8。 |
| Worker/Runner | 真实 `bcs_dist_worker::WorkerRuntime`+`WorkerProcessor`；Runner 为真实 strict-FSM reducer（`bcs_dist_runner_fsm::FsmReducerRunner::d0`）。**T9 落-runner 接线**（见“联合装配补齐”）新增 `runner_start_inputs` 桥接（`bcs-dist-worker prepare.rs`）。 |
| Connector | 真实 `bcs_dist_connector::ConnectorEngine`+`DistConnectorRules`；下游 A2A 对象为受控 journal fixture（协议允许边界：idempotent dedupe、按需完成）。 |
| Supervisor | 真实 `DispatchSupervisor`（`supervisor_enabled` 配置开关；disabled 即崩溃恢复对照组，装配为 `None`）。 |
| listing/Card | `bcs-dist-gateway-store` 真实 `StoreTaskReader`（同一 reader 服务 T4 legacy list + T8 GroupListingPort）、`cursor_secret` 生产配置落线、未配置即 fail-closed；Card 为 `GroupDiscovery`（published-only）。 |
| reclaim 驱动 | 最小接线：`GroupA2aRoles::tick` 在每个 shard 心跳驱动一次受限批量 `reclaim_dispatch_directory`（稳定游标，T7 切片的效果面）。 |
| D3 held-index 抢救 | 联合装配自建宿主提供方（`AssemblyHeldRecovery`）：扫描真实 held 索引（`layout.held`），按所见 revision 将死主持有的 dispatch **重新 arm 为 due**（真实 `DispatchControlPort::reschedule`）；**唤醒动作由 DispatchSupervisor 完成并以聚合的 `SupervisorReport.notified + settled ≥ 1` 为通过断言**（drive 报告聚合全部心跳迭代，先 visited/notify 后 success 才算过）；D8 保持未接线（recorder 断言零调用）。仅在 supervisor 装配下挂载——**禁用 Supervisor 的对照组不得恢复**。 |
| Heartbeat | 装配不 spawn 后台循环：宿主驱动 `GroupA2aRoles::tick`/`CompanionWorld::drive`（测试驱动 Model lease 时钟，`model.advance`），跨进程真实时钟/生命周期归 P 装配。 |

配置接缝 `[group_a2a]`：`enabled`（默认 false）、`cursor_secret`、`supervisor_enabled`、`verification_backoff_max_ms`（≤64 讨论、已接线并受 ConnectorConfig 校验约束）、`shards`、`public_url`、`namespace`。cut-snapshot 保留政策未定（contract-decisions:174）：**记录为 ONLY-RECORD**，配置注释明示“无治理决定不得加开关”；`due_verifications` ≤64 单趟续页与 retained 核实 ≤8 lane 为结构性定数，注释记录。

### 联合装配补齐（本次交付内新发现并修复的生产缺口，三项全部同进程证据+受影响 crate 全量回归）

1. **admission 身份摘要稳定性**（`bcs-dist-store/src/inbox/validation.rs`）：`request_digest` 把系统时间戳字段计入身份 → 两次同等构造 digest 不同 → `strict_start_rejected`（对面异步骤时序）。修复：身份摘要剔除 `create_task/session` 的 created_at/updated_at/时间戳（同 `WorkerTaskAdmission::recover` “永不比较新生成时间戳”原则）。受影响 `bcs-dist-store` 全量 46 suites exit 0。
2. **strict FSM 轮次进入 Runner 的生产桥**（`bcs-dist-worker/src/processor/prepare.rs`）：排队任务的提升此前只送 `RunnerInput::Start`，真实 FSM reducer 需要 `GroupStart`（冻结 RoundInput）。新增 `runner_start_inputs`：FsmGroup 会话+rounds 装配时从**license `get` 的权威 cut** 取整输入（不信任 inbox payload）。受影响 `bcs-dist-worker` 全量 31 suites exit 0（T7 commit-spine harness 同步对齐 GroupStart 入口）。
3. **shard lease 轮换下的 licensed admit 绑定 + 单 shard 范围 + 外主排斥**：`StoreGroupRoundAdmission` 构造即 pin 一个 ShardClaim，接管后旧 claim 获 `strict_session_fenced`。修复于装配层（`RebindingRoundAdmission`）：(i) shard 由 claim 派生（`[group_a2a].shards` 非 1 元素即启动失败——多 shard 需按 shard 多装配，P 期决定）；(ii) 只跟踪宿主注册 leaser 的 claim，**未注册外主的 claim 一律 strict_session_fenced 排斥，不借用他人 epoch**；(iii) 轮换时依当前 shard 记录重建绑定，持久行/许可证不动。
4. **T17 披露的 T9 隔离腿落地**：通过真实机械（no-dedupe profile 的不确定首发 → AskAgent 短期耗尽 → 核实队列 handoff → 5s stop 停核期限）产出**真实 `DispatchRecord.quarantined`**（向量 `quarantined_round_keeps_working_face_and_bounded_stream`）：GET 经真实路由保持 **WORKING 面、非终态**；订阅先快照后有界关闭；同键重投奉回已决槽位（仍占用）。

### 关闭入口（计划必含断言）

`entrance_closed_keeps_existing_routes_untouched`（装配口径）+ 修复轮新增 `closed_entrance_real_router_mounts_no_group_routes`（**真实构建路由**口径）：默认配置下 `BcsServer` 实建 + 随机端口，全组 REST 面（message:send/stream、tasks 列表、agent-card）逐一路由 **404**（未合并，而非挂载面 401/40x），既有路由 `/health` **200 不受影响**——计划“关闭入口时既有路由不受影响”必含断言两面齐备。

### 命令与结果（PIPE-FREE 日志 + 捕获时 `TEST_EXIT`，均在 `.superpowers/sdd/plan/logs/`）

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `cargo test -p bcs --test group_a2a_companion`（接线前 stub 装配） | RED：功能性场景 3 失败/2 通过（NotMounted 拒绝），`TEST_EXIT=101` | `task18-red-companion-run.log` |
| 同上（装配完成+修复轮） | GREEN：**11 passed / 0 failed**（takeover 全链、acked 死连接器 + Supervisor 唤醒归因、对照、配置层+真实路由双关闭入口、P 缺失启动失败、cursor fail-closed+行、backoff 校验、多 shard 拒绝、外主 lease 排斥、隔离段 quarantine 腿 GET/流/占用），`TEST_EXIT=0` | `task18-green-companion.log` |
| `cargo test -p bcs-dist-worker --no-fail-fast` | 31 suites 全 ok，`TEST_EXIT=0` | `task18-green-bcs-dist-worker.log` |
| `cargo test -p bcs-dist-store --no-fail-fast` | 46 suites 全 ok，`TEST_EXIT=0` | `task18-green-bcs-dist-store.log` |
| `cargo test -p bcs-db-migrations --no-fail-fast` | 全 ok，`TEST_EXIT=0` | `task18-green-bcs-db-migrations.log` |
| `cargo test -p bcs --no-fail-fast`（整 crate） | 87 test targets **894 passed / 0 failed**，`TEST_EXIT=0` | `task18-green-bcs-whole-crate.log` |
| `cargo check --workspace --all-targets` | Finished，`TEST_EXIT=0` | `task18-green-workspace-check.log` |
| `bash scripts/ci/check-r25-conformance.sh`（静态腿全跑；R25.3 环境有界） | 静态腿结果与分支基线一致（详“跳过与受限”）；**TEST_EXIT=1（R25.3 linker Bus Error，磁盘重链接波）** | `task18-r25-statics-bounded.log` |

建库双链：工作树内迁移链经 `LocalSqliteDbPlugin::new_file` + `run_sqlite_migrations` 实跑于每个装配（companion archive/artifacts 每次新文件）；`bcs-db-migrations` 目标全绿。MySQL 双方言：不适用——**本任务未改任何迁移或双方言面**。

### 跳过与受限（如实）

- **真实跨进程崩溃矩阵**（独立进程 Singlebox companion、共享持久 provider、停 Gateway/Provider、真 MySQL）：**blocked(P)**——P 未认证，none。同进程表达如下并已全绿：Worker 代际更替（takeover 全链）、Connector 死亡+D3 re-hint 唤醒接任（远端恰好执行一次）、对照（无 Supervisor 不得恢复）。**不得以此替代跨进程证据**。
- **`acked_connector_dies_without_hint` 的宿主声明形态**：同进程内进程死亡由宿主显式声明（`retire_connector`）声明“该 incarnation 已死”——这是同进程崩溃表达；跨进程的 liveness 驱动断言归 P 矩阵。D3 重建走真实 notify。D8 未接线（计划明示非验收前提，recorder 零调用已断言）。
- **resident-slot 语义缺口（如实记录）**：实测发现 External strict-license CAS 落在 resident Worker 已有 slot 上时，`control_head_conflict` 循环推迟（同进程内需 Worker 代际更替才消化新 envelope）。生产 P 装配需在 P 阶段决定 resident-refresh 语义；本任务不越权改 T7 引擎循环，companion 以“install→代际更替→接续”表达。**记录为 P/后续联合装配缺口，不掩盖。**
- **验证/隔离核实队列补发、TaskFeed 首次补发、Dispatch 目录跨容量**：跨进程结果 **blocked(P)**；同进程对照已在 Supervisor/装配 tick 下有功能证据（D3 re-hint + reschedule/notify 全走真实存储）。
- **quarantined 面的语义 GET/订阅**（T17 遗留 T9 依赖腿）：**本轮落地**（修复轮 1）：以真实核实队列停核产出真 Quarantined 记录并断言 GET WORKING 面/有界流/占用——上前披露的"T16 dispatch-reader read path"两腿均已在同进程装配上钉住；真实 cross-provider 语义归 P。
- **listing 的多行阳性续页**：严格轮次归档后 accepted 索引回收该行，严格下实时 cut 至多一行 live（多项队列在 T 系归档后被移除）——多行续页/Tamper/expiry 断言由 T17 `group_rest` 已有 8 向量覆盖（同 store/router 对）；本装配断言之分仅覆盖 fail-closed 与真实行/cut 形态。
- **R25**（如实）：静态腿（R25.1/R25.2）CI 仲裁项与既有基线一致差集，本轮修复未新增 trait/边界面；R25.3 `cargo test --workspace -- --list` 在本磁盘重链接波后以 **exit 1（linker Bus Error）** 止步——既定 CI 仲裁纪律维持，不以此冒充绿灯（完整日志 `task18-r25-statics-bounded.log`）。
- **API 门禁**：本轮公开 API 无破坏性变更；无 public-api 清单差异需求（决策记录级别：无）。
- 磁盘事件：`/home` 曾耗尽（workspace check 后 2.8 GiB）——删除 `target/debug/deps`（59 GiB，可重链）释放至 58 GiB；R25 重跑后仍 < 20 GiB 的规则按记录归档。

### GREEN 度量（数字来自代码与向量，非估计）

| 度量 | 值（来源） |
| --- | --- |
| 受理最大存储调用 | inbox `lookup`（观察性读，不保留）→ `admit`；WorkerTaskAdmission::recover 对照性读 ≤2 bounded reads；admit 失败路径每次失败的 inbox 写入都有 durable 记录（决策类写单条）。严格 admission aftermath 是幂等复访（`AdmitOutcome::Duplicate` 不再追加 Start entry）。 |
| 结果未知路径 | `OutcomeUnknown` 走 `abandon_strict_start`/`resolve_commit`，不烧回复重试；store `resolve_commit` 一次。commit 冲突走 `license_commit`→reread→recovery CAS 一趟。 |
| 分页/恢复扫描有界 | dispatch scan page ≤128 rows/页、稳定 key 游标 round 有界；Supervisor：concurrency ≤8、max_pending ≤64 jobs、page_entries ≤128、IO 超时 ≤2s；TaskFeed：page_entries ≤128、scan_pages ≤32、followers ≤128；listing：LIST_READ_CONCURRENCY=8、group cut ≤4096 行、页上限 profile v1 100 行、`pageSize>budget` 拒绝。 |
| 故障退避 | Supervisor retry_backoff 200ms 起、2^n 增、cap 5s、3 败弃 pending job；Connector send backoff 200ms→5s；verification lane：interval 2s、backoff min 2s、**backoff_max 经 `[group_a2a] wbackoff 配置接线（默认 60s，校验 backoff_max ≥ backoff_min）**。 |
| Retention/GC | cut-snapshot：cursor TTL 300s、过期拒绝续页；**reaper 未挂载（政策未定，ONLY-RECORD，不自动删除）**。dispatch 目录：角色 tick 每心跳一次受限批量 reclaim（`reclaim_dispatch_directory`）；held 索引按 claim 生命周期；deadline 历史/admission 记录保留（无自动删除）。 |

## 基线和证据边界（以下为历史记录）

- 当前分支工作基线：`a702256fc24611a97bbb622eed6d310fc0347a73`。
- dev 参考：`040a6267c40e866a09dcd79a9bea639503b6bca8`。
- #4245 静态核对版本：`c7187edcc99354f017f2e1a9bfcb3d6e88c15b96`，未参与本次编译。
- 新增 `bcs-dist-model::group_round` 候选类型、wire 测试和真实 CoordinationStore 上的 checkpoint 探查。底层使用现有测试专用 Model，不是生产 provider；重建 store 不是进程或持久服务重启。
- 没有修改旧 SessionSpec、RunnerInput、Service API、路由或生产存储算法。模型加法升级 0.1.0 → 0.1.1；候选结构不能被当成已冻结的运行时协议。

## 命令与结果

在 `apps/bcs` 下执行。完整临时日志保存在 gitignored `.superpowers/sdd/plan/`，下表记录可复现命令与实际结果。

| 命令 | 结果 |
| --- | --- |
| `cargo test -p bcs-dist-model --test group_round_wire`（实现前） | RED，exit 101，缺少 Admission / RoundInput / TerminalProof，符合计划允许的缺类型失败。 |
| `cargo test -p bcs-dist-model`（实现后） | 5 项新增 wire、5 项旧格式和 1 项 doctest 通过。 |
| `cargo test -j 1 -p bcs-dist-model -p bcs-dist-store -- --test-threads=1` | 审查修正后最终重跑：**97 passed、0 failed、0 ignored**，含 8 项 checkpoint 特征探查。初次恢复运行曾为 96 passed/1 ignored；后者已按下行审查意见改为普通基线测试，不能把这次 GREEN 解释为 D0 GREEN。 |
| `cargo test -p bcs-dist-store --test group_round_checkpoint_probe d0_gate_original_acceptance_survives_later_checkpoint -- --ignored` | 首次显式执行 RED，exit 101：推进 checkpoint 后原受理回执为 OutcomeUnknown。独立审查指出其不是正确的强制冻结门槛，现已改为普通基线特征测试 `baseline_receipt_lookup_after_restart_needs_retained_proof_protocol`；不再要求旧 API 承担历史查询。排队中的旧名称重跑已取消，未产生新测试结果。 |
| `cargo check --workspace --all-targets` | 最终以 `-j 1` 重跑通过，exit 0，覆盖审查修正后的全部 target。 |
| `bash scripts/ci/check-public-api.sh` | SKIP，exit 77，缺 cargo-public-api，不算通过。该脚本也不覆盖 model crate，且比较已提交版本。 |
| `cargo test --workspace` | 上一会话编译中断。恢复后 `cargo test --workspace -j 1 -- --test-threads=1` **失败，Cargo exit 101**：`bcs-bridge-cli --test register` 中 `desktop_registration_persists_socket_and_rejects_invalid_options_before_network` 在 `crates/tools/bcs-bridge/cli/tests/register.rs:173` 失败，提示 `Codex desktop detection failed: no safe, running app-server Unix WebSocket at the selected endpoint`；该目标 39 passed / 1 failed。不是全量 GREEN；默认 fail-fast，后续目标未全部执行。包装脚本 exit 0 不是 Cargo 成功。未调查该测试根因，也未修改本次范围外的 bridge 代码。 |

## 实际确认与尚未证明

1. 两个独立 CoordinationStore 实例对同一 checkpoint 版本提交不同候选，仅一个 Committed，另一个 CommitSeqConflict。这证明原语，不证明完整 GroupRoundRepo 的 BUSY/请求幂等行为。
2. 合法注册/claim/seal 后可创建初始 checkpoint；未注册的捏造 owner 无法创建。新严格入口在无活跃 owner 时怎样受理和唤醒尚未验证。
3. 当前 head 的提交回包丢失，可在已知 Sid 的条件下通过新 store 实例读回；换 owner 后旧提交被 Fenced。原型里的待派发字段不等于真实 Runner/Connector 已派发。
4. **仅复用现有提交回执不足以保留原请求证据。** 当前 `resolve_commit` 从当前 outbox 构造回执；推进 head 后旧身份返回 OutcomeUnknown。#4245 固定版本 `coordination/session.rs:296–319` 同样如此。基线反例只否定“该 API 单独足够”的假设，不是现有合同回归，也不是必须改变该 API 才能冻结 D0。真正的正向冻结门槛应通过选定的请求/冷证明保留协议，在 checkpoint 推进和 owner 交接后找回原受理；这条链路尚未验证。
5. **checkpoint 写入本身不提供未知 Sid 发现。** 只提交 checkpoint 后，实际 sched/active ZSet 为空。需要许可前登记可恢复发现意图或同等原子发现机制，并证明废弃准备不能成为下一代排队票。当前实验没有验证整个 admission 的发现链路。
6. **独立 ZSet 版本不能证明跨 Sid 覆盖。** A/B 都提交，延迟 A 索引、先写 B，索引有合法版本却仅含 B。缺覆盖屏障时不能返回貌似准确的 total。实验用的是存储原语，不是实现或否定了某个尚不存在的公共 List 协议。

## 冻结检查清单

> 本表为 S1.5 后的最终态；每行证据可追溯到 [S1.5 门禁与审查记录](#第五轮s15门禁审查与-d0-冻结记录) 或 S1.1～S1.4 各节。

| 要求 | 最终状态（S1.5） |
| --- | --- |
| 必填 scope/source/generation、输入顺序、原规范化依据与结果证据 wire | **已冻结**（bcs-dist-model 0.1.1，wire 测试 + 向量绿；generation 拒零代次，schema 拒不支持版本） |
| GroupRoundRepo、集中 conformance、typed Runner Start | **已冻结**（bcs-service-api 8.0.0；GroupRoundRepo 有 harness `group_round_repo_contract_tests` 与 conformance 入口且全套绿；RunnerInput::GroupStart 经 S1.4 消费者审计 + 全 workspace check 编译证明；生产 `admit` 落 store 原语归 T2） |
| 请求证据跨轮读回 | **已冻结（原型级）**：S1.1 请求登记/预留/接管 + 跨 Sid 重用测试绿；冷证明按 Tid 转存物化归 T4/T7（S1.4 缺口 #1，已指派） |
| 无 hint 自动发现 | **已冻结（原型级）**：S1.1 预登记目录先于许可 + 接管扫描测试绿；生产扫描合同归 T7 |
| 归档后未提交、Completed 后 Feed 故障、B 实际派发 | **已冻结（联合版本映射级）**：S1.4 真实关节映射测试（受理→CAS→Done→append→publish→跨 head/owner）绿；真实 Connector 全链 fault 窗口归 T6/T7；未应用 Feed 许可跨 head 缺口已指派 T7（S1.4 缺口 #2） |
| 两 Sid 的公共列表 cut | **已冻结（原型级）**：S1.2 双重采集 + 不可变快照 + 签名游标测试绿；生产物化索引/游标协议归 T4/T8 |
| replay_before_current_capabilities | **已冻结（原型级）**：lookup(Absent/Prepared/Accepted/Expired) + 当前授权先于重放测试绿 |
| oversized_intermediate_result_is_bounded_failure | **已冻结（原型级）**：S1.3 真序列化预算（B−1/B/B+1）+ 流式入站检测 + 统一停止屏障 + 有界失败收尾测试绿；生产 IO/停止屏障归 T6/T7 |
| 权限矩阵、撤权点、分页期限、默认/非默认预算 | **已冻结（候选参数转正）**：GroupRoundGrants（New=published+invoke；Replay/Read/Subscribe=read；Cancel=read+cancel）与 RoundBudgetProfile::v1()（入站/公开帧 8 MiB、Feed 事件 128 KiB、批 256 KiB、分页 100 行、游标 300 秒、历史 0/≤100）；failure-reason ≤1 KiB **明确未冻结**，如实登记 |
| 真实 provider、跨进程恢复 | **非 D0 必需**（Stage 2+ 范围）：原型与联合映射不作替代证据；T2 生产 GroupRoundRepo + provider GREEN（阻塞于外部 P）、T9 跨进程故障矩阵按 S1.4 结论放行开工 |
| 公共 API 门禁 | **已修复为真实差异门禁并通过**（见 S1.5 记录；不再以 SKIP 或接口导出替代） |

## 第二轮 T1 正向原型（执行中）

在前述基线探查之后，新增以下代码；上文“不修改 Service API”等描述仅记录第一轮，不再代表当前工作树：

- Service API 8.0.0：新增 `RunnerInput::GroupStart`（exhaustive match 的兼容性变化）、GroupRoundRepo 和集中 harness。新增可信授权矩阵、候选预算 profile、有界读取及来源分域向量。
- `group_round_support/`：仅测试使用的正向协议原型，真实 CoordinationStore 负责 checkpoint/owner CAS，测试 Model 负责底层存储；不安装到运行时。
- `group_round_protocol` 与 `group_round_races`：10 项通过，覆盖预登记发现、受理丢响应、换 owner、A/B 轮次、归档未提交、持久保留旧请求、两段发布、列表双重收集、当前授权先于重放、能力变化不阻断重放及跨 Sid 同消息竞争。
- 强制 yield 暴露跨 Sid 同消息双受理（RED）；新增 scope 内稳定请求预留后 GREEN。丢响应注入调整到 checkpoint commit 前，避免错误地只测试准备写入失败。
- `group_round_contract`：4 项通过；`group_round_vectors`：2 项通过。9 MiB 输入在 8 MiB+1 读取后拒绝；小最终候选不能豁免中间结果；这仍不是 Connector/Worker 全路径超限验收。
- 第一轮第二阶段受影响套件（向量新增前）：508 passed、0 failed、1 既有 ignored doctest。全 workspace all-target check 通过。
- `cargo-public-api 0.52.0` 已安装在 gitignored 工具目录；固定 `nightly-2025-11-30` 可以直接提取 model 与 Service API。现有 `check-public-api.sh` 使用不支持的 `--diff-git-checkouts` 且吞掉错误，返回 0 **不算有效门禁通过**。需要修正门禁/支持版本并对工作树做真实比较。
- 桌面注册失败根因：当前 umask 002 使测试创建的 Unix socket 可被组写，安全检查拒绝。仅本命令设 `umask 077`，原失败用例通过；未放松安全检查或修改 bridge 代码。全工作区在该权限下单并发重跑，并以磁盘 20 GiB/可用内存 2 GiB 阈值保护。

原型有明确容量上限且不自动清理，但仍需检验请求预留的竞争/废弃语义、真实发布效果及物理部署依赖。正向测试通过不自动把 D0 标为冻结。具体协议选择及候选参数见 contract-decisions.md 的 T1 原型节。

## 第三轮：独立审查修复与最新结果

最新受影响套件：`cargo test -j 1 -p bcs-dist-model -p bcs-dist-store -p bcs-service-api -p bcs-test-support -- --test-threads=1`，**520 passed、0 failed、1 既有 ignored doctest**（`t1-reviewed-suite.log`）。这替代前面的 508/97 等阶段性计数，不代表真实联合版本验证通过。

已完成的审查修复：

- 同请求并发在 Busy 判断前、预留失败和 checkpoint CAS 冲突后重新读权威记录；相同请求回 Replay，不误报 Busy。
- 请求预留保存首次请求、预期 checkpoint head 和 owner，区分 prepared/abandoned；未决预留不等于 Conflict。废弃先 seal 或推进精确 head 栅栏，再 CAS 标 abandoned，旧准备不可复活；失败竞争者的消息键可重用。接管 prepared 请求保持原 generation 与规范化依据。新增测试覆盖预留后未受理、两个预留竞争、迟到提交、跨 Sid 重用和能力变化后接管。
- 派发副作用前提交 dispatch_intent；sink 已写但 ack 未提交时仍须等待精确 RoundKey 的停止证据。Get 重新计算 result 和 closure 实际正文摘要；错误停止身份、损坏结果拒绝。
- Feed 从 checkpoint 布尔标记改为独立 CAS 文档：权威终态提交固定两份效果所需的事件/快照正文、摘要、稳定 event ID、source_commit 和前驱。失去执行 ownership 的实例仍只能凭原许可发布；篡改、越序、未 append 就 publish 均被拒绝。append 后换 owner、再恢复快照及重复发布测试通过。许可/引用不回收，因此不宣称已验证生产 GC。
- 列表双重采集增加确定性交错：采集投影后暂停，另一个任务提交并更新索引，再恢复校验；拒绝混合 cut，下一次完整查询读到新状态。
- 实际同步 IO 从 Service API 移到 test-support 作为参考实现；API 只保留预算/授权合同。新增 `lookup(GroupRequestIdentity)`，区分 Absent/Prepared/Accepted/Expired，消费者可在当前能力校验前读取首次规范化依据。

上轮审查指出的证据缺口不能靠这些原型测试自动消失：仍缺真实 #4245 联合链路、冷证明迁移/压缩、完整分页游标和真实编码/停止屏障验收。固定 scope 测试不是任意租户隔离的充分证据；有限原型不代表生产持续容量。

### 资源和门禁限制

- `umask 077` 的全 workspace 重跑在编译阶段被资源保护主动 SIGTERM，exit 143；当时磁盘余量 19.7 GiB、可用内存 11.7 GiB。没有全量通过结论，未删除共享缓存。
- cargo-public-api 工具安装成功，固定 nightly 的 model/Service API 接口导出成功；旧 CI gate 不兼容当前工具并吞掉失败，尚未得到有效 baseline/worktree API 差异门禁。没有用工具的 in-place checkout 模式覆盖当前未提交修改。
- `git diff --check`、修改文件 1,000 行限制及本地文档链接检查通过；审查修复后的 `cargo check --workspace --all-targets -j 1` 最终通过（exit 0，`t1-reviewed-check.log`）。

## 范围裁决

本次保留可独立验证的候选类型和原型，未为了消除反例而改造生产 SessionStore、导入整个未合入 PR 或执行 T2/T7。T1 因原型未通过不能标完成；D0 保持未冻结。下一步仍是 T1 的物理协议闭环，而不是宣布所有后续实施已放行。

访问成本方面，测试 Model 没有真实 DB 往返；不能从两写者/两 Sid 实验推断吞吐。后续方案须明确每批上限、竞争重试、发现目录扫描和冷证明读写成本，不扫描无界历史。恢复执行时复用已有 Cargo target，不清共享缓存，编译并发与测试线程均限制为 1。资源监测每 15 秒采样，观测到的最低可用内存约 11.5 GiB、最低磁盘余量约 35.9 GiB，未触发 2 GiB/20 GiB 告警阈值；采样结果不代表精确峰值。

## 第四轮（S1.4）：联合版本固定与合同映射验证

### 联合版本 pin（实际合入版本重验）

| 命令 | 结果 |
| --- | --- |
| `gh pr view 4245 --repo inclusionAI/Avernet --json number,title,mergeCommit,state,mergedAt,headRefOid` | `MERGED`，标题 `feat(bcs): connect Gateway to recoverable Worker runtime`，head `6defec198ac766b92a0fc68c6d097010d0e43963`，mergedAt `2026-11-24T09:01:25Z`，dev 侧提交 `64a7afe3c64f88792d7f44b5f695da2ad0d865ae`。plan 原文的 c7187edcc9"在审"状态已被超越，本节按其实物重验 |
| `git log --oneline -5 origin/dev`（于仓库根） | `d806668442 fix(bcs): forward catalog label normalization through the metrics wrapper (#4338)` 为当前 dev tip；本分支已 rebase 于其上 |
| 实体存在性（矩阵行基础） | `ls`(及直接编译通过)证实 `crates/services/bcs-dist-store/src/archive/sql.rs`、`src/admission.rs`（worker crates）、`src/processor/{decision,transitions,effects,prepare,drive,lifecycle}.rs`、`src/task_feed/{storage,append,publish,terminal,follow,reader,retention}.rs`、`src/inbox/{producer,validation}.rs` 均在 dev 合入后的工作树 |

### 矩阵核对命令（git log/grep 粒度）

| 命令 | 结果 |
| --- | --- |
| `git log origin/dev --format='%h %s' -- crates/services/bcs-dist-store/src/archive/sql.rs`（于 `apps/bcs`） | 起始提交 `18a6301926 feat(bcs): persist immutable distributed results and blobs`（2026-11-19） |
| `git log origin/dev --format='%h %s' -- crates/services/bcs-dist-store/src/task_feed/storage.rs` | 起始 `c6f7b45e62 feat(bcs): implement committed task feed and logical retention` |
| `git log origin/dev --format='%h %s' -- crates/services/bcs-dist-store/src/inbox/validation.rs` | 起始 `6a1c847d50 feat(bcs): add reliable inbox admission and bounded recovery` |
| `grep -n "PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES" crates/contracts/bcs-dist-model/src/input.rs` | 行 68 处定义 8\*1024\*1024，与 `RoundBudgetProfile::v1().inbound_bytes` 等值（S1.3 已钉） |
| `grep -n "MAX_EVENT_BYTES" crates/services/bcs-dist-store/src/task_feed/storage.rs` | 行 4：128\*1024；批 256 KiB 见 `task_feed/append.rs:12,17` |
| `grep -rn "admit_connector" crates/` | 定义于 service-api `port/repo/distributed/inbox.rs:54`、实现在 `bcs-dist-store/src/inbox/producer.rs:285`、消费者 `bcs-dist-connector/src/report.rs:102` 与其 lifecycle/recovery 测试 |
| `grep -rn "RunnerInput::" crates/ --include="*.rs"` | 8.0.0 全部消费者：processor `prepare.rs`（构造，不穷尽匹配）/`risk.rs`（序列化进 attempt 摘要）、test-support 契约 harness `services.rs`（透传）、`bcs-dist-test-runner/src/lib.rs`（if-let 非穷尽）、worker 测试集（let-else/if-let/`other =>`），无一需要为 `GroupStart` 加匹配臂；唯一显式 `GroupStart` 臂在 `bcs-service-api/tests/group_round_contract.rs`（本分支新增测试，非生产消费者） |

### 可运行合同兼容测试

新增 `bcs-dist-store` 集成测试目标 `tests/group_round_joint_mapping.rs`（716 行；选择 dist-store 而非 test-support 契约层的原因：它要把 CoordinationStore、StoreTaskFeed、StoreInboxProducer、DbArchiveStore(SQLite) 等真实关节装进同一 KeyLayout/存储命名空间，属单 crate 集成装配；test-support 契约层是跨 provider 复用的 port conformance，不适配跨关节链路）。两层夹具不用 S1.1 的原型 Model 文档（不复用 `group_round_support`），只用测试 Model 作底层插件。

| 命令 | 结果 |
| --- | --- |
| `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12 cargo test -p bcs-dist-store --test group_round_joint_mapping -- --test-threads=1` | **2 passed, 0 failed**。`joint_admit_terminal_feed_readback_survives_head_advance_and_takeover`：真实 Inbox 受理引导（spec+initial task）→ `acquire` → 受理 CAS（积极者 `Committed`，同 head 异 id → `CommitSeqConflict`，同 id 异内容 → `ContentConflict`）→ `AwaitingResult`+SaveResult CAS → `Done`+Append/Publish 双许可单次 CAS → 真实 feed 先 publish 拒绝（`snapshot_append_incomplete`）→ append `Appended`/重放 `AlreadyApplied` → publish `Published`/重放 `AlreadyApplied` → `resolve_commit` 丢回包读回 → `restore`/`get_final` 无 head 读回 → 下一轮同源立即受理（无第二次解锁）→ 换代接管：已应用许可经 Completion 跨 head/跨 owner 重放 `AlreadyApplied`，后继 `acquire` seal 之后旧 owner 迟到写 `Fenced` → 轮 2 提交成功（`preserve_frozen_results` 强制保留 A 的冻结终态）→ **断言缺口**：head 推进后从未应用的 CommittedBatch 许可被 `EffectVerifier` 拒绝（`unproven_effect`），已应用者仍可重放。`gettask_final_without_remote_event_id_reaches_the_real_inbox_once`：真实 `admit_connector` 接收 `remote_event_id: None` 的 GetTask Final（身份 `key_input_id(key_digest)`，state=completed）→ `Accepted` → 换 holder 异字节重报 `Duplicate` → 真实 inbox 流恰好 1 条键事件 → 同 key 异 state `ContentConflict` |
| `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12 cargo test -p bcs-dist-store -p bcs-dist-model -p bcs-service-api -p bcs-test-support` | **729 passed、0 failed、1 既有 ignored doctest**（727 基线 + 2 项 S1.4 新增；此前 4 crate 上的 S1.1～S1.3 全绿套件保持绿） |
| `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12 cargo check --workspace --all-targets` | **exit 0**，`Finished dev profile in 1m 11s`——Service API 8.0.0（含 `RunnerInput::GroupStart` 破坏性穷尽加法）的工作区级消费者兼容证明 |

**TDD 说明（无独立 RED 阶段）：** 映射目标均为已合入的生产原语，无生产代码变更可供先 RED；两项测试自合同映射表直接写出。首轮执行中两处断言因真实合同分类被修正后通过——同 head 竞争 CAS 的输家按提交 id 区分（先误写 `CommitSeqConflict`，学得同 id 异内容是 `ContentConflict` 且回包语义）；旧 owner 迟到写在 seal 前仍 `Committed`（学得 fence 生效于 `acquire`）。这两次修正本身即合同级映射证据。

**判定：** 映射成立，无未解决接口冲突；两处缺口（closure 证据按 Tid 物化、从未应用 Feed 许可跨 checkpoint）已在 contract-decisions.md 的 S1.4 节登记并指派 T4/T7——不是映射失败，是其本来就归后续生产阶段的合同缝。未运行真实 Connector 端 `rules.rs`→report 全链归 T6；跨进程 provider/联合角色归 P/T9。

### 磁盘记录

s14 期间 `/home` 曾降至 0.8 GiB 可用（工作区 target 累积的无关测试可执行文件 ~45 GiB）。`cargo sweep --time 3` 可清部分过小（全部产物当日复用）；按"测试可执行文件可按需重链、编译对象保留"的原则，移除了与四个受影响 crate 无关的 >50 MiB 陈旧测试可执行文件，释放后 df 显示 46 GiB 可用；后续所有 cargo 命令照常复用既有 target，未执行 `cargo clean` 或删除编译对象缓存。

## 第五轮（S1.5）：门禁、审查与 D0 冻结记录

本节是 Stage 1 收口轮的完整命令/结果/限制台账。日志均在 gitignored `.superpowers/sdd/plan/` 下；工作目录 `apps/bcs`，受影响命令统一带 `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12`（资源应急降级处已注明）。

### #4245 合入形态更正（复核陈述）

#4245 并非 squash 单提交合入：dev 收到的是 rebase 式提交序列（约 17 个提交，comitter 时间同秒），`64a7afe3c6` 是该序列的 tip / mergeCommit；上文的“squash 合入”表述不成立，以本行为准。`git log 64a7afe3c6 -22 --format='%h %ad %s'` 显示序列跨 2026-11-19～11-24 的个体提交（inbox admission → worker runtime → single-document admission → dispatch slot 修复等）。

### 受影响模块套件与全量检查

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12 cargo test -p bcs-dist-store -p bcs-dist-model -p bcs-service-api -p bcs-test-support` | **729 passed、0 failed、1 既有 ignored doctest**（96 个 test target 全 ok，无 FAILED）——与 S1.4 计数一致 | `task-5-gate-1-affected-suite.log` |
| `CARGO_PROFILE_DEV_DEBUG=line-tables-only CARGO_BUILD_JOBS=12 cargo check --workspace --all-targets` | **exit 0**，20.81s Finished | `task-5-gate-2-check-all.log` |

### 全工作区测试（真实全量，umask 077 + --no-fail-fast）

命令：`(umask 077 && CARGO_PROFILE_DEV_DEBUG=line-tables-only cargo test --workspace --no-fail-fast)`（编译期曾按资源纪律降级 -j 4/-j 2；测试执行期全量运行）。日志 `task-5-gate-5-workspace-test.log`（含三次接续重跑的完整拼接输出）。

- **总量：929 个 test target：9616 passed、3 failed、74 ignored**；`--no-fail-fast`，cargo exit 101 仅由下述 3 个预期红产生，无编译错误、无中止目标。
- **预期 host RED 逐项兑现（不修改的生产拒绝）**：`-p bcs-collaboration-store --test mysql_store` 中 3 项因 host SQLite 3.26 低于 bundled 而失败：`branch_skip::sqlite_branch_skip_contract_and_environment_isolation`、`failure_contract::sqlite_failure_action_migration_preserves_legacy_rows_and_replays`、`history_contract::sqlite_history_acceptance_and_terminal_replay_contract`。此为既有已知 host 基线红、CI 仲裁，不修 bundled。
- **预期 host RED 之一未复现**：控制器预告的 `provider_initial_managers` 5 项 host 红在本轮**全部绿**（`--test provider_initial_managers` 12+6 passed；MySQL 链 1 项 ignored 要求 `BCS_TEST_MYSQL_URL`），如实记录，不解释为 CI 状态变化。
- **bcs-bridge-cli register**：umask 002 下 Unix socket 组可写曾被安全检查拒；本轮 umask 077 全量重跑该目标绿，未放松安全检查、未改 bridge 代码。
- **无其他任何失败**。ignored 74 = 测试自身的 env 门槛（19 处 `requires BCS_TEST_MYSQL_URL`）等既有标记，非本轮产物。

### API 差异门禁（修复后第一次真实基线↔工作树比较）

- **修复内容**（`scripts/ci/check-public-api.sh` 全量重写）：`--diff-git-checkouts` 已被 cargo-public-api 0.52.0 移除，且 0.52.0 的 `diff refA..refB` 会对当前树做 in-place `git checkout`——脏工作区禁用。改为：baseline 侧用 `git worktree add --detach <tmp> <merge-base>`（不动主树），worktree 侧用当前树（含未提交修改），两侧各取 `cargo public-api -p <crate>` 清单后统一 diff；工具任一侧失败都 FAIL 并展示 stderr，无吞错；缺目录即 FAIL（不再静默 continue）。覆盖 bcs-config-api、bcs-protocol、bcs-service-api，另单独检查 bcs-dist-model 的版本与序列化 wire 兼容。
- **配置实例的路径更正**：bcs-protocol 实际位于 `crates/contracts/bcs-protocol`（CLAUDE.md 服务树描述过时），首轮运行曾静默跳过，修复后 FAIL-loud 并全量覆盖。
- **工具安装与固定**（不进 CI 默认工具链）：`rustup toolchain install nightly-2025-11-30 --profile minimal`（rust-lang.org 直连超时，按 singlebox 惯例改用 USTC rust-static 镜像）+ `CARGO_INSTALL_ROOT=<repo>/.superpowers/tools cargo install cargo-public-api --version 0.52.0 --locked`；运行经 `RUSTUP_TOOLCHAIN=nightly-2025-11-30` 固定；均写入脚本头注释。工具/nightly 缺失仍 SKIP(77)，真实比较必须真 PASS/FAIL。
- **真实结果（baseline=origin/dev merge-base `d806668442`，exit 0）**：
  - bcs-config-api：清单 4599 行**无变化**；bcs-protocol：26824 行**无变化**。
  - bcs-service-api 7.1.0→8.0.0：**+289 项、0 删除**，新增面 = `RunnerInput::GroupStart`（含 `input: RoundInput`）+ `pub mod port::repo::distributed::group_round`（GroupRoundRepo、GroupRoundGrants、GroupRequestRecord、RoundBudgetProfile 等）。清单级无删除即无 listing 级破坏；8.0.0 major 按穷尽 match 保守升位，S1.4 消费者审计已证明树内全部消费者非穷尽（无一处需加臂）、`cargo check --workspace --all-targets` 绿。
  - bcs-dist-model 0.1.0→0.1.1：**+753 项、0 删除**（serde 序列化实现为主）。旧格式 wire 回归 `cargo test -p bcs-dist-model --test group_round_wire` **5 passed** 真实运行通过（序列化兼容以真实 serde 往返为准）。
  - 版本传播分析见 S1.4 节（`RunnerInput::` 全消费者 grep 结论在“矩阵核对命令”表）；gate 只在有 PR 正文文件时才强制 Propagation analysis 段，本地无该文件未触发。
  - 完整 gate 输出与两侧清单存 `.superpowers/sdd/plan/task-5-gate-3-public-api.log` 与 `task-5-api-gate-artifacts/`。

### R25 conformance 门禁（吞错修复后）

- **吞错修复**（`scripts/ci/check-r25-conformance.sh`）：R25.3 的 `cargo test --workspace --no-fail-fast -- --list 2>/dev/null || true` 改为 stderr 落 mktemp 文件、cargo 失败即 FAIL 并打印 stderr 尾部——与 API 检查器同一原则（图谱中第二处吞错）。
- **真实 exit 1**（`task-5-gate-4-r25.log`）：R25.1 报约 90 个 trait 缺 harness、R25.2 报约 110 个边界 impl 缺 conformance 入口 + 9 个 conformance 文件未走集中 harness。**逐项核对证明全部为 dev 预存在基线红**（CI 仲裁、不在本分支修外国代码）：
  - dev 工作树（`d806668442`）与分支工作树的 R25 扫描集合 diff：trait 集合 dev 238 = 分支 238 + **仅 `GroupRoundRepo`**（本分支唯一新增 trait；harness `group_round_repo_contract_tests` 存在且有 conformance 入口 `conformance_group_round.rs` 调用，全套测试绿 = R25.1 唯一新增项达标）；`impl X for Y` 边界集合 diff **完全相同**（分支零新增生产边界 impl）。
  - 控制器点名的 5 项（SessionFileServiceImpl、OrganizationCore、CallerConfigurationDeliveryGate、ProviderBotEvents×2）为其中子集（`git grep` dev 证实命令输出见 S1.5 过程记录）。
- **R25.3 中止与限制（如实）**：`cargo test --workspace -- --list` 在本机 4 次执行均因 host 磁盘耗尽（ENOSPC）于重链接尾段失败退出 101——直接受本轮资源事件影响（见下节），4 次完整 stderr 存 `task-5-r25-attempt-*.log`。语义等价证据：同晚完成的全工作区测试本身即证明 libtest 发现了全部 conformance 目标（929 target 实际运行，含 `conformance_group_round`、`group_round_*` 全部目标）。该项不掩盖、不降格，以“已尝试+基础设施中止+等价证据”记录；CI 侧可继续仲裁。
- R25.2 的第一项检查（conformance 文件调用集中 harness）有一个既有 dev 预存在红集（9 文件），与本分支无关。

### 架构门禁汇总（architecture-checks.yml 仅 gate main；照常逐项执行记录）

单跑构成脚本（合并器 arch-check.sh 自身在磁盘应急期间不再触发其内嵌全量 `cargo --list`/public-api 重跑，两者已由上文独立真实运行覆盖）：

| 脚本 | 分支结果 | dev 同位对照 |
| --- | --- | --- |
| check-port-purity / check-store-boundaries / check-interceptor-chain / check-waivers / check-protocol-compat / check-forbidden-symbols | PASS | PASS |
| check-deps（DEP-6 bcs-protocol→bcs-domain 不在允许列表） | FAIL | 同 FAIL（dev 预存在） |
| check-import-rules（LINT-1） | FAIL | 同 FAIL（dev 预存在） |
| check-trait-naming（LINT-4，非 Service 后缀 trait） | FAIL（11 项） | 同 FAIL 11 项（dev 预存在） |
| check-config-validation（CFG-1 bootstrap config_loader 单测） | FAIL（真实 cargo 运行结果） | 本分支 diff 无 bootstrap 文件；红为预存在（dev 同位未复跑：需重编译 dev 工作树，资源不允许） |
| check-pr-gate | SKIP 77（非 PR 环境，与既往一致） | — |
| check-baseline-not-growing | SKIP 77（无法解析 origin/refactor_arch_bcs，非 PR 环境） | — |
| check-public-api.sh / check-r25-conformance.sh | 见上两节各自真实结果 | — |

### 资源事件与磁盘台账（如实）

- 会话起 `/home` 44 GiB；全工作区编译期跌穿 20 GiB 保护阈值（最低采样 0 GiB，多次 100% 满盘并伴 ENOSPC）。全量 `cargo test --workspace` 在 line-tables-only 下测试可执行文件总量 ~48 GiB + 45 GiB 期初产物，逼近 /home 78 GiB 物理上界。
- 处置：按授权仅删可重链测试二进制，不动共享缓存/target 对象层、未 cargo clean；错误类别复盘——(a) 早期按“同名留最新”的误判去重删除了运行必需的 test 目标件，触发后续每次 `cargo --list` 大规模重链接波与反复 ENOSPC（教训如实保留）；(b) 后续改为对照完整运行日志精确定位仅删“未出现在运行清单中的孤儿件”才稳定。过程中另发现重叠监控脚本误杀进行中运行一次，一并记入台账。
- 全程 `df/free` 15 秒采样（`task-5-resource-monitor.log`）；所有 cargo 均复用既有 target。
