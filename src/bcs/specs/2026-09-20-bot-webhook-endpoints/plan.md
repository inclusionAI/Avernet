# BCS Bot 独立 webhook 地址 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 同一 Provider 下的 Bot 可使用独立 webhook，兼容既有公共入口，无需 Backend 参与。

**Architecture:** 在 ProviderBotBinding 保存可选 URL，Provider 公共 URL 成为可选默认值。
由 BotCore 统一解析必填的最终投递地址，复用 HTTP transport。
管理契约、Repo 与迁移同步演进，平台适配由实际 Provider 接入方负责。

**Tech Stack:** Rust、Serde、现有 HTTP adapter、Memory/SQLite/MySQL store、Cargo contract tests。

---

## 执行约定

- 实施中用户已明确要求：本次暂不处理 1000 行限制，不拆分文件；Task 1 及后续相关拆分步骤据此取消。
- Task 5 复用现有 tests/helpers 启动设施，独立编写接收端用例，不拆分旧集成测试。

- 规格：[spec.md](spec.md)。本文件保留实施步骤，实际完成情况以 [validation.md](validation.md) 为准。
- 下列路径相对仓库根；命令默认也在仓库根执行。
- 遵循根 `AGENTS.md`、`src/bcs/AGENTS.md`、`src/bcs/CLAUDE.md`。
- 本次不修改 Backend，不接入 Poolab SDK，不新增每 Bot 凭证或动态寻址服务。
- 不运行全局 cargo fmt；不覆盖现有未跟踪文件；不放宽 CI baseline。
- 实施前使用隔离分支/工作区并安装该工作区 hooks：`scripts/install_git_hooks.sh`。
- 每一阶段先写能表达外部行为的失败测试，再实现并运行对应测试；阶段完成后形成可审查提交。
- 不在本次整理方案时提交代码、创建 PR 或执行发布。

## Task 1：建立兼容性测试基线并处理文件大小

核对以下现有入口及相邻 CONTEXT 文档：

- `src/bcs/crates/contracts/bcs-domain/src/provider.rs`
- `src/bcs/crates/contracts/bcs-protocol/src/http/provider.rs`
- `src/bcs/crates/services/bcs-bot/src/core/provider_core.rs`
- `src/bcs/crates/services/bcs-bot/src/core/bot_core.rs`
- `src/bcs/crates/services/bcs-bot/src/application/provider.rs`
- `src/bcs/crates/services/bcs-bot/src/application/bot.rs`
- `src/bcs/crates/services/bcs-bot-store/src/provider.rs`
- `src/bcs/crates/adapters/http/bcs-http/src/routes/providers.rs`
- `src/bcs/crates/bootstrap/bcs/src/migrations.rs`

1. 运行现有 Provider contract 测试，记录基线失败；不能把原有失败当作本次已通过。
2. 对将修改的源文件执行 `wc -l`，统计测试文件也不能遗漏。
3. 源码核对时 provider_core.rs=1224、store/provider.rs=1758、routes/providers.rs=1113 行；
   migrations.rs 及若干测试也已超过 1000 行。实施时重新测量，以实际版本为准。
4. 仅按相关职责拆分：配置解析、Provider 管理、Bot 注册/更新、store memory/DB/row mapping、
   route 管理/映射、迁移 schema/runner/tests；保持原公开导出和调用方向。
5. 对其他因新增字段而必须修改的超限文件同样拆分，不以改一行或旧文件为理由跳过限制。
6. 只做为本改动满足文件上限所需的拆分，不重排无关模块；拆分后重跑步骤 1。

基线命令：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-bot --test provider_core --test provider_management --test switch_delivery_contract
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-bot-store --test provider_repo_contract
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test provider_routes_contract --test bot_delivery_switch_contract
```

预期：现有行为通过；必要拆分不改变外部结果。
建议提交：`refactor(bcs): separate provider configuration and binding responsibilities`。

## Task 2：模型、HTTP DTO 与 Service API

修改入口：

- `src/bcs/crates/contracts/bcs-domain/src/provider.rs`
- `src/bcs/crates/contracts/bcs-protocol/src/http/provider.rs`
- `src/bcs/crates/service-api/bcs-service-api/src/application/provider.rs`
- `src/bcs/crates/service-api/bcs-service-api/src/core/provider.rs`
- `src/bcs/crates/service-api/bcs-service-api/src/port/repo/provider.rs`
- 上述类型的 re-export、noop/fake 和所有构造调用点。

新增测试：`src/bcs/crates/contracts/bcs-protocol/tests/provider_bot_webhook_dto.rs`。

1. 测试旧 Provider/Bot JSON 无新字段可反序列化，旧 binding 缺字段读为 None。
2. 测试 Provider 注册允许缺省/null，Bot PATCH 可区分字段缺省/null/字符串，非法类型拒绝。
3. 增加可选 Provider 默认地址与绑定字段；最终 BotDeliveryTarget 地址继续为 String。
4. 管理契约使用显式修改类型，HTTP 层负责 JSON 存在性转换。例如：

```rust
pub enum BotWebhookChange {
    Unchanged,
    Inherit,
    Set(String),
}
```

5. 新增绑定地址 Repo 更新方法，参数包含所属 Provider、Bot 标识、可选保存值和更新时间；
   返回持久化结果或错误，不提供静默成功默认实现。
6. 同步所有调用者、fixture、Service API 实现，保持旧构造场景使用 None。
7. 管理返回值新增已保存的 Bot webhook；ProviderInfoResponse 可表示无默认地址。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-protocol --test provider_bot_webhook_dto
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
```

预期：DTO 三态与旧输入兼容测试通过，workspace 无漏改的类型调用方。
建议提交：`feat(bcs): define bot webhook configuration contracts`。

## Task 3：存储、迁移和缓存

修改入口：

- `src/bcs/crates/services/bcs-bot-store/src/provider.rs` 及 Task 1 拆出的职责文件。
- `src/bcs/crates/services/bcs-bot-store/tests/provider_repo_contract.rs`。
- `src/bcs/crates/bootstrap/bcs/src/migrations.rs` 及拆出的迁移注册/测试文件。

新增：

- `src/bcs/migrations/mysql/027_provider_bot_webhook.sql`
- `src/bcs/migrations/sqlite/028_provider_bot_webhook.sql`
- `src/bcs/crates/services/bcs-bot-store/tests/provider_webhook_contract.rs`

迁移编号依据当前目录，实施时若已被占用，顺延并同步本计划和迁移注册表。
MySQL 新增 `webhook_url TEXT NULL`；SQLite 新增 `webhook_url TEXT`。
Provider 公共地址仍保存于 config JSON，不另增 Provider 表列。

1. 为 Memory/SQLite 共用行为案例增加 null、独立 URL、替换、清除及身份隔离测试。
2. 先验证旧库缺列时新读取失败，再接入版本化迁移。
3. 更新所有 INSERT/SELECT/row mapping，不能只改按 bot_uuid 单查。
4. 更新以 env/provider_id/bot_uuid 为条件，确认未找到和实际 DB 失败不会返回成功。
5. 提交成功后本地 binding cache 失效；写入失败不让新值进入缓存。
6. 测试旧库升级、新库初始化、重复启动、保留旧行与迁移 checksum 行为。
7. MySQL 验证迁移 SQL 和 store 参数映射；有可用实例时执行真实迁移。
   SQLite 通过不能作为 MySQL 真实迁移已验证的证据。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-bot-store
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --lib migrations
```

预期：两类 store 行为一致，SQLite 新旧库都可启动；MySQL 实测状态单独记录。
建议提交：`feat(bcs): persist per-bot webhook endpoints`。

## Task 4：注册、解析与管理行为

修改入口：

- `src/bcs/crates/services/bcs-bot/src/core/provider_core.rs` 及拆出的配置/绑定模块。
- `src/bcs/crates/services/bcs-bot/src/core/bot_core.rs`
- `src/bcs/crates/services/bcs-bot/src/application/provider.rs`
- `src/bcs/crates/services/bcs-bot/src/application/bot.rs` 的 switch 路径。
- `src/bcs/crates/adapters/http/bcs-http/src/routes/providers.rs` 及拆出的映射模块。

新增测试：

- `src/bcs/crates/services/bcs-bot/tests/provider_webhook.rs`
- `src/bcs/crates/adapters/http/bcs-http/tests/provider_webhook_routes_contract.rs`

1. 先写同 Provider 多地址、旧 Bot 继承、无默认地址 Provider 的行为测试。
2. 配置解析不再要求默认 URL 必有值，仍拒绝显式无效值并验证其他必填项。
3. 在注册任何 Bot/绑定写入之前验证 Gateway 的有效地址；Plugin 拒绝覆盖配置。
4. 注册重放不改保存值；显式地址与原配置不一致返回 ServiceError::Conflict，经 adapter 映射 409。
5. 在 BotCore 中统一选择地址，逻辑等价于：

```rust
let webhook_url = binding.webhook_url
    .as_ref()
    .or(downlink.webhook_url.as_ref())
    .ok_or_else(missing_delivery_endpoint)?
    .clone();
```

6. Bot/Provider/凭证检查保留。缺地址是配置错误，不执行 HTTP 或 WS 回退。
7. Bot PATCH 在 application/core 判定是能力修改还是地址修改：
   地址字段缺省走旧路径；地址独立修改走绑定 Repo；混用在写入前拒绝。
8. Inherit 检查 Provider 默认地址；Set 复用已有 URL guard 的配置验证。
9. Provider 查询、Bot 注册/更新/列表返回保存值；不让 route 自己决定地址继承。
10. switch 新建绑定检查默认地址；已有绑定重放检查该绑定有效地址；失败不得先 kick WS。
11. 测试不授权、跨 Provider、DB 错误、禁用状态、Patch 混用无部分写入及 auth-mode-only 读取。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-bot
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test provider_webhook_routes_contract --test provider_routes_contract --test bot_delivery_switch_contract
```

预期：spec 管理语义全覆盖，旧能力更新和 WS 行为无回归。
建议提交：`feat(bcs): resolve and manage bot-specific webhook targets`。

## Task 5：真实 HTTP 路由验收与协议回归

新增：`src/bcs/crates/bootstrap/bcs/tests/provider_bot_webhook_integration.rs`。
复用现有 `src/bcs/crates/bootstrap/bcs/tests/provider_downlink_integration.rs` 的测试设施，
通过测试 support 模块共享必要 helper，避免继续增大超限文件。

1. 起三个本地 receiver：Provider 默认、Bot A、Bot B。
2. 通过 HTTP 管理接口创建 Provider/Bot，并从真实消息入口触发投递。
3. 断言 A/B/旧 Bot 只命中各自预期 receiver，payload 里的 Bot 身份匹配。
4. 让 A receiver 失败，断言公共 receiver 没有收到 A 的重投。
5. 同一用例至少覆盖 1.0 callback 与 2.0 SSE；核对 inject/history/abort/interaction 的目标来源。
6. 地址 PATCH 后本实例下一次新投递命中新 receiver；重建 store/重启后保存值仍生效。
7. 两个 DB store 实例验证缓存最终一致：本地更新失效，另一实例遵循 TTL。
   采用现有测试时间控制或受控 cache fixture，不通过新增生产开关修改 TTL 语义。
8. 不把活动运行切换地址的测试写成“支持迁移”；维护边界见 spec 第 5 节。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-provider-http --test provider_transport_contract
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test provider_bot_webhook_integration --test provider_downlink_integration
```

预期：请求实际到达正确 URL，协议版本和接收端失败行为符合已有契约。
建议提交：`test(bcs): verify per-bot webhook routing and compatibility`。

## Task 6：文档、全量门禁与发布交接

修改：

- `docs/bot-provider-integration.md`
- `docs/bot-provider-integration.zh-CN.md`
- `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`
- `src/bcs/CLAUDE.md` 中与现有 HTTP Provider 已不一致的连接方式说明。
- 受影响 crate 的 `CONTEXT.md`、管理 API 契约/schema 映射（以实际暴露入口为准）。
- 本目录 `spec.md` 的验证证据和结果 PR/commit。

1. 更新注册/查询/更新示例、默认地址继承、Provider 返回值可 null、PATCH 限制和错误语义。
2. 把下行协议里的固定 `provider.webhook_url` 表述改为“解析后的 Bot 下行地址”，报文版本不变。
3. 文档明确 Provider 接入方责任，Backend 不参与此次开发。
4. 对所有新增/修改源码重新检查 1000 行上限、有效本地链接和 diff whitespace。
5. 运行下列门禁，记录真实结果与无法运行原因；SKIP 不计为已验证。

```bash
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
bash src/bcs/scripts/ci_test.sh --fast-fail
bash src/bcs/scripts/ci/arch-check.sh
bash src/bcs/scripts/ci/check-protocol-compat.sh
bash src/bcs/scripts/ci/check-r25-conformance.sh
bash scripts/ci/singlebox_coverage.sh
python3 scripts/ci/verify_singlebox_coverage_artifacts.py --reports-dir scripts/.dependencies/coverage/singlebox/reports/
git diff --check
```

全 workspace 单测使用既有 fast-fail 入口，避免普通模式仅输出报告而不阻断失败。
Singlebox 运行默认 all-module 入口并验证报告，保持根 AGENTS 中 BCS 覆盖率和 artifact 门禁。
这些是发布前的规定门禁；通过后只有新增修改、失败或未消除风险才重复扩大测试。

6. 发布按 schema → 全量 BCS → Provider 接入方排序；新地址能力上线前不让旧实例接收相关流量。
7. 完成 Poolab 接收端对 BCN 报文/下行认证的真实联调；若未联调，结果明确写未验证。
8. 交接回滚前提：已使用 Bot 独立 URL 后，旧 BCS 会忽略覆盖，须先停流量或建立兼容路由。

建议提交：`docs(bcs): document bot webhook registration and rollout`。

## 完成定义

spec 第 8 节逐项有测试或明确的联调证据，涉及的源文件均满足大小和架构约束。
代码评审能从契约看到新旧请求差异、从测试看到实际接收端、从发布说明判断回滚条件。
实现与验证结果见 [validation.md](validation.md)；文件拆分步骤已按用户后续指示取消。
