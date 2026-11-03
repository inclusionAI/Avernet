# Caller 配置契约与持久化（P1/P1b）

> **状态注记（2026-10-20 更新）：** 2026-10-09 的注记曾声明本域实现"整体回退"；
> 此后已随 `d72f4a49c` 及后续提交**重新落地**。当前 HEAD 的实际情况：
> - ✅ 已重新落地：领域类型与 Core/Repo 契约、受认证 GET/PATCH 子资源路由
>   （`router.rs` 挂载 `GET|PATCH /providers/{provider_id}/bots/{provider_bot_ref}/caller`）、
>   SQL 同步、`CallerConfigurationDeliveryGate` 派发保护；
>   对应测试 `conformance_caller_configuration` / `provider_caller_registration_contract` /
>   `provider_caller_capabilities_contract`。
> - ⚠ 仍为历史/未落地段落：下文涉及 `create_provider_bot_with_caller`、
>   `RegisterService.register_bot_with_caller`、`CallerRegistrationIncomplete` 的
>   "注册时原子初始化"（2026-10-06）子集是已回退轮次的记录——当前注册机制为
>   `ProviderCore::with_caller_config_store` + `RegistrationReconciliationRequired` 语义，
>   消费方是 `provider_caller_registration_contract.rs`。阅读对应段落时以此为准。
> - 🆕 服务器 TOML 配置段 `[caller_identity]` 已落地（`bcs-config-api`），见文末
>   "服务器装配配置 [caller_identity]"；注释版模板在 `configs/bcs-config-example.toml`。

## 服务器装配配置 `[caller_identity]`（当前实现，spec §10.13.7）

可选段；缺省 = 不装配任何 caller 能力（fail-closed，不留假插件）。出现即全字段必填，
非法材料拒绝启动。密钥走秘密下发，不提交文件。

```toml
[caller_identity]
plugin = "jwt"          # 本构建仅 "jwt";"agentpass" 为内部适配器,公开装配 fail-closed 拒启

[caller_identity.jwt]
issuer = "https://your-issuer.example.com"
signing_kid = "kid-2026-01"          # 必须命中 public_keys 中一项
signing_key_pem = "<Ed25519 PKCS#8 PEM>"
pseudonym_key_hex = "<>=16 bytes hex,与签名密钥独立>"
max_ttl_seconds = 300

[[caller_identity.jwt.public_keys]]
kid = "kid-2026-01"
public_pem = "<Ed25519 公钥 PEM>"
```

装配边界在 `bootstrap/bcs/src/caller_identity_wiring.rs`（JWT 插件 `bcs-caller-identity-jwt`）。
错误一律 `InvalidConfig` 闭合文案，不回显键材料。proof 的 wire 签发插件标签
`caller_execution_token.type`：`jwt` / `agentpass`（编码标签，信任来自受信装配）。

## Bot 侧配置读取的时效与成本（2026-10-20，§15.96）

`bcs_bots.bot_info.$.caller` 的运行期读取按三层组织,均不改变 §10.7.2 的写路径（二进制 CAS 逐字段保留）:

- **批量**:准入扫描按群成员一次 `IN` 查询,不是逐 bot N+1;
- **投影**:SQL 只取 `$.caller` 子对象 + 有效性卫兵列,不再整条 `bot_info` blob 下发与 parse;
  `caller: null`/畸形 JSON 仍是显式错误,绝不解释为 disabled（P1 冻结契约不变）;
- **读缓存**(`bcs-bot-store::caller_config_cache`):process-local,有快照正 TTL 30s、
  missing/未配置负 TTL 5s,singleflight,过期回源失败即 Err（fail-closed,无陈旧续命）。

**改变后的生效时效**:同一进程 PATCH→读取依旧立即(sync 成功写通失效);跨副本无失效总线——
关停方向(enabled 快照)≤30s 收敛且期间继续按 caller 保护路径(保守);**启用方向 ≤5s 收敛,
窗口内新启用目标按普通 Owner 路径派发(无 proof),这是被接受的边界**。历史读回
(G/PATCH 后的 provider 作用域 GET)刻意不经过缓存。诊断计数:
`bcs_bot_store::caller_config_cache_statistics()` → [hits, negative_hits, misses, errors,
invalidations, loads]。

## 责任与边界

`bcs_domain::caller_identity` 提供非敏感 Bot 配置模型，供后续受信控制面同步复用。
它不是入站 `CallerContext`，也不是 Human 执行身份信封、MCP grant 或 Provider readiness。
本批扩展内部 application/Core/Repo Service API，不新增 HTTP/Plugin API 端点，不宣称 Caller 已能执行。

实现依据：Caller spec §10.7；实施计划 P1/P1b。设计资料当前保留在 `Avernet/src/bcs`；源码在 `Avernet/apps/bcs`。

## 数据契约

```json
{
  "source_revision": 42,
  "caller_identity": {
    "enabled": true,
    "minimum_isolation": "caller_container"
  }
}
```

| Rust 类型 | 规则 |
|---|---|
| `CallerMinimumIsolation` | V1 仅 `caller_container`。`container_bound` 属于 Gateway 实际保证，不是此配置字段的值。 |
| `CallerIdentityConfig` | `enabled` 必填；最低隔离省略则为 caller_container；拒绝未知字段/非法类型。Rust Default 为关闭，只用于从未配置过的 Bot。 |
| `CallerIdentitySnapshot` | source_revision 必填 u64，caller_identity 必填完整快照；拒绝未知字段；不包含 Bot ID、env 或认证信息，它们由未来消费入口绑定。 |
| `CallerConfigRevisionDecision` | Apply / Unchanged / Stale 是纯比较结果，不是同步成功的 wire response。 |
| `CallerConfigRevisionConflict` | 同版本不同配置冲突；只包含非敏感 revision。后续 adapter 映射为版本冲突错误。 |

revision 是权威源单调版本，不是时间戳。不把 0 当“未配置”的哨兵；未配置由 `Option<&CallerIdentitySnapshot>::None` 明确表达。JSON 跨语言生产者须无损传输整数；若接入方只有 JavaScript number，应在其适配边界另行限制安全整数范围，不经过浮点转换。

未知字段严格拒绝意味着未来新增隔离能力或可写配置字段需先演进契约，不能依赖旧服务静默忽略。

## 版本比较

`incoming.compare_revision(current)` 不修改 current，不调用 IO。

| 当前状态 | 入站版本/内容 | 结果 |
|---|---|---|
| 未配置 | 任意合法快照 | Apply |
| 已配置 | 更高版本（包括显式关闭） | Apply |
| 已配置 | 同版本同内容 | Unchanged |
| 已配置 | 同版本不同内容 | Conflict |
| 已配置 | 更低版本 | Stale |

省略最低隔离和显式 caller_container 在解析后等价。

## Core 与 Repo 契约

`BotControlPlaneCoreService` 与 `BotControlPlaneRepoPort` 增加同名方法：

```rust
get_caller_configuration(bot_id, env)
    -> ServiceResult<Option<BotCallerConfiguration>>
sync_caller_configuration(bot_id, env, incoming: CallerIdentitySnapshot)
    -> ServiceResult<Option<BotCallerConfigurationSync>>
```

- 查询外层 `None`：当前 env 下目标不存在、已删除或不是 Bot；不是读取失败。
- 查询返回 `Some`、内部 `snapshot=None`：Bot 存在但从未配置；不能用 revision=0 表示未配置。
- 同步返回 `snapshot + decision`：Apply 为本次原子写入的快照；Unchanged/Stale 为读取到的权威快照。它不是 Provider readiness 或执行授权。
- 同版本不同内容返回 `Conflict`。所有 DB、解析和不支持错误向上返回，不把错误转换成 disabled。
- Core 直接调用 Repo，不水合 Provider、不读运行时缓存。普通内部同步方法本身不授权；已实现的 Provider application 入口先认证再调用带 Provider 作用域的方法，其他消费入口仍需先验证服务身份、Bot 管理范围与 env。
- 默认 trait 实现明确报不支持；现有 MemoryBotRepo 暂不支持 Caller 配置。SQLite 持久化实现可用于本地验证，不能静默退回 Memory/Owner。

## 存储、原子性与访问成本

复用 `bcs_bots.bot_info` 的 `$.caller` 保存上述完整快照，无 schema migration。
只保存非敏感配置；不保存 Human 身份、token、grant 或执行引用。整个 bot_info 为 SQL NULL 是合法旧数据；非法 JSON、非对象根、`caller:null` 或非法快照均报错。

```text
Core → Repo → 按 bot_id + env 读取非删除 Bot 的 bot_info
                ├─ Unchanged / Stale：返回权威快照，不写
                ├─ 同版本不同配置：Conflict，不写
                └─ Apply：JSON_SET($.caller) + 原始 bot_info 二进制相等 CAS
                           ├─ 写入一行：Apply
                           └─ 竞争失败：仅重读一次
                               ├─ 相同快照 / 新版本：Unchanged / Stale
                               ├─ 同版本不同内容：Conflict
                               ├─ 目标消失：None
                               └─ 仍需写入：Conflict，交由上层重试
```

CAS 同时约束 env、非删除和 Bot 类型。MySQL 用二进制 CAST，SQLite 用 BINARY 比较，避免大小写不敏感排序弱化 CAS。版本比较在 Rust 中使用 u64，不依赖 SQL 浮点提取。

整个 JSON 是 CAS 比较对象，因此并发修改其他 Bot 字段也可能导致 Conflict；不静默覆盖其他更新，不自动无限重试。读回绕开 Bot 运行时缓存，另一 Repo/Core 实例下一次读取可见已提交配置；不因此宣称既有 Bot DTO/cache 已接入 Caller。

| 操作 | DB 往返上限 | 返回/更新行数 |
|---|---|---|
| 读回、重复、旧版本、同版本冲突 | 1 次查询 | 至多 1 行 |
| 新版本成功 | 1 查询 + 1 条件更新 | 至多 1 行 |
| CAS 竞争失败 | 2 查询 + 1 条件更新 | 每次至多 1 行 |

不跨外部调用持有事务，不增加批量扫描或循环重试。查询只选 bot_info；其字节成本取决于已有 JSON 大小。上述行数不代表数据库扫描成本，真实 MySQL 的执行计划和负载仍需验证。底层错误和原始 JSON 不进入错误文本，避免泄露已有 Bot 数据中的凭据。

## 旧客户端兼容

普通 Bot 生命周期/descriptor 更新继续使用原有字段级 JSON 更新，测试验证它们保留 Caller 快照；Caller 更新也保留原有 descriptor 字段。旧更新入口省略 Caller 子对象时应跳过同步方法，不能写入默认 false。显式关闭需要更高 revision。

~~当前未扩展 HTTP DTO 或 BotCapabilities，因此生产注册/更新入口尚不能写入本配置。~~（2026-10-20 已过时：受认证 GET/PATCH 子资源已挂载并可写入本配置，见文首状态注记。）持久化成功不代表已启用 Caller 执行；不能仅暴露开关而仍按 Owner 派发。

## Provider 基础能力声明与权威读回（2026-10-04）

Provider 技术能力独立于 Bot 开关；新增领域类型 `ProviderCallerCapabilities`、
`CallerTokenVisibility`，持久化映射为 `bcs_providers.config.$.caller_capabilities`。
这是 §10.2 的基础声明，不是完整能力协商或 readiness：

```json
{
  "caller_capabilities": {
    "supports_caller": true,
    "supports_isolation_binding": false,
    "supports_observe_inject": true,
    "token_visibility": "runtime_only"
  }
}
```

四个字段均必填，拒绝未知字段、非法类型和 null；不提供默认支持值。
`supports_isolation_binding=false` 允许不返回 binding 引用，不表示免除隔离要求。
声明中不接受 Bot `enabled` 或计算结果 `effective_status`。

```text
受信 application（先授权）
  → ProviderCoreService.get_provider_caller_capabilities(provider_id)
  → ProviderRepoPort.get_provider_uncached(provider_id)
  → 当前 env 的 Provider 记录 → 校验 enabled 与 config → 非敏感能力投影
```

- 现有且未禁用的 Provider 未声明子对象：`Ok(None)`；downlink.enabled 不推导 Caller 支持。
- 不存在或禁用：`CALLER_PROVIDER_UNAVAILABLE`；坏 JSON/声明：
  `INVALID_PROVIDER_CALLER_CAPABILITIES`，不返回原始 config 或解析器诊断。
- 仓储读取失败继续返回错误；新增 trait 方法默认明确不支持，不能当未声明。
- SQL 实现绕过普通 Provider 的 30 秒进程内缓存，跨实例已提交更新在下一次读取可见；
  MemoryProviderStore 读取当前内存记录。普通 get_provider 保留既有缓存语义。
- 每次能力读取 1 次 SELECT，至多返回 1 行，0 写入、无内部重试；读取完整既有 Provider
  记录并在 Core 内投影，不将原始 config 暴露给此方法消费者。返回行数不代表扫描成本。
- 这不是与派发原子绑定的检查，不新增配置新鲜度、定期对账或上游同步恢复。
- Core 已由 ProviderManagement 的受认证查询消费，尚未接入生产准入或 HTTP 路由；外部注册/更新 DTO 不新增可写 Caller 字段。
  新能力不能修改 Bot revision，也不能解除未就绪派发保护。
- 协议版本、ensure、实际隔离保证、token profile、Owner lineage、历史屏障和
  status/cancel/fencing 的协商仍待实现；四项声明全部为 true 也不能判 ready。

变更范围为内部 Service API 及其 Repo 实现，不新增 Plugin API/HTTP 端点、迁移或依赖。
现有 trait 实现保留源码兼容；要参与 Caller 能力消费必须显式实现新方法。
领域契约 2 项、Core→真实 SQLite/Memory 仓储契约 4 项覆盖严格声明、旧配置、跨实例
缓存绕过、禁用/缺失、存储故障和错误脱敏。普通 Provider 缓存/注册更新回归随包运行。

```sh
cargo test --package bcs-domain --package bcs-bot --package bcs-bot-store --package bcs-service-api
# 654 passed, 0 failed, 4 ignored
cargo test --package bcs-test-support
# 24 passed, 0 failed, 1 ignored
```

## 受认证的 application 能力查询（2026-10-04）

`ProviderManagementService.get_provider_caller_capabilities(provider_id, provider_admin_token)`
复用既有 Provider admin 认证，随后调用 Core 权威投影；没有新 HTTP 路由，也不接收完整消息 JSON。

```text
未来控制面 adapter
  → ProviderManagementService
    → 既有 get_provider：验证 admin 凭据类型、有效性及 Provider ID 匹配
    → 检查 Provider 未禁用
    → Core.get_provider_caller_capabilities
      → Repo.get_provider_uncached：再次依据当前记录检查禁用和能力声明
      → 返回 Option<ProviderCallerCapabilities>，不返回原始 config/凭据
```

- 空/错误/非 admin 凭据和已撤销 admin 凭据拒绝；其他 Provider 的有效 token 也拒绝。
- 先认证后读取能力；无权限请求不能借坏配置错误探测能力内容。
- 认证路径可使用既有普通 Provider 缓存，但最终能力投影不使用该缓存；缓存中仍为启用
  的旧记录不能绕过 Core 的当前禁用检查。旧缓存为禁用时可能保守拒绝，不自动降级。
- 默认 trait 实现明确不支持；旧实现不必新增空实现，也不能返回伪造的未声明结果。
- 复用既有服务认证机制，不引入 Human 逐跳授权、凭据持久化、凭据 command Debug 或新授权开关。
- 不授予 Bot 配置修改权；Bot 同步仍须另行校验权威源、目标 Bot 管理范围和 env。
- 成本：SQL 配置下既有 admin 认证一次凭据查询、Provider 缓存未命中时至多一次记录查询，
  再加一次权威 Provider 查询，即正常单次调用至多 3 次 SELECT，0 写入/新重试。
  无跨认证和投影的事务，不宣称在途撤销或与后续派发的原子授权。

契约测试 `conformance_provider_caller_management.rs` 使用真实 application、ProviderCore、
MemoryProviderStore 和本地 registry，验证成功投影/旧配置、错误类型/跨 Provider 拒绝、
禁用、非法配置脱敏和凭据撤销；原 Core→SQLite 契约继续覆盖跨实例缓存与存储失败。
此步骤不是完整 Bot 配置同步闭环，不开放写入口或执行。

本步验证：先运行新增成功查询测试，因 application 方法缺失而失败，再实现后通过。
`cargo test --package bcs-bot --package bcs-service-api --package bcs-test-support`
共 414 通过、0 失败、1 忽略，其中新增 application 契约 3 项。
`git diff --check` 通过；架构门禁重跑仍为 1 通过/12 跳过/2 失败，
既有 DEP unbound variable 与 CFG 缺 rg 的问题未修复、未绕过。


## 受认证 Provider 作用域 Bot 配置同步（2026-10-04）

新增内部 `ProviderManagementService.sync_provider_bot_caller_configuration(command, provider_admin_token)`。
`SyncProviderBotCallerConfigurationCommand` 仅包含 `provider_id`、`bot_id`、`env`、`snapshot`；
凭据独立传入，不加入配置快照或可 Debug 的 command，不持久化 Human 身份材料。

```text
未来控制面 adapter（尚未接入）
  → ProviderManagement：验证 Provider admin 凭据类型/有效性/目标匹配
    → 校验 command.env 等于 BCS 当前 env
    → ProviderCore 权威无缓存读回：再次校验 Provider 存在、未禁用及声明合法
    → BotControlPlaneCore.sync_provider_caller_configuration
      → Repo：bot_id + env + canonical provider_id + 非删除物理 Bot
        → 版本判断 → JSON CAS 更新 → 必要时一次同作用域重读
    ← BotCallerConfigurationSync { snapshot, decision }
```

- 归属只使用 `bcs_bots.provider_id`；首次读取、条件更新、竞争后重读都包含 Provider 约束。
  MySQL/SQLite 对 Provider ID 作二进制比较，避免大小写不敏感排序扩大管理范围。
  未迁移/归属为 NULL 的 Bot 拒绝同步，不查询旧 binding 来自动认领。
- 仓储对缺失、已删除、非 Bot、跨 env/Provider 返回 None；application 统一返回
  `caller_configuration_target_out_of_scope`，不泄露其他 Provider 的配置快照。
- 未声明或未支持 Caller 的 Provider 可以存储 Bot 意图，但不代表执行 ready；坏能力声明拒绝。
  重复/旧版本返回当前快照；同版本不同内容冲突；写入失败传播，绝不返回成功。
- 未装配 control plane 或 Repo 不支持该能力时明确失败。普通未带 Provider 的内部方法
  保留供已授权消费者使用，但此 application 路径不能退回该方法。
- 此回执只确认配置存储，不包含 wire `effective_status`。尚无 HTTP 写路由、Backend 同步调用、
  完整注册 DTO 或配置关闭后的在途取消；现有未就绪派发保护不解除。
- Provider 凭据/禁用检查与 Bot CAS **不是同一事务**；仅 Bot 归属/env 和 JSON 版本在写入时
  原子约束，不宣称 Provider 并发禁用/撤销具备原子 fencing。

仓储成本仍为最多 2 次读 + 1 次条件更新，无内部循环重试。application 额外执行既有 admin
认证和一次 Provider 权威查询；按当前 SQL 实现推导，成功 Apply 最多 5 次 DB 操作，CAS
竞争路径最多 6 次（含认证时普通 Provider 缓存未命中的读取），不是端到端性能实测。

验证：应用契约 5 项经真实 application/Core/SQLite 与 MemoryProviderStore 覆盖版本、凭据种类、
跨 Provider/env、归属为空、禁用/撤销、存储失败与脱敏；仓储 13 项增加大小写边界、非 Bot/删除目标、
归属并发变化的 CAS/重读约束。Memory 的不支持共享契约同时覆盖新方法。

```sh
cargo test --package bcs-domain --package bcs-bot --package bcs-bot-store --package bcs-service-api --package bcs-test-support
# 689 passed, 0 failed, 5 ignored
```

架构门禁重跑：1 通过 / 12 跳过 / 2 失败，仍为 DEP 脚本 unbound variable、CFG 扫描缺少 rg；
未修改或绕过门禁。未运行真实 MySQL、全 workspace、Singlebox、Gateway/AgentPass 联调。

## Provider 作用域配置读回（2026-10-04）

配置写入和读回使用同一认证边界。新增内部方法：

```rust
ProviderManagementService::get_provider_bot_caller_configuration(
    provider_id, bot_id, env, provider_admin_token,
) -> ServiceResult<BotCallerConfiguration>

BotControlPlaneCoreService / BotControlPlaneRepoPort::get_provider_caller_configuration(
    bot_id, env, provider_id,
) -> ServiceResult<Option<BotCallerConfiguration>>
```

```text
Provider admin 认证及目标匹配
  → 当前 env 校验
  → Provider 权威无缓存状态/声明校验
  → Core → Repo 单次 SELECT（Bot + env + canonical Provider + 非删除 + Bot 类型）
  ← 仅 Caller 配置快照，不返回原始 bot_info
```

- 存在且归属正确、从未配置：`BotCallerConfiguration { snapshot: None }`；
  不制造 disabled/revision=0 快照。
- 不存在、跨 Provider、NULL 归属、已删除或非 Bot：Repo 返回 None，application 统一报
  `caller_configuration_target_out_of_scope`。外部 Bot 的坏 JSON 也不解析、不泄露。
- 错误/非 admin/已撤销凭据、禁用 Provider、跨环境请求明确拒绝；本方法沿用同步方法的
  Provider 声明校验，坏声明也拒绝读回，不默认 disabled。数据库失败继续报错。
- 默认 trait/MemoryBotRepo 明确不支持；没有不带 Provider 的 fallback，也不从旧 binding 认领。
- 仓储每次 1 次 SELECT、至多 1 行、0 写入，无重试或运行时缓存；其他实例配置及归属变化
  下一次查询可见。加上既有认证/Provider 检查，按 SQL 路径推导最多 4 次 DB 读取；未做负载验收。
- 不保证 Provider 撤销与读回是同一原子快照。返回值只表示当前读到的配置，不是执行准入证明。

新增 application 读回契约 4 项，覆盖未配置/启停版本、认证优先、作用域隐藏与存储故障；
另加仓储 1 项，验证跨实例可见性、归属变化和单读零写。共享 Memory 不支持契约扩展到读回。

### 后续注册/更新接入约束

现有 `register_provider_bot` 先完成 Bot/归属创建，再处理后续步骤。不能将 Caller 初始化简单
追加为“注册后第二次写入”：新 Bot 在配置尚未落库时可能被视为未配置并走 Owner 路径。
后续需将初始配置与注册可派发状态放在同一安全发布边界内，并对中途失败做契约验证。
混合 descriptor/Caller 更新也需明确部分成功语义。当前不修改这些外部 DTO、不挂载新的写路由；
因此本次只完成内部查询闭环，不将 P1b 或生产同步接入标记完成。

## 注册时原子初始化 Caller 配置（2026-10-06，应用/Core/仓储子集）

> **⚠ 历史段落（已回退轮次的记录）：** 本节及后续两节的
> `create_provider_bot_with_caller` / `register_bot_with_caller` /
> `CallerRegistrationIncomplete` 符号均不在当前代码中。当前注册机制为
> `ProviderCore::with_caller_config_store` + `RegistrationReconciliationRequired`
> 语义，契约见 `crates/services/bcs-bot/tests/provider_caller_registration_contract.rs`。

`BotProviderRepoPort.create_provider_bot_with_caller(record, capabilities, owner, token, snapshot)`
用于已授权上游首次创建 Bot；snapshot 是必填完整非敏感配置。旧 `create_provider_bot` 保持
不携带 Caller 的语义。新增方法默认明确报不支持，不能以“先创建后同步”实现，也不能在不支持时
丢弃快照回退旧方法；即使 snapshot.enabled=false 也必须保留其显式版本。

```text
RegisterService.register_bot_with_caller（内部接口，未挂载 HTTP）
  → 验证 Provider v2 注册令牌及签名 mode 范围
  → ProviderRegistrationCore.register_with_caller（重新授权）
  → DbProviderStore.create_provider_bot_with_caller
    → 校验新 Bot 元数据/owner/runtime credential
    → 将 snapshot 放入 capabilities JSON 的 $.caller
    → 一个 DB 事务
      ├─ 严格 INSERT Bot + canonical Provider 归属 + 初始 Caller 快照
      └─ Gateway 模式：INSERT compatibility binding
    → 提交后失效既有 binding 缓存
```

- Bot 和初始配置在同一 INSERT 中出现；Gateway binding 失败整笔回滚，不留下无配置 Bot。
- Bot ID 或 Provider/ref 重复返回冲突，不覆盖旧配置、不自动将旧注册改为 Caller 注册。
- Plugin 模式没有 Gateway binding；存储支持快照不等于该接入模式具备 Caller 执行能力。
- SQL 复用现有事务：Plugin 1 条条件 INSERT，Gateway 2 条 INSERT；不额外做注册后配置查询/写入。
  事务 API 调用次数不等于数据库网络往返次数；真实 MySQL、隔离级别和负载验证仍待完成。
- MemoryBotProviderStore 通过默认方法在任何写入前拒绝；旧 Memory 注册不受影响。
- 已有 Core 和内部 application consumer；无 schema/HTTP DTO 变更，旧 `register_bot` 不变。
  新方法只接受 Provider-scoped v2 token，owner/provider 来自签名；v1/无效/过期 token 拒绝，
  选定模式必须在 signed allowlist 内。Core 复用当前 Provider 授权与普通下行 readiness，
  不等同完整 Caller Provider 能力/执行准入，HTTP/Backend 注册字段尚未接入。
- 应用接口签名：`register_bot_with_caller(command: RegisterBot, snapshot: CallerIdentitySnapshot)`，
  返回既有 `BotRegistration`，不新增 ready 或身份凭据。错误沿用脱敏 ApplicationError；
  默认实现不支持时显式拒绝，不回退 ordinary 注册；新增方法对其他现有实现源码兼容。
- 应用层仅做本地令牌验证与一次 Core 调用，无独立 DB 访问或配置追加写。
  Core 的 Provider/凭据/单目标冲突检查和提交后的 Human/owner-edge 操作与原注册路径相同；
  后两项在 Bot 事务外；Caller 注册在创建成功后遇到这些失败，返回无字段的
  `ServiceError::CallerRegistrationIncomplete`，application 映射为 Conflict /
  `registration_reconciliation_required`（固定脱敏消息，不返回 Bot 凭据或底层错误）。
  普通注册错误行为不变。该标识不是恢复或安全发布屏障；不承诺整条注册操作原子。
  需用已有受认证 Provider/ref 查询检查已提交状态，再由运维处理；没有自动补写、
  凭据重放、删除回滚或重试。失败后的同 ref 重试仍冲突，不能覆盖已提交快照。
  真实 SQLite 回归覆盖 Human 插入失败、反向归属边失败，以及普通入口兼容性。
- 注册→派发跨边界回归（2026-10-06）：真实注册 application/Core/SQLite 的 enabled Bot，
  成功或提交后 Human/反向归属边失败后，经共享 gate 的 send/inject/task dispatch 和 pinned
  派发全部拒绝，外部 transport 调用为零；Provider descriptor 更新保留 snapshot/revision。
  仅顺序验证已提交状态，不证明并发安全发布；disabled 配置（含后续关闭）不能当作注册已完成。
  本轮无生产/schema/SQL 修改，HTTP Caller 注册字段和生产执行继续关闭。
  真实 SQLite 触发器测试覆盖 binding 写失败后 Bot 回滚及脱敏；未验证 MySQL/并发撤销 fencing。
- `token` 是既有 Bot 注册凭据，不是 Human Caller token；本次未新增 Human 身份材料持久化。

本批回归：`cargo test --package bcs-bot --package bcs-bot-store --package bcs-service-api --package bcs-test-support`，589 通过、0 失败、5 忽略。架构门禁仍为 1 通过/12 跳过/2 失败（DEP 脚本 unbound variable、CFG 扫描依赖缺失的 rg）；未改动门禁。`git diff --check` 通过。未运行真实 MySQL、全 workspace 或 Singlebox。

新增 `conformance_caller_registration` 4 项真实仓储契约：初始快照与归属读回、Gateway binding
失败整笔回滚及错误脱敏、重复注册不覆盖且旧路径保留未配置状态、Memory 提前拒绝且无副作用。

## 未就绪派发保护（本批交付）

```text
SQL 启动装配
  → 既有 BotDeliveryPort 消费者
  → CallerConfigurationDeliveryGate（application 策略装饰器）
      → Core → Repo → 当前 env 的权威 Bot 配置
      ├─ 存在且未配置/关闭：原 WS/HTTP transport mux
      └─ 开启/目标无效/读取失败：delivered=false，不调用 transport
```

- `deliver` 和 `deliver_on_connection` 共用检查；不按 frame method 或 delivery kind 绕过。开启 Caller 的 send、inject、task 派发目前均拒绝，因为身份执行和观察容器路由还未接通。
- 开启时错误为 `CALLER_EXECUTION_UNAVAILABLE`；目标无效、坏配置或 DB 错误也显式拒绝，不能退回 Owner。`BotDeliveryResult.delivered=false` 是本 attempt 的终态，不是结果未知，也不生成 transport-only 的 `DeliveryNotSent` 重试证明。
- 类型化 `abort` / `abort_on_connection` 不读配置，确保配置故障不阻断取消。availability/connection identity 只表达连接状态，不假装是 Caller readiness。
- 只在 SQL composition 安装。Memory 的配置读取和写入都明确不支持，不能开启 Caller，保留原有 Memory 派发路径；不是检查失败后降级。
- 每次内容派发增加 **1 次 SELECT，返回至多 1 行 bot_info，0 写入**；N 个目标为 N 次查询，无新增批处理、缓存、事务或重试。即使普通 Bot 未开启也需读取，SQL 故障因此会拒绝其内容派发。并发压力由既有派发并发与连接池承载，尚无负载验收。测试覆盖正常、阻断、连续 32 次故障及控制操作零读取。
- 本检查只保护经过共享 `BotDeliveryPort` 的派发；Provider interaction resolve 和 history request 使用其他端口，不宣称已覆盖。后续开放 Caller 前须完成这些端口的身份/路由边界。
- 配置读取与实际网络发送之间没有原子 fencing。此保护不是完整准入、身份快照或在途撤销机制；关闭配置后新普通派发可通过，不表示允许将旧 Caller run 转成 Owner。

公网 Caller 配置写入口仍关闭。Provider 能力协商、Gateway/身份插件、run/attempt 与撤销闭环完成前，不开放 Caller 执行。

## 测试与验收范围

- 领域类型契约：12 项，覆盖缺省、非法输入与版本比较。
- SQL 仓储 Caller conformance：14 项，覆盖真实 SQLite、共享版本契约、Memory 不支持、确定性并发重放/冲突、错误传播、目标隔离、坏数据、NULL/u64 边界与读写次数。
- 既有 Bot 控制面 conformance：补充生命周期和 descriptor 更新保留 Caller 配置。
- Core consumer conformance：2 项，通过真实 Core→Repo→SQLite 验证跨实例读回、冲突与失败传播，不是仅 DTO 序列化测试。
- 共享 harness：`bcs-test-support::contract::repo::caller_configuration`，后续支持此能力的 Repo 应运行相同版本契约；不支持的实现运行拒绝契约。

存储/Core 前一批命令：

```sh
cargo test --package bcs-bot-store --package bcs-bot --package bcs-domain
# 450 passed, 0 failed, 4 ignored
cargo test --package bcs-service-api --package bcs-test-support
# 214 passed, 0 failed, 1 ignored
```

本批派发保护验证：

```sh
cargo test --package bcs-bot --package bcs-service-api
# 383 passed, 0 failed
cargo test --package bcs --lib
# 309 passed, 0 failed, 5 ignored
cargo test --package bcs-message-flow --test delivery_runtime
# 17 passed, 0 failed
```

Caller 派发 conformance 共 8 项（真实 SQLite Core/Repo + 记录型 transport）；SQL 实际装配测试 1 项，已先验证未装配时失败，再验证装配后通过。原调度器契约验证显式拒绝与不确定派发的区别。

没有新增依赖、迁移或外部网络 IO。真实 MySQL 未验收，不能将 SQLite 并发测试视为 MySQL conformance。未运行 workspace 全量、Singlebox、真实 Gateway/AgentPass。

架构门禁上一批记录为 15 项中 1 通过、12 跳过、2 失败：DEP 脚本 `check-deps.sh:36` 报 unbound variable；CFG 中 bootstrap 131 测试通过，但后续扫描因缺失 rg 失败。跳过项还涉及 cargo-public-api、基线分支与非 PR 环境。派发保护批次已重跑，仍为 1 通过、12 跳过、2 失败（相同 DEP unbound variable / CFG 缺失 rg）；未修改脚本，该检查仍未通过。

Provider 基础能力批次已再次运行架构门禁，仍为 1 通过、12 跳过、2 失败（DEP unbound variable / CFG 缺失 rg），未改变门禁。`git diff --check` 通过。

## 下一批必须一起交付的闭环

1. 受信注册/更新/读回的 application/HTTP 映射与权限校验；普通运行 Bot 不获得开关修改权限。
2. Backend/其他权威源的版本化配置同步，完整入口兼容与越权拒绝测试。
3. Provider 能力和 Bot 声明分离；完整 readiness/准入消费权威配置；本批已有派发阶段的未就绪拒绝，但尚无 ready 执行路径。
4. 配置关闭与在途执行取消联动、真实 MySQL 验收；不将 P1/P1b 整体标记完成。
