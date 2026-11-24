# T1 验证记录

状态：**T1 部分完成，D0 未冻结。** 本次没有执行 T2～T9、合并 #4245、启用服务或提交代码。

## 基线和证据边界

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

| 要求 | 当前状态 / 解除条件 |
| --- | --- |
| 必填 scope/source/generation、输入顺序、原规范化依据与结果证据 wire | 已有候选和测试；generation 用 NonZeroU64，拒绝零代次；schema 显式拒绝不支持版本。描述符本身不授予权限。 |
| GroupRoundRepo、集中 conformance、typed Runner Start | 未传播：遵守 T1“原型通过评审后再同步”的门槛。不能以无真实消费者的 trait 假装冻结。 |
| 请求证据跨轮读回 | 阻塞：明确 key、checkpoint 授权、转存读回、压缩和墓碑顺序，覆盖所有故障窗口。 |
| 无 hint 自动发现 | 阻塞：明确有界目录/意图和唯一写者，再验证消费者自动发现，不由测试直接传入 Sid。 |
| 归档后未提交、Completed 后 Feed 故障、B 实际派发 | 未运行：当前未包含 #4245 processor/TaskFeed/archive 联合实现，须提供配套联合版本或明确移植范围。 |
| 两 Sid 的公共列表 cut | 只验证朴素独立索引方案不足；覆盖屏障与快照保留方案尚未实现验证。 |
| replay_before_current_capabilities | 只验证 wire 保留原记录；授权、请求索引、首次规范化恢复的应用行为尚未实现。 |
| oversized_intermediate_result_is_bounded_failure | 未运行联合 Connector/Worker 路径；限额常量/序列化测试不能代替有界 IO 和停止事实分离。 |
| 权限矩阵、撤权点、分页期限、默认/非默认预算 | 未冻结，需要与上述原型及真实编码一并验证，不能为勾选任务随意宣布默认值。 |
| 真实 provider、跨进程恢复 | 未执行；模型和重建消费者实例不作替代证据。 |
| 公共 API 门禁 | 缺工具；单独 model 兼容检查是加法声明和旧 wire 回归，不替代该门禁。 |

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
