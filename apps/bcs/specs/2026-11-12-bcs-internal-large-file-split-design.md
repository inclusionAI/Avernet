# BCS internal 五个超 1000 行 Rust 大文件按职责拆分设计 Spec

- **日期**：2026-11-12
- **状态**：draft（流水线 spec 任务 dima 2026111200120552113；待编排器/人工评审）
- **类别**：req · 【bcs】按职责拆分 5 个超 1000 行的 Rust 大文件（CI 合规）
- **范围**：仅 ocb 仓库 `apps/bcs-internal/`（内部 BCS overlay workspace）内 5 个 `.rs` 文件的**纯结构性拆分**；不改任何行为、不动 `submodules/avernet` 指针、不改 `Cargo.toml`/`Cargo.lock`、不新增 crate。
- **决策前提**（由本 spec 依据仓库既有规则推定，评审可推翻）：
  1. 拆分目标为 ocb 侧 `apps/bcs-internal` 当前 5 个达到/超过 1000 行的 Rust 源文件（明细见 §1.3，含 1 个恰为 1000 行触顶文件）；
  2. 拆分手段为**同 crate 内的模块拆分 + 既有 `#[path]` 测试抽取惯例**，不新建 crate，因此不触碰 workspace `members`、bootstrap `main.rs`、`check-p4.sh`/`check-p5.sh` 清单与 `Cargo.lock`；
  3. 只有一个文件（`outbound.rs`）的生产代码本身超 1000 行，必须拆生产模块；其余 4 个文件的合规路径是"按职责抽取测试/观测子模块"，生产区保持在 1000 行内即可，不做过量拆分。

---

## 1. 背景与问题

### 1.1 规则来源

- Avernet 根 `CLAUDE.md` 编辑纪律（团队两仓共用约定）：**"1,000-line file limit is CI-enforced. Before finishing a change, check line counts; split by responsibility and update callers/tests before CI."**
- Avernet `apps/bcs` 的两份 source-size waiver（`apps/bcs/docs/waivers/agentpass-subbots-{,client-}source-size.md`）**expiry = 2026-11-12（今日）**，其 `removal_plan` 要求"按职责拆分 ... with unchanged contracts"。Avernet 侧 waiver 表内的大头（`server.rs`、`bot/dispatcher.rs`、`bcs-bot-store`、`bcs-cli/main.rs` 等）在 dev 上已被陆续拆完（现 `server.rs` 985 行、`bot-store/lib.rs` 687 行）。
- ocb 侧 `apps/bcs-internal` 的 ACI `PRChecker.aci.yml` 有 stc 安全扫描与 `scripts/check-{architecture,p0,p4,p5}.sh` 结构门禁；**当前没有脚本化的行数检查**——1000 行上限是评审/合规约定。本工单（CI 合规）要求把 `apps/bcs-internal` 现存触线文件收敛到上限以内。

### 1.2 为什么是 `apps/bcs-internal` 这 5 个文件

全仓扫描（本 doc worktree，`wc -l`，子模块未检出即不在 ocb 侧范围内）：

| # | 文件（相对 `apps/bcs-internal/`） | 当前行数 | 生产/测试构成 |
|---|---|---:|---|
| F1 | `crates/plugins/bcs-channel-dingtalk/src/outbound.rs` | **4096** | 生产 1902 + 内联测试 2194（53 例） |
| F2 | `crates/plugins/bcs-channel-dingtalk/src/lib.rs` | **1625** | 生产 516 + 内联测试 1109（23 例 + caller_tests） |
| F3 | `crates/plugins/bcs-buservice-user-directory/src/lib.rs` | **1329** | 生产 668 + 内联测试 655（36 例）+ 尾部 3 |
| F4 | `crates/external-clients/bcs-layotto/src/zcache/client.rs` | **1140** | 生产 952 + 内联测试 184（6 例）+ 尾部 4 |
| F5 | `crates/plugins/bcs-auth-agentpass/src/lib.rs` | **1000**（触顶） | 生产 720 + 内联测试 272（9 例）+ test-only helper 12 |

其余 Rust 大文件（`apps/tui`、`apps/desktop`、`apps/gateway-cli` 等）不属【bcs】范围；Avernet 公共 BCS（dev 分支）另有若干 >1000 行文件，属另一条线（Avernet 仓派发），不在本工单。

### 1.3 行数口径（本工单评测基准）

**口径 = 文件全量物理行数（`wc -l`），测试代码计入。**证据：团队既有 waiver 表（Avernet `apps/bcs/docs/waivers/agentpass-subbots-source-size.md`）把 `tests/frame_compat.rs`(3437)、`tests/bot_use_cases.rs`(2097)、`tests/management.rs`(1294) 等**纯测试文件**列入明细，其 `removal_plan` 明文"…and large test fixtures by responsibility"；团队 CI 惯例（"1,000-line file limit is CI-enforced…split by responsibility"）对文件本身计行，不按生产/测试切片。因此 F2/F3/F5 以"抽测试"达成合规名正言顺。

**F5（恰为 1000 行）为何在范围内**：waiver 口径下 1000 行是贴线合规，但零余量——任何一行新增即触线；工单扫描以 ≥1000 抓取时必然命中。本 spec 按"恢复余量"处理（抽测试到 ~716），与其余四件同一合规逻辑：目标 ≤1000 且留余量（§2-1）。

### 1.4 问题本质

超大体量文件把多个职责焊在一起：F1 的 `impl DingTalkDelivery` 单块 1596 行、44 个方法，横跨令牌缓存、ACK 表情状态机、流式卡片、普通投递、失败通知、回复上下文、附件下载 7 类职责；F2–F5 的体积主体是内联测试与观测代码。评审、变更定位与覆盖率归因都被放大，合规扫描持续触线。

## 2. 目标 / 非目标 / 反目标

**目标**

1. 上述 5 个文件拆至每个 `.rs` 文件 ≤ 1000 行，且留出合理余量（目标 ≤ 800，除 F4 按设计自然落在 ~784）；
2. **行为零变化**：纯移动式编辑（move-only），方法体逐字节保留；仅允许最低限度的可见性放宽（`pub(super)`/`pub(crate)`）与 `use` 路径调整；
3. 公共 API 路径全部保持不变（crate 根 re-export 原样）；`inventory::submit!` 注册随代码移动、模块无关，天然不受影响；
4. 每个拆分单元落地后，对应 crate 既有测试全绿；收口时过全部既有门禁（§6）。

**非目标**

- 不改造任何业务逻辑、并发语义、超时/重试参数、日志文案；
- 不新建/拆分 crate，不改任何 `Cargo.toml`/`Cargo.lock`，不动 workspace `members`、bootstrap `main.rs` 的 `use … as _;` 清单、`check-p4.sh`/`check-p5.sh` 包列表；
- 不更新 `submodules/avernet` 指针，不在子模块内做任何改动；
- 不做全局格式化（仓库明令禁止 `cargo fmt` 类操作）；不做顺手"优化"（重命名、去重、抽象）；
- F3/F5 生产区拆分（本已 ≤ 1000）、F4 命令分族拆分（~784 后余量充足）、Avernet 公共 BCS 侧剩余大文件、以及其它 app 的大文件——全部留作后续工单。

**反目标**（出现即失败）

- 拆完仍 >1000 行，或为拆而拆导致文件数爆炸（微模块 <30 行成串）；
- diff 中出现非移动性的逻辑改动；
- 公共路径变化迫使 `bcs-internal` 二进制、示例或其它 crate 改 import；
- 任何测试被删除、跳过语义改变（如 `#[ignore]` 增删）、或测试执行顺序语义被破坏（F5 的全局 `OnceLock`/`AtomicBool` 与全局 tracing subscriber 敏感）。

## 3. 沿用惯例（拆分手法）

均来自仓库既有代码，直接复用：

1. **文件模块 → 目录模块零路径变化**：`foo.rs` → `foo/mod.rs` 时 `crate::foo::Item` 路径不变（Rust 2018+），`lib.rs` 的 `pub use outbound::DingTalkDelivery;` 与 `http_client.rs` 的 `use crate::outbound::DingTalkApiResponse;` 一行都不用改。先例：`bcs-layotto/src/zcache/mod.rs`（含兼容别名 `pub use client::AsyncZCacheClient as ZCacheClient;`）。
2. **`#[path]` 测试抽取**：`#[cfg(test)] #[path = "x_tests.rs"] mod tests;`——先例 `bcs-auth-agentpass/src/identity.rs:97-99`、`remote_identity.rs:144-146`、`lib.rs:995-1000`、`bcs-buservice-user-directory/src/lib.rs:1327`。
3. **多 `impl` 块跨模块**：同一 crate 内可在任意模块续写 `impl Type`；字段/私有方法被子模块访问需提升为 `pub(super)`（测试在模块后代中可继续访问私有项，Rust 隐私按"模块+后代"计算）。
4. **拆分后保持 `mod x;` + `pub use x::{…}` 收口在 `lib.rs`**，不新增 `pub mod`、不改 re-export 清单。
5. 提交形态：先纯移动（`git mv` 级别，内容不变），再做可见性/import 的最小编辑，分步可编译——参照 `bcs-bridge` `0676f81393`（R100 重命名与胶水文件分离）。

## 4. 逐文件拆分设计

### 4.1 F1 · `bcs-channel-dingtalk/src/outbound.rs`（4096 → `src/outbound/` 目录，全部 ≤500 行）

生产代码 1902 行，必须拆生产模块；内联测试 2194 行必须拆测试模块。

**生产侧模块图**（行数为现状截取量，±误差）：

| 新文件 | 职责 | 来源行段 | ~行数 |
|---|---|---|---:|
| `outbound/mod.rs` | `DingTalkDelivery` 结构体（9 字段）、构造（`try_new`、`try_new_with_base_url_for_test`）、常量表（117–141）、`active_config`、`send_dingtalk_json`、共享渲染/日志小助手（`render_*`、`log_delivery_*`、`event_kind_label`）、`ChannelDeliveryPort` 顶层编排（`is_available`、`deliver_event`）、`DingTalkApiResponse` | 31–41, 103–188, 254–266, 1029–1080, 1207–1240, 1740–1901 | ~500 |
| `outbound/ack_reaction.rs` | ACK 表情状态机：`AckReactionKey/Phase/State`、schedule/attach/expire/complete/update/recall 重试、body 构造、retryable 判定、reaction 名映射 | 52–101, 268–688 | ~480 |
| `outbound/streaming_card.rs` | 流式卡片：`StreamState`、卡片创建/更新 HTTP 与 body 构造、`card_open_space_id/card_user_id`、`deliver_streaming_card` 编排 | 43–50, 947–1027, 1106–1205, 1500–1738 | ~470 |
| `outbound/normal_delivery.rs` | 普通消息投递：`send_normal_message`、`normal_message_payload`、`provider_message_ref`、`deliver_normal`（含 delta/final 缓冲） | 786–865, 1082–1104, 1412–1498 | ~250 |
| `outbound/interactive.rs` | 命令回复 + HumanInput 一次性/卡片回退：`deliver_command_reply`、`deliver_human_input_request` | 1242–1410 | ~170 |
| `outbound/webhook.rs` | 回复上下文缓存（`remember_reply_context`）+ 会话 webhook 直投（`try_normal_webhook`，含 `validated_session_webhook` 交互） | 190–252 | ~75 |
| `outbound/tokens.rs` | 令牌缓存：`CachedToken`、`access_token*`、`exchange_token`、test-only `sensitive_request_values` | 31–35, 690–734 | ~55 |
| `outbound/failure_notice.rs` | 应用 API 失败通知 `send_failure_notice` | 867–945 | ~80 |
| `outbound/file_download.rs` | 消息附件下载 URL 交换（`resolve_message_file_download_url` + 局部 DTO），供 `attachment.rs` 的 `DingTalkDownloadUrlResolver` 使用 | 736–784 | ~55 |

要点与边界：

- **可见性**：`DingTalkDelivery` 的 9 个字段（bindings/client/tokens/reply_contexts/webhook_http/webhook_origin/streams/ack_reactions/now_ms）提升为 `pub(super)`；被子模块复用的私有方法（token、send_json、render、active_config 等约 30 个）提升为 `pub(super)`。类型定义保持在 `mod.rs`。
- **依赖环（现状已存在，保持不增不减）**：`http_client.rs:13` 引 `crate::outbound::DingTalkApiResponse`——文件转目录后路径字符串不变，零改动；`failure_notifier.rs:16` 引 `DingTalkDelivery`，outbound 引 `DingTalkReplyContext`/`validated_session_webhook`——各自保持原 import。`outbound.rs:29` 对 crate 根 re-export 的引用改为直连 `crate::config::…`（子模块惯例，避免依赖根 re-export 顺序）。
- **`mention_notify.rs:19`并存的 `OTO_BATCH_MESSAGE_PATH` 副本不动**（非本工单范围）。
- 迁移顺序（每步可编译）：(a) `git mv outbound.rs outbound/mod.rs` 零编辑；(b) 按模块图逐簇抽出（每簇一个提交，方法体字节不变）；(c) 测试拆分。

**测试侧拆分**（2194 行 → `outbound/tests/` 目录，`mod.rs` 由 `mod.rs` 内 `#[cfg(test)] mod tests;` 解析；测试作为 `outbound` 后代模块，继续合法读写私有字段，如 `delivery.ack_reactions` 的直改场景）。**行段为主题指引而非逐字搬迁表**——共享 doubles 与 builders 一律先落 `support.rs`，再按测试函数主题归位，实现时按实际依赖就近调整：

| 测试文件 | 主题 | 主要内容（现行段） | ~行数 |
|---|---|---|---:|
| `outbound/tests/support.rs` | 全部共享设施：`SharedLogBuffer/Writer`、`capture_tracing_logs`、`TestBindingRepo`、`active_binding`、`delivery_with_*` 构造器（2213–2251）、`event/reply_context/webhook_reply` builders（2364–2411） | 1925–2251, 2364–2411 | ~390 |
| `outbound/tests/webhook_and_binding.rs` | 会话 webhook 成功/回退、入站图片模式、group_receive_only 抑制 | 2105–2212 的测试函数、2252–2363、2412–2542 | ~330 |
| `outbound/tests/ack_reaction.rs` | ACK 纯单测 4 例 + 状态机异步测试 13 例 | 2543–3106 | ~565 |
| `outbound/tests/command_and_normal.rs` | 命令回复、普通单聊/群聊 API、下载 code | 3107–3307 | ~200 |
| `outbound/tests/failure_notice_and_logging.rs` | 失败通知、投递日志 | 3308–3475 | ~170 |
| `outbound/tests/final_display.rs` | final display 两例 | 3476–3597 | ~125 |
| `outbound/tests/streaming_card.rs` | 流式卡片创建/收尾/回退 | 3598–3749 | ~215 |
| `outbound/tests/human_input.rs` | HumanInput 8 例（含群）+ 标签/渲染 2 例 | 3750–4096 | ~350 |

注：`tests/rendering.rs` 并入 `human_input.rs`（60 行独立成文件违反"微模块不成串"精神）;`tests/mod.rs` 只做 `mod xxx;` 声明收口。生产侧 9 个模块中 `tokens.rs`/`file_download.rs`/`webhook.rs`/`failure_notice.rs` 体量 55–80 行，均为单一内聚职责，属"有意的细粒度"而非反目标所列的 <30 行微模块成串，评审时按此口径验收。

### 4.2 F2 · `bcs-channel-dingtalk/src/lib.rs`（1625 → ~530 + 测试目录，全部 ≤500 行）

生产代码仅 516 行，**不拆生产逻辑**（非目标：ingress/prepare_inbound 模块化留待将来）；按职责抽测试到 `src/lib_tests/` 目录，`lib.rs:517` 的 `#[cfg(test)] mod tests {…}` 替换为 `#[cfg(test)] #[path = "lib_tests/mod.rs"] mod tests;`：

| 测试文件 | 主题 | 来源行段（现 lib.rs） | ~行数 |
|---|---|---|---:|
| `lib_tests/mod.rs` + `lib_tests/support.rs` | 测试 doubles：`TestDownloadUrlResolver`、`TestBindingRepo`、`TestSink` 族、`RecordingFailureDelivery`、`SharedLogBuffer`、`valid_config/provider/signed_headers` helpers | 543–847 | ~310 |
| `lib_tests/provider.rs` | provider 配置/收件人/repo no-op 4 例 | 636–649, 849–889 | ~55 |
| `lib_tests/callback_messages.rs` | 回调 msgtype/附件/图片引用 11 例 | 890–1293 | ~405 |
| `lib_tests/callback_ack.rs` | ACK/解除绑定/日志/签名 5 例 | 1294–1563 | ~270 |
| `lib_tests/factory.rs` | 工厂注册 3 例 | 1564–1625 | ~60 |
| `src/tests/caller_tests.rs`（原位保留） | `#[path = "tests/caller_tests.rs"] mod caller_tests;` 从 `lib_tests/mod.rs` 引入，保持 `crate::tests::caller_tests` 路径
 | 现 `src/tests/caller_tests.rs` | 53（不变） |

`lib.rs` 保留：模块声明与全部 `pub use` re-export、`DINGTALK_CHANNEL_TYPE` 常量、`DingTalkChannelProvider`/`ChannelProvider` impl、`DingTalkHttpIngress`/`ChannelHttpIngressPort` impl、`prepare_inbound*`、工厂函数与两条 `inventory::submit!`。测试构造 `DingTalkHttpIngress {…}` 字面量处改为经 `pub(crate)` 字段访问（`DingTalkHttpIngress` 已在 crate 内，仅需把字段从私有提为 `pub(crate)`）。

### 4.3 F3 · `bcs-buservice-user-directory/src/lib.rs`（1329 → ~674 + `plugin_tests.rs` 655）

- 生产区 668 行涵盖 5 职责（provider 配置解析、HMAC 签名、JSON 映射、插件/端口适配、工厂），体量正常，**不拆**；
- 将 `mod tests`（670–1325，655 行、36 例）整体抽至 `src/plugin_tests.rs`，`lib.rs:670` 替换为 `#[cfg(test)] #[path = "plugin_tests.rs"] mod tests;`。测试经 `use super::*` 触达 crate 根全部私有项（`build_department_lookup_request`、`BackendDeptLookupConfig::from_provider_config`、`normalize_path`、`build_signature`、`DEFAULT_*` 常量），路径语义不变；
- 尾部 `#[path = "observations_test.rs"] mod observations_test;` 原样保留。

### 4.4 F4 · `bcs-layotto/src/zcache/client.rs`（1140 → ~784 + 两件，全部 ≤200…784 行）

仅抽测试仅余 956（余量过薄），按职责多抽一簇观测代码：

1. **观测簇抽出** → `src/zcache/client_observation.rs`（~170 行）：`ClientObservation`、其 `Drop`、`RequestObservation` 与其方法/`Drop`、`logged_connection_request`、test-only `logged_request`、`NEXT_CLIENT_ID`。`client.rs` 声明 `mod client_observation;`（文件同目录，`#[path]` 与文件模块二选一，按 `mod.rs` 近邻惯例用普通 `mod`）并对 `pool.rs` 需要的两项做 `pub(crate) use client_observation::{ClientObservation, logged_connection_request};`，**`pool.rs:super::{}` import 一行不改**。`redis_auth_command` 留在 `client.rs`。
2. **观测测试跟随移动**：`tests/support/zcache_observation.rs`（146 行）的 `#[path]` 声明从 `client.rs:1138-1140` 移入 `client_observation.rs`（`#[cfg(test)] #[path = "../../tests/support/zcache_observation.rs"] mod observation_tests;`，同目录故相对深度不变；`use super::*` 此时解析到观测簇私有项，语义对齐）。
3. **内联测试抽出** → `src/zcache/client_tests.rs`（~186 行）：`mod tests`（1 纯例 + 5 个 `#[ignore = "Requires Redis server"]`，ignore 语义原样保留），`client.rs` 尾部替换为 `#[cfg(test)] #[path = "client_tests.rs"] mod tests;`。

公共 API 全不动：`zcache/mod.rs`、`lib.rs` 的 `AsyncZCacheClient`/`ZCacheClient` re-export，以及 `bcs-leader-election/src/master.rs` 使用的 `zone_set_nx_ex/zone_set_xx_ex/zone_cas_*` 签名均不受影响（方法体跨模块移动，不改变 inherent 方法路径）。

### 4.5 F5 · `bcs-auth-agentpass/src/lib.rs`（1000 → ~716 + `plugin_tests.rs` 284）

- 将 `mod tests`（721–993，272 行、9 例）抽至 `src/plugin_tests.rs`，声明保持模块名 `tests`：`#[cfg(test)] #[path = "plugin_tests.rs"] mod tests;`——**必须保持 `crate::tests::{empty_acm_user, test_acm_params, MockAgentAcmClient}` 路径**，`identity_tests.rs`、`observations_test.rs` 正在消费；
- 同时把 test-only 助手 `authenticate_agent_code_with_client`（495–506，12 行）移入 `plugin_tests.rs`（生产区只剩 pub `authenticate_agent_code` 路径上的真实代码）；
- 生产区 5 簇（ACM SDK init、tcauthmng HTTP、agent_code 解析、插件/AuthPlugin 装配、BotRuntimeTokenResolver）共 ~716 行，本已合规，**不拆**；
- 测试序敏感项原样保留：`resolve_returns_none_when_sdk_not_initialized` 对进程全局 `AGENT_ACM_INITIALIZED` 的操作、以及 `observations_test` 的全局 tracing subscriber——抽取不改模块求值顺序（同为 `#[cfg(test)]` 编译进同一测试二进制）。

## 5. 约束与护栏

- **交付形态（本 spec 拍板）**：一个实现工单、一个 PR、**按文件分阶段提交**——F5、F3、F4、F2 各 1 个提交，F1 三个提交（(a) `git mv` 零编辑、(b) 生产逐簇抽出、(c) 测试拆分），共 7 个提交，每个提交可独立编译可独立 review；五个拆分单元相互独立，若评审认为 diff（约 9000 行移动）过大，可按文件无损拆成多个 PR。禁止一次性大爆炸提交。
- **编辑纪律**（仓库 CLAUDE.md）：不动未改行的空白/格式；只移除本次移动造成的死 import；`git diff --check` 必须干净。
- **磁盘纪律**（workspace AGENTS.md）：本 worktree 当前**缺** `.cargo/config.toml`（recon 实测），实现工单开工时先补 `[build] target-dir = "/tmp/cargo/Avernet-target"` 并写入该 worktree gitdir 的 `info/exclude`；构建前 `df -h /tmp` 余量 <8G 只清 `debug/incremental`，<3G 停手；不 cargo clean / 不整删共享缓存。
- **不做的事**：不报"顺带修"其它近顶文件（`zdas/client.rs` 847、`crates/plugins/bcs-channel-dingtalk/src/failure_notifier.rs` 810 等均 <1000，留作常规维护）。

## 6. 验证与门禁（按工具链现实分级）

本机实测（2026-11-12）：`rustc 1.99.1`、`cargo-llvm-cov` 可用；**`protoc`、`cargo-nextest` 均未安装**，`bcs-layotto/build.rs` 的 `tonic_build::compile_protos` 使 F4 连 `cargo check` 都依赖 protoc。因此验收分级：

**必备（每个拆分单元、必须本机过）**：

```bash
git submodule update --init submodules/avernet      # 一切门禁前置
cargo test  --manifest-path apps/bcs-internal/Cargo.toml -p <crate>            # 本单元测试全绿
cargo check --manifest-path apps/bcs-internal/Cargo.toml -p <crate> --all-targets
wc -l <新文件清单>    # 全部 ≤1000，并贴入 PR 描述
./apps/bcs-internal/scripts/check-architecture.sh && ./apps/bcs-internal/scripts/check-p0.sh
```

F4 的必备项需先解决 protoc：以内网制品源下载预编译 protoc 至 `/tmp` 并临时注入 `PATH`/`PROTOC`；不可得时如实记录并用 `cargo check -p bcs-layotto` 在 ACI 上的通过证据替代本机证据。

**收口（尽量本机；工具链不可达时如实降级并留证）**：

```bash
./apps/bcs-internal/scripts/check-p4.sh        # 11 包 check+test（F3/F5 在列表内；F1/F2 的 dingtalk 不在 p4/p5 列表，主要覆盖靠单 crate 测试与 ACI ci_test）
./apps/bcs-internal/scripts/check-p5.sh        # 二进制组装 --help 标记；注意其硬编码 apps/bcs-internal/target/debug/bcs-internal
bash apps/bcs-internal/scripts/ci_test.sh --fast-fail   # 全 workspace nextest 100%；需补装 cargo-nextest + protoc
```

`cargo-nextest` 经 `cargo install cargo-nextest --locked` 安装；若受机器/网络限制装不上，按流水线惯例（push `--no-verify`、CI 由编排器监控）在 PR 描述中如实声明"本机未跑成 ci_test，留待 ACI"，不放任任何已开测试的失败。`check-p5.sh` 硬编码 target 路径——重定向 target-dir 后需以默认 target 目录跑一次 p5（或临时符号链接 `target`）再恢复；已在计划中列为显式步骤。

## 7. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| F1 体积大、可见性提升点多，移动时混入逻辑改动 | 中 | 提交切三段（§5）；每段 `cargo check`；review 按"方法体 diff 应为空"核对；方法体字节级移动 |
| F1 测试直改私有字段（ack_reactions 等），拆后编译不过 | 低 | 测试保持为 `outbound` 后代模块（`outbound/tests/`），隐私规则天然兼容；support builders 集中放 `tests/support.rs` |
| F4 观测测试 `use super::*` 依赖具体私有项，挪错宿主模块即断 | 低 | 按 §4.4 第 2 步把 `#[path]` 声明移进 `client_observation.rs`，深度不变 |
| F5 其它测试文件经 `crate::tests::` 消费共享 mock | 低 | 保持抽取后模块名仍为 `tests`、`pub(super)` 可见性不变 |
| ci_test/nextest 需要 protoc、cargo-nextest 与磁盘余量 | 低 | 计划列前置检查；余量硬闸按 §5 磁盘纪律 |
| 拆完行数贴线（如 950+）半年后复发 | 低 | 目标 ≤800（§2-1）；F4 已为此多抽观测簇 |

## 8. 附：现状证据快照（2026-11-12，本 worktree）

```text
4096  apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/outbound.rs
1625  apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/lib.rs
1329  apps/bcs-internal/crates/plugins/bcs-buservice-user-directory/src/lib.rs
1140  apps/bcs-internal/crates/external-clients/bcs-layotto/src/zcache/client.rs
1000  apps/bcs-internal/crates/plugins/bcs-auth-agentpass/src/lib.rs
 970  apps/bcs-internal/crates/tools/bcs-bridge/tests/register.rs   # 参考：tests 目录内、未触线
```

## 9. Builder-check 裁决记录（2026-11-12）

独立审阅者（talented-builder 视角）提出 4 问，裁决如下；均为本 spec 依据仓库证据自决（my call），评审可推翻：

| # | 问题 | 裁决 | 落点 |
|---|---|---|---|
| 1 | 行数口径：测试代码是否计入？只算生产代码则 F2/F3/F5 天然合规 | **全量 `wc -l` 计入测试**——waiver 表收纯测试文件为证；F5 贴线按恢复余量处理 | §1.3 |
| 2 | §4.1 模块表是否为逐字强制 | **生产表为目标分解、测试表为主题指引**；共享 builders 先入 `support.rs`，就近可微调；55–80 行内聚小模块不违"反微模块成串" | §4.1 测试表注 |
| 3 | 收口门禁：protoc/cargo-nextest 本机缺失怎么办 | **分级验收**：单 crate test/check + arch/p0 必过；p4/p5/ci_test 尽量本机，不可达时依赖 ACI 并 PR 留证（流水线惯例 push --no-verify + 编排器监控 CI） | §6 |
| 4 | 五个 PR 还是一个 | **一个 PR、按文件分 7 个阶段提交**；必要时按文件无损拆分 | §5 |
