# Bridge Plugin Retention Reclamation Execute Plan

> 日期：2026-10-19
> 状态：T0–T5 本地实现与验证完成；T6 发布事项未执行，内部发行验证受既有锁文件问题阻塞
> 验证记录：[Validation](2026-10-19-bridge-plugin-retention-reclamation-validation.md)
> 对应 [Spec](../specs/2026-10-19-bridge-plugin-retention-reclamation-design.md)
> 基准：`25e8027156d807335e24dca7b8d4c413427113e5`

## 0. 执行范围与规则

**Goal:** Plugin 成功消费后回收前缀，避免累计已发送事件耗尽配额；按用户追加确认将数量上限由 8192 提高至 16384。
只回收现有传输语义下的已消费记录；真实积压保护与 Gateway 全量重放保持不变。

**非目标：** 不提高 32 MiB 字节预算或并发上限，不取消 thinking 计费，不引入 ACK/磁盘存储/增量合并/
批量调度，不修改引擎生命周期，不直接更新工单或发布生产版本。

- 先读仓库根及 BCS 的 `AGENTS.md`、`CLAUDE.md` 和架构契约规范。
- 不运行 `cargo fmt` / `cargo fmt --all` 或全局格式化。
- 新增/修改 Rust 源文件最终不超过 1000 行；当前 `run.rs` 已超限，需要局部职责拆分。
- 不提交真实 trace、会话内容、内部端点、运行状态或凭据。测试使用合成数据。
- 不自动清理用户修改，不自动 commit/push/发布；各阶段完成后保留可审查 diff。
- 本计划顺序执行即可，不要求并行代理或额外工具技能。

### 命令与路径约定

下文所有 `cargo` 命令都在 **公共 BCS workspace 根目录** 执行：

```bash
# 从 OCB 根进入：
cd submodules/avernet/apps/bcs
# 若独立检出 Avernet，则进入 apps/bcs。
```

文件清单均相对于该 BCS workspace 根。依赖齐备时可增加 `--offline`；不得为
通过测试更新锁文件或静默跳过失败。依赖/宿主环境阻塞需记录与产品回归分开。

## 1. 任务依赖与交付切分

```text
T0 基线和调用点核对
  → T1 现状约束与失败测试
  → T2 Buffer 封装（先保持现有行为）
  → T3 区分 Plugin/Gateway 交付对象
  → T4 成功消费后的回收接线
  → T5 诊断与完整回归
  → T6 发布前验证和结果归档
```

T2/T3 为受控重构，最终行为必须由 T4 和整体验收证明。不要把“新队列能 pop”
单独当作问题修复；所有步骤完成前不得宣称 Plugin 的累计上限问题已解决。

## T0. 确认基线、接口与真实限制

**读取：**
- `crates/tools/bcs-bridge/core/src/{run,plugin,runtime,webhook,encoder}.rs`
- `crates/tools/bcs-bridge/core/tests/{plugin_websocket,e2e_webhook,encoder_contract}.rs`
- `crates/tools/bcs-bridge/qwenwork/src/tests.rs`

- [x] 记录源码提交与工作区状态，确认没有需要保留的并发修改。
- [x] 全仓检索 `RunHandle`、`RuntimeReply::Run`、`buffer` 的直接调用者，列出 Rust API 影响。
- [x] 确认 Plugin route 在 pending 期间不会过期，重连不重置 cursor。
- [x] 确认 V2/V3 `encode(None)` 的合法过滤行为及 encoding-error 清理路径。
- [x] 跑现有 run 单测和下列协议基线，保存实际结果；不要把文档中的 17 passed 当作新结果。

```bash
cargo test --locked -p bcs-bridge-core --lib run::tests::
cargo test --locked -p bcs-bridge-core --test plugin_websocket \
  reconnect_delivers_offline_final_without_restarting_engine -- --exact
cargo test --locked -p bcs-bridge-core --test e2e_webhook \
  duplicate_send_reattaches_with_replay -- --exact
cargo test --locked -p bcs-bridge-core --test encoder_contract
```

**完成条件：** 有基线、调用点清单和失败分类，修复范围仍限 Spec FR-1～FR-8。

## T1. 先建立回归与契约测试

**新增/修改：**
- `crates/tools/bcs-bridge/core/src/run_buffer_tests.rs`（新 buffer 的单测模块）。
- `crates/tools/bcs-bridge/core/tests/plugin_retention.rs`（新的聚焦集成测试）。
- 必要时新增局部 test-support helper；不复制整套 WebSocket fixture。

- [x] 建立正常消费的长流回归：累计至少 20,000 条，消费与生产交错，最终应成功。
- [x] 为样本 A/B 构造相同事件类型数量的合成输入，末尾再增加正常事件与 final。
  不复制原始正文，不假设原故障时的 cursor 数值。
- [x] 加入无消费时仍在 16384 条/32 MiB 字节预算触发保护的对照，防止通过删掉保护“修好”。
- [x] 为发送成功、失败、超时、挂起后取消设计可控 sender，用于精确断言提交边界。
- [x] 记录长流在旧实现的失败：累计达到上限但已完成消费的记录未被释放。
- [x] 保留现有数量超限和 raw 字节计费测试；它们的无消费场景在修复后仍应通过。

**测试方式约束：** 单元长流不做 20,000 次同步入队后才消费；集成网络测试也不能
把“关闭对端 socket”误认为本地下一次 send 必然失败。长流可采用 mock sender/
确定性调度；真实 WebSocket 测试覆盖握手、重连和 wire 行为。首期不通过改生产
发送调度或降低默认配额加速测试。

**完成条件：** 新测试能稳定暴露累计保留缺陷，与真实积压保护明确区分（AC-01、04、06、12）。

## T2. 封装 Buffer，先保持既有行为

**新增：**
- `crates/tools/bcs-bridge/core/src/run_buffer.rs`
- `crates/tools/bcs-bridge/core/src/run_buffer_tests.rs`

**修改：**
- `crates/tools/bcs-bridge/core/src/lib.rs`
- `crates/tools/bcs-bridge/core/src/run.rs`
- 必要时将 `run.rs` 原有测试迁至 `core/src/run_tests.rs`。

- [x] 引入 `VecDeque<RetainedRecord>`，记录完整 `RunEvent` 与入队计费大小。
- [x] 统一管理 `retained_bytes`、`released_through` 和 `last_emitted_seq`。
- [x] 将计费、限额判断、追加与回收封装为短锁内的受控操作，不持锁网络 await。
- [x] 实现 `commit_delivery_through`：相同/落后提交不重复扣费，未来/跳跃提交拒绝。
- [x] 正常 seq 与队列长度解耦，清空后继续从原序号追加。
- [x] 对错误余量记录也保存计费值，保证普通记录和额外终止错误回收都不下溢。
- [x] Plugin 读取以序号范围定位，落后于回收下界明确报错；不静默跳过缺口。
- [x] 保持 Gateway snapshot/subscribe 原子性，暂不对任何模式自动开启回收。
- [x] 不在每次 pop 时 shrink；在终态清理按既有策略释放容器，记录容量与计费区别。
- [x] 抽出必要职责/测试，使所有新增或修改 Rust 文件不超过 1000 行。

**新增单测断言：** AC-02、03、06、07、08、11，以及不重算序列化大小的计费一致性。

```bash
cargo test --locked -p bcs-bridge-core --lib run_buffer
cargo test --locked -p bcs-bridge-core --lib run::tests::
```

若原测试模块迁移，更新过滤器并确认输出中的实际测试数；零测试不算成功。

**完成条件：** 回收原语已验证，旧协议测试仍通过，但此阶段不宣称已接通投递回收。

## T3. 显式区分 Plugin 与 Gateway 交付对象

**修改：**
- `crates/tools/bcs-bridge/core/src/runtime.rs`
- `crates/tools/bcs-bridge/core/src/plugin.rs`
- `crates/tools/bcs-bridge/core/src/webhook.rs`
- 直接匹配 `RuntimeReply` 的调用者及测试，含 QwenWork。

- [x] 使用显式交付 enum（或同等清晰的强类型接口）表达 Plugin 与 Gateway。
- [x] run 创建时固定保留策略，不允许运行中在 Plugin/Gateway 间切换。
- [x] Plugin 新 run 与同 ID 重试都不创建 Gateway forwarder。
- [x] Gateway 仍先订阅再启动引擎，同 ID 重试仍获得完整历史与实时跟随。
- [x] 模式不匹配显式返回错误，不引入 panic、伪造空流或可空字段掩盖错误状态。
- [x] 更新全部 Rust 调用点，保持 HTTP/WS wire schema 不变。

```bash
cargo check --locked -p bcs-bridge-core -p bcs-bridge-app \
  -p bcs-bridge-cli -p bcs-bridge-qwenwork
cargo test --locked -p bcs-bridge-core --test e2e_webhook \
  duplicate_send_reattaches_with_replay -- --exact
cargo test --locked -p bcs-bridge-qwenwork
```

**完成条件：** AC-10、13 通过，没有 Plugin 隐藏读取者，Gateway 订阅时序未改变。

## T4. Plugin 成功消费后提交并回收

**修改：**
- `crates/tools/bcs-bridge/core/src/plugin.rs`
- `crates/tools/bcs-bridge/core/src/run.rs`
- `crates/tools/bcs-bridge/core/tests/plugin_retention.rs`

- [x] 在正常 `send` 成功之后提交 cursor，随后同步回收对应前缀。
- [x] `encode(None)` 按现有过滤契约提交消费；发送失败、超时、未完成时不提交。
- [x] 将 pending cursor、route cursor、buffer 回收封装在不含 await 的同步提交段，
  校验前置，避免可预期错误导致部分提交。
- [x] 保持编码失败的替代错误上报与 `failed_delivery` 路径；不把原事件误记为成功。
- [x] 保持活跃 run 队列为空时不调用 terminal `release_delivery()`，不提前解除 pin。
- [x] 终态发布后排空事件才正常释放；重复释放、grace、幂等 fingerprint 不变。
- [x] 重连不重置 pending/route cursor，不重放回收前缀、不重启引擎。
- [x] 真正未消费积压超限仍保留一个终止错误并取消当前 run。
- [x] 一个 run 的消费提交不能触及另一 run、Bot 或 Session。

```bash
cargo test --locked -p bcs-bridge-core --test plugin_retention
cargo test --locked -p bcs-bridge-core --test plugin_websocket
```

**完成条件：** T1 长流由红变绿，真实积压对照仍有界失败；AC-01～09、11～13 的
Plugin 项均有断言，且没有增加 ACK、存储或发送调度行为。

## T5. 诊断与兼容回归

**修改：**
- `crates/tools/bcs-bridge/core/src/run.rs` 与 buffer 统计接口。
- 对应单元/集成测试。
- `crates/tools/bcs-bridge/cli/README.md` 中适当补充保留与可靠性边界。

- [x] overflow 记录 retention mode、events/bytes/both、消费 cursor、保留量、候选大小。
- [x] 增加完成摘要或既有诊断字段，不逐事件 INFO 输出，不添加正文/凭据。
- [x] 明确错误追加前后统计口径；让调用者能直接识别“已产生”与“待投递”。
- [x] 保留 `buffer_overflow` 外部类别，不要求消费者识别新错误码。
- [x] 补充 README：Plugin 已消费前缀回收；Gateway 保持完整历史；本地写入不等于
  服务端落库确认；本修复不提供进程重启恢复。
- [x] 验证新增 DB 读写为 0，按事件一次入队/出队；多 run 测试无 O(N²) 历史扫描。
- [x] 检查真实峰值内存没有随累计历史持续增长；区分容器容量、临时副本和预算值，
  不要求 RSS 在每次提交后立即下降。

```bash
cargo test --locked -p bcs-bridge-core --test encoder_contract
cargo test --locked -p bcs-bridge-core --test e2e_webhook
cargo test --locked -p bcs-bridge-core -p bcs-bridge-app \
  -p bcs-bridge-cli -p bcs-bridge-qwenwork
```

如发现与本改动无关的宿主依赖失败，应在未修改基准上复现并单列，不直接修改无关
实现或把失败标成通过；所有变更相关测试必须通过。

**完成条件：** Spec AC-01～AC-14 全部关联到具体测试/验证记录，无协议或字段回归。

## T6. 交付检查与发布准备

- [x] `git diff --check`，审查 diff 只包含相关文件，无全局格式化。
- [x] 核对新增/修改 Rust 文件均不超过 1000 行，所有本地文档链接可解析。
- [x] 记录实际运行的命令、测试数、失败/未运行原因、资源与 DB 成本变化。
- [x] 将 Spec 状态更新为真实阶段；尚未发布不得写“已上线”。
- [ ] 公共变更先完成评审；内部发行层消费该公共版本，不复制实现。
- [ ] 在实际内部发行仓库执行 bridge 构建/测试，验证版本锁定；该步骤依赖可用
  的内部构建环境，不是公共 workspace 测试的一部分。
- [ ] 计划内排空/停止活跃 run 后升级；不承诺热切换期间恢复已回收历史。
- [ ] 发布后观察长任务累计量与保留量、真实积压 overflow、终态和重连指标。
- [ ] 若回滚，停止/排空后回退发行版本；明确旧版本将恢复累计上限缺陷。

内部发行验证命令（仅在包含该 workspace 的发行仓库根执行）：

```bash
cargo test --locked --manifest-path apps/bcs-internal/Cargo.toml -p bcs-bridge
```

文档落盘或公共单测通过都不授权自动发布、重启用户运行实例或更新 DIMA。

## 2. 最终验收记录

测试细节和验收映射见 Validation；提交、发布与下游验证未完成项保留未勾选：

- [ ] 公共源码提交：本轮实现将随修复提交交付；提交状态以 Git/PR 为准。
- [ ] 内部发行版本/验证状态：`--locked` 被既有 Cargo.lock 不一致阻止，未修改基准同样复现。
- [x] AC-01～AC-14 对应测试与结果：见 Validation。
- [x] Gateway 重放回归结果：见 Validation。
- [x] V2/V3、重连、工具/交互与终态回归结果：见 Validation。
- [x] 长流峰值保留量/内存与真实积压失败结果：见 Validation。
- [x] 新增 DB 读写为 0 的核对结果：见 Validation。
- [x] 未运行项、已知限制与非相关失败：见 Validation。
- [ ] 升级/回滚说明及发布状态：方案已记录，尚未执行部署或回滚。

## 3. 后续工作，不纳入本轮执行

1. 有界批量发送与公平调度，解决当前单 run 名义约 50 条/秒的投递限制。
2. 在明确文本段边界后合并小粒度 delta，不能只靠事件类型相同就拼接。
3. Gateway 长任务的完整重放存储/有限重放协议，以及端到端可靠性增强。

这三项必须独立评审；不得在实现前缀回收时顺带改变现有流式语义或资源策略。

执行说明：T2–T4 在同一工作树集成后验证，未创建独立中间提交。长流发送边界测试
位于 `core/src/plugin_retention_tests.rs`，直接调用生产 `flush_events()` 并使用可控
Sink；`core/tests/plugin_retention.rs` 验证公开 runtime 接口。最终验证使用该工作树
独占的 Cargo target，避免其他检出复用同名产物。

追加限额调整：事件数 8192 → 16384，字节上限仍为 32 MiB。Plugin 边界单测使用
生产常量；公开 runtime 契约测试显式验证 16384 条正常记录 + 1 条错误。历史故障
形状和红灯证据中的 8192/8193 不改写。提交与推送按用户要求使用 `--no-verify`。
