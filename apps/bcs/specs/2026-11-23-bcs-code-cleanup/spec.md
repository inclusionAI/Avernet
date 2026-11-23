# BCS 分层重复与无效逻辑清理

Status: draft; 方案 A 已获用户确认，Manifest 功能簇暂缓，修订后的书面设计与实现计划待用户一并审阅。
Date: 2026-11-23.
Owning module: `apps/bcs`.
Public contract change: 当前批次无公开 API 变更；Manifest 的两个 `bcs-config-api` 加法方法暂缓，需后续独立决策和版本策略。
Validation evidence: §8 及执行时创建的 `validation.md`。

## Context

本 spec 记录 `apps/bcs` Rust workspace 的低风险代码重构、重复逻辑收敛和重构后无效私有逻辑清理设计。设计来源是对当前代码、架构约束、构建基线和 CI 检查结果的只读分析。

执行前提：当前批次不改变公开 API、HTTP/WS 协议、数据库 schema、迁移历史和既有运行语义。Manifest 的公共 API 增量已明确暂缓，不属于本批实现范围。

## 1. 背景与问题

BCS 当前包含大量按存储实现、适配层和历史功能拆分的代码。拆分本身符合分层架构，但部分纯函数和数据投影逻辑已经在多个文件中复制，造成：

1. 同一字段的默认值、解析错误处理和兼容行为可能在不同查询路径中漂移；
2. Memory / DB 实现重复维护同一套输入校验，修改时容易只更新一侧；
3. 相同的 CLI、Manifest 和请求辅助逻辑存在多个来源，后续修复可能遗漏；
4. 部分聚合逻辑在热路径中使用重复的线性查找，代码和运行成本都偏高；
5. 测试共享辅助代码、兼容代码和真正无用代码混在一起，难以判断哪些可以删除。

本次工作不是一次“全局机械去重”。代码扫描中发现约 100 组归一化后相同的函数体，其中一部分是合理的边界适配或 Memory / DB 实现，不能仅凭相似度删除。需要按语义、依赖方向和合约归属分类后再处理。

## 2. 已确认的代码证据

### 2.1 Group Store 重复最集中

`crates/services/bcs-group-store` 在以下路径重复构造 `Group`：

- `src/store_reads.rs`
- `src/store_lists.rs`
- `src/store_finds.rs`

目前有 8 处字面 `Group { ... }` 构造：1 处严格单组读取和 7 个 JOIN 结果循环。七个 JOIN 循环分布为 `store_reads.rs::load_all_groups_from_mysql` 1 个、`store_lists.rs` 3 个、`store_finds.rs` 3 个；三处文件均重复了 Participant 行聚合逻辑。

当前七个 JOIN 聚合过程通过 `entry.participants.iter().any(...)` 判断重复参与者，参与者数量增加后会产生 O(n²) 的查重成本。事件式变更等位置的单次线性成员存在性检查不属于本次聚合优化范围。

### 2.2 Event Store 的 Memory / DB 校验重复

以下七组命名校验函数在 Memory 和 DB 路径各维护一份：

- claim 参数校验；
- lease renewal 校验；
- materialization 校验；
- completion 校验；
- Delivery 列表的 `validate_list_limit`（不含 Subscription 列表）；
- replay 校验；
- subscription 创建校验。

DB 侧已有 `src/db/validation.rs`，Memory 侧仍在 `src/memory.rs` 中保留相同函数体。上述七组函数的错误消息和校验顺序目前相同，应收敛为同一个 crate-private 校验模块。

revision replacement 和 append 只有部分逻辑相同，不能整函数合并：Memory replacement 先拒绝空 subscription id/env，DB 只校验下一 revision；Memory append 还解析两个时间戳。共享 DB `validate_replacement` 的下一 revision 谓词与 `validate_event_command` 的非空字段循环，Memory 在保留前置/后置检查后调用同一实现，保留各后端首错行为。subscription list limit、retention、skip/cancel reason 等内联重复不在本批共享范围内，验收第 3 条指上述七组函数及这两个明确共享的 replacement/append 谓词。

### 2.3 CLI 环境处理重复

以下运行时函数在 `crates/tools/bcs-cli/src/lib.rs` 和 `src/cli/environment.rs` 中存在重复实现：

- `session_file_path`
- `get_optional_session_file_path`
- `load_session_info_from_path`
- `load_optional_session_info`
- `normalize_bcs_api_url`
- `normalize_url_from_source`
- `resolve_env_bcs_url`
- `resolve_session_bcs_url`
- `get_current_env`

需要保留 CLI 的现有入口和命令行为，但只保留一个实现来源。其中 `get_current_env` 由 bin 直接复用 `bcs_cli::get_current_env`，不进入 `shared.rs`；其余八个 session/URL 函数进入共享源文件。`write_session_file` 只存在于 lib 测试模块和 bin 的 `cli/tests.rs`，属于两个测试 target 各自的 fixture，不是运行时写路径；本批保留这两份短小 fixture，不将其纳入运行时共享模块。

### 2.4 Manifest 辅助逻辑重复

以下路径都实现了 Manifest Bundle 的相同或近似逻辑：

- `crates/adapters/http/bcs-api-http/src/v1/internal/routes/manifest.rs`
- `crates/adapters/http/bcs-http/src/routes/assets.rs`

重复内容包括：

- 判断是否为本地文件 Bundle；
- 获取本地资源文件名；
- 根据扩展名选择 Content-Type。

这些是扫描发现，不是本批任务。两个 adapter 继续独立处理 Manifest，原因见 §5.4。

### 2.5 枚举字符串转换重复

扫描发现以下纯转换逻辑在多个 crate 中重复：

- `ParticipantRole` 到字符串；
- `ParticipantMode` 到字符串；
- `GroupStatus` 到字符串；
- `GroupStrategy` 到字符串；
- Unix 时间戳到毫秒；
- 部分 client kind、event state 和错误映射。

这部分存在领域类型、Service API 类型和 wire 类型并存的情况，不应直接把所有同名函数机械合并。同 crate 和跨 crate 转换均不在本批实施，后续只有在类型归属、依赖方向和测试覆盖逐项明确后才独立评估。

## 3. 目标和不变量

### 3.1 目标

1. 消除行为完全相同的重复实现，降低维护点数量；
2. 统一相同数据模型的默认值、解析和错误处理；
3. 优化 Group Participant 聚合的重复查找；
4. 保持 Memory / DB 各自既有行为，并共享其中完全相同的校验；
5. 清理本次重构后有引用扫描、编译和测试证据证明不再被使用的私有逻辑；
6. 让后续新增字段或校验规则只需要修改一个权威实现。

### 3.2 必须保持的不变量

- 不改变 HTTP、WS、A2A、CLI 对外协议；
- 不改变 JSON 字段名、默认值、状态转换和错误消息语义；
- 不改变 BCS 的 adapter → application → core / port 调用方向；
- 不让 service crate 直接依赖 DB、cache 或 runtime-store plugin；
- 不修改已提交 migration，也不新增 schema 变更；
- 保留 BaaS 无 `name` / `toolName` provider result 的兼容行为；
- 保留共享测试辅助和协议兼容 fixture，除非有全局引用证据证明其确实无用；
- 不运行全局 `cargo fmt` 或其它会产生无关格式变更的工具。

## 4. 方案选择

### 4.1 方案 A：分层、安全重构（采用）

按 crate 边界收敛纯逻辑和本地投影，不进行跨层大规模架构调整：

1. Group Store：共享行解码和聚合器；
2. Event Store：共享 Memory / DB 校验函数；
3. BCS CLI：集中环境和 session 文件处理；
4. HTTP Manifest：本批暂缓；后续独立变更再评估公共 API 增量和版本策略；
5. 同 crate 和跨 crate 字符串/时间转换留待后续独立评估，本批不实施；
6. 仅删除本次改动后失去调用者、且经引用扫描和编译测试确认的私有 helper/import；存量死代码候选不在本批扫描。

**优点**：行为风险低、容易按 crate 验证、不会扩大公共协议和架构边界。

**缺点**：不会一次性解决所有跨 crate 的相似代码；部分领域转换逻辑需要后续单独设计。

### 4.2 方案 B：把公共转换全部下沉到 `bcs-domain`

将角色、状态、策略和时间转换统一作为领域类型方法。

**不采用原因**：当前同时存在 domain 类型、Service API 类型和 wire 类型。强行统一会扩大公共 API，并可能引入不必要的依赖或协议耦合。

### 4.3 方案 C：优先清理死代码、依赖和 CI 检查

先处理 `#[allow(dead_code)]`、未使用依赖和过期架构检查脚本。

**不采用原因**：能够减少噪声，但短期无法解决运行路径中的重复解析和重复校验；另外共享测试辅助中的“单个测试目标未使用”并不等价于全局无用。

## 5. 详细设计

### 5.1 Group Store 行解码和聚合

在 `crates/services/bcs-group-store/src/store_mapping.rs` 或相邻 crate-private 模块中增加统一的 Group 行投影能力，负责：

- 从 `DbRow` 读取 Group 公共字段；
- 应用现有默认值；
- 解析 opening message、routing policy、group kind、group strategy；
- 解析 human mention notify mode；
- 初始化空的 `participants` 和 `messages`；
- 根据调用场景保留“严格读取”和“列表读取”的现有错误策略。

设计上不直接把所有调用统一成相同的错误行为：

- `get` / 单 Group 读取继续返回详细 `ServiceResult` 错误；
- list / find 投影继续保留当前对坏行的跳过、默认或整体返回空列表行为；
- 通过显式的 strict/tolerant 解码入口区分两种策略，避免重构时意外改变兼容行为。

Participant 聚合增加一个 crate-private accumulator：

- 使用 `HashSet<String>` 记录已经加入的 participant id；
- 保持当前 DB 行顺序和 Group 排序行为；
- 对 actor kind、mode、tags、message view scope 继续使用现有规范化函数；
- 不改变重复 participant 的“只保留第一条”语义。

分页和 count 查询只合并查询构造与结果解码，不改变 SQL 的筛选、LIMIT、OFFSET 和排序语义。`find_by_participant_paginated_sql` 当前外层 SELECT 未投影 `message_view_scope`，本批保留缺列回退行为并用真实 SQLite 锁定；不得借重构补列或改变 Human 成员的现有投影结果。

### 5.2 Event Store 共享校验

将上述七组完全相同的 Memory / DB 校验函数放到 `bcs-event-store` crate 内部的共享模块，例如：

```text
crates/services/bcs-event-store/src/validation.rs
```

共享函数继续返回现有 `EventRepoError`，并保持：

- 错误消息；
- 校验顺序；
- 边界值；
- SHA-256 校验；
- 状态组合检查；
- replay 约束。

`db/validation.rs` 不再保留七组函数及 replacement/append 共享谓词的独立实现，DB 和 Memory 共同调用共享模块。replacement 共享 `validate_replacement` 的下一 revision 谓词，Memory 仅在空 id/env 前置检查后调用它；append 共享 `validate_event_command` 的非空字段循环，Memory 随后再解析两个时间戳。各后端前后置检查和首错不得改变。

`memory.rs` 基线为 2453 行，修改前必须先独立机械拆分以满足所有变更源文件不超过 1000 行：

- `memory.rs` 保留 `MemoryEventStore`、状态、锁类型和构造入口；
- `memory/fanout.rs`、`memory/delivery.rs` 接收对应纯状态 helpers 和交付方法体；
- `memory/repo.rs` 保留唯一的 `impl EventRepoPort`，长方法委托给同 crate 的 inherent helpers；
- `memory/retention.rs` 接收 `purge_expired` 的完整实现；
- 同级模块间需要调用的自由函数和 inherent helpers 均使用 `pub(super)`，不是模块私有 `fn`，也不扩大 crate 外可见性；
- 保留所有公开签名、锁获取顺序、`.await` 和 public re-export，不拆成多个同 trait impl。

拆分是独立提交和回滚点。实施计划必须给出具体函数提取集和各文件目标行数；初始长方法提取集包含 `purge_expired`、`claim_deliveries`、`complete_delivery_attempt` 和 replay 职责内的 `create_replay_target`，避免 `repo.rs` 只剩数十行余量。七组共享 validator 在拆分提交中暂留 `memory.rs`，下一提交直接迁至 crate 根 `validation.rs`，不做第三次搬家。按知识标准在 Event Store `CONTEXT.md` 新增 memory 子模块布局说明（现有文档仅描述 `src/db/`），并把实际 `wc -l` 输出写入 `validation.md`。

现有 DB/Memory conformance tests 只作为回归保障的一部分。RetryWait 无未来重试时间、replay reason 超长、Delivery list limit 0/100/101 和后端首错等负例需明确新增共享单元测试及 Memory/DB store 级特征测试，不得把现有正向 conformance 当作这些边界的已有覆盖。

### 5.3 BCS CLI 环境和 Session 工具

`main.rs` 与 `lib.rs` 是两个 crate root，`pub(crate)` 不能直接跨 target 调用，lib 也不能只 re-export bin 的 `cli/environment.rs`。采用单一源文件、两个 target 各自私有编译的方式：

- 在 `src/cli/environment/shared.rs` 保存上述八个 session/URL 纯函数的唯一实现；
- 两个 crate root 分别用 `#[path = "cli/environment/shared.rs"] mod shared_env;` 加载它；
- `SessionInfo` 仍由 lib 定义并公开，bin root 私下导入 `bcs_cli::SessionInfo`，shared 模块通过 `super::SessionInfo` 引用；
- bin 的 `cli/environment.rs` 继续拥有命令入口和 binary 独有逻辑，通过 `super::shared_env` 获取并重新导出所需私有名字，同时直接复用 `bcs_cli::get_current_env`；
- lib 保留原公开路径，不引入新的公共函数，不把 `Cli`、日志 target、网络探测或鉴权流程带入 shared 模块。

两个 target 各自的 `resolve_bcs_url`（lib 另有可供测试注入默认值的 `resolve_bcs_url_with_distribution_defaults`）因参数类型（`Cli` / `GlobalArgs`）和编译默认值来源不同而保留，不纳入八函数共享范围；成对测试针对两个 `resolve_bcs_url` 的共有排序，lib 另行覆盖可注入默认值，bin 另行覆盖自身的 session 与 compiled/local 分支，避免包装链漂移。

bin 的模块引用必须保持 super-relative，禁止新增 `crate::shared_env` 等依赖普通 bin root 布局的路径，以保留 `main.rs` 被内部 overlay 通过 `#[path]` 复用的兼容约束。仓库内不能执行该 overlay 的编译验证，必须在最终报告中单列。

URL 优先级需分层保持：clap 会把 `MOLTIS_BCS_URL` 捕获进 `cli.url`，所以真实解析入口中它与显式 `--url` 共用最高优先级槽位，并先于 `BCS_API_BASE_URL`；手工构造 `url: None` 调用 resolver 时，才依次检查 `BCS_API_BASE_URL`、`MOLTIS_BCS_URL`、session、编译默认值和 localhost。resolver 的错误返回与 bin 当前的 `unwrap_or_default()` 处理也不得统一或“修正”。

Session 文件格式、默认路径、上述实际 URL 行为和错误信息保持不变。`write_session_file` 测试 fixture 本批保留在各自测试模块，不增加跨 target 的测试专用路径模块。实施时同步修正 CLI `CONTEXT.md` 和 `main.rs` 配置注释中的优先级描述。

### 5.4 Manifest 工具（暂缓）

本批不抽取 Manifest 工具，也不向 `ManifestBundleConfig` 增加公开方法。两个 HTTP adapter 继续各自保留 `is_file_bundle`、`local_asset_file_name` 和路由侧的响应逻辑；不使用跨 crate `#[path]`、`include!`、新依赖或其它方式绕过公共 API 边界。

**决策记录（2026-11-23）：deferred。** 根据本轮评审采用暂缓方案，未批准新增公开方法。原因是共享实现需要扩大 `bcs-config-api` 的公共 API，而本批不调整包版本或门禁。`check-public-api.sh` 在检测到 API 差异后用 `^version[[:space:]]*=` 读取当前版本；该模式不匹配 `version.workspace = true`。在 `set -euo pipefail` 下，当前版本读取失败即退出，不会进入后面的 base 版本跳过分支。

后续若重新启用 Manifest，必须单独建立变更记录，先明确维护者批准、consumer propagation analysis、包/工作区版本策略和 API-1 脚本兼容性，再实施方法抽取。Content-Type 判断继续由两个 HTTP adapter 各自拥有。

### 5.5 其它明确重复逻辑

只处理以下条件同时满足的逻辑：

1. 输入类型和输出语义完全一致；
2. 函数不是协议/trait 的必要边界包装；
3. 合并后不会引入反向依赖；
4. 现有测试能够覆盖行为；
5. 合并后调用方数量下降，且不会只是把代码移动到更难理解的位置。

本批候选范围：

1. Group Store 的行解码和聚合；
2. Event Store 的七组校验及明确相同的 replacement/append 谓词；
3. CLI 环境/session 工具。

同 crate 时间和枚举转换、跨 crate 领域转换和错误映射均留待后续独立变更评估，不属于本批验收范围。

## 6. 明确不做的清理

以下代码默认不删除或合并：

- Service API 与 Repo Port 中用于边界隔离的重复包装；
- Memory / MySQL 实现中必须保留的存储差异；
- 共享测试辅助模块中当前测试目标未使用、但被其它测试目标引用的符号；
- 兼容旧数据、旧协议或预留运行路径的 `#[allow(dead_code)]`；
- 生成代码、协议定义和 OpenAPI 映射中的表面重复；
- 只因为函数名相似、但错误处理或一致性语义不同的实现；
- 同 crate / 跨 crate 枚举和时间转换，以及与本次改动无关的存量死代码；
- Event subscription list limit、retention、skip/cancel reason 等未纳入七组共享函数的内联校验；
- Manifest `content_type_for` 等 HTTP 表现层逻辑。

## 7. 实施顺序

### 阶段 1：Group Store

- 增加/调整统一行投影函数；
- 增加 Participant accumulator；
- 改造 list/find/read 调用方，包括 `store_reads.rs` 的全量 JOIN；
- 删除重复的 `Group { ... }` 构造和聚合代码；
- 补充 Group 行解析、坏行处理、重复 Participant、分页缺列和分页结果测试。

### 阶段 2：Event Store

- 先按 1000 行门禁独立拆分 `memory.rs` 并更新 CONTEXT；
- 建立共享 validation 模块；
- 迁移 DB / Memory 调用方，保留 replacement/append 的后端差异；
- 删除七组重复 validation 实现；
- 新增共享边界及 Memory/DB 首错测试，运行 event-store 全部测试和 conformance tests。

### 阶段 3：CLI

- 收敛 CLI 环境/session 工具，修正 CLI CONTEXT/配置注释并记录 overlay 验证边界；
- 运行 CLI 及实际公开库消费者的相关 contract tests。

Manifest 功能簇已暂缓，不纳入本批实施和验收。

### 阶段 4：证据驱动的局部清理

- 仅对本次改动后失去调用者的私有 helper/import 逐项引用检查；
- 仅删除有全局引用、编译和测试证据支持的代码；
- 不扫描或批量处理存量 `#[allow(dead_code)]` 和重复转换函数；
- 不把架构检查脚本当前已有的基线失败误判为本次代码问题。

## 8. 验证计划

### 8.1 基线验证

已完成：

```bash
cargo check --workspace --all-targets
```

结果：通过。

已观察到的现有检查状态：

- `check-store-boundaries.sh`：通过；
- `check-port-purity.sh`：通过；
- `check-forbidden-symbols.sh`：通过；
- `check-deps.sh`：当前存在既有 DEP-1 / DEP-6 基线失败；
- `check-import-rules.sh`：当前存在既有 LINT-1 分层迁移基线失败；
- `cargo-clippy`：本机组件未安装，暂不能运行。

### 8.2 阶段验证

每个阶段完成后执行对应测试：

```bash
cargo test -p bcs-group-store
cargo test -p bcs-event-store
cargo test -p bcs-cli
cargo check -p bcs -p bcs-http --tests
```

完成所有阶段后执行：

```bash
cargo check --workspace --all-targets
cargo test --workspace
bash scripts/ci/check-store-boundaries.sh
bash scripts/ci/check-port-purity.sh
bash scripts/ci/check-forbidden-symbols.sh
BCS_BASE_REF=bee25769ab bash scripts/ci/check-public-api.sh
```

Event Store 必须区分现有正向 conformance 与新增负例：共享纯函数覆盖边界值和错误消息，Memory/DB store 级测试分别锁定首错，DB 使用真实 SQLite 命令对象。可提供 `BCS_TEST_MYSQL_URL` 时，额外运行 `cargo test -p bcs-event-store --test conformance_event_store_mysql -- --ignored`；否则在最终报告注明真实 MySQL 本地未执行及所依赖的 CI，不能用 SQLite 代替。

API-1 仍按计划作为公开 API 不变的回归检查；工具缺失或执行失败须单列，不能记为通过。

Group 验证分两层：`RecordingDbPlugin` 提供受控行，验证投影器的列名映射、错误处理和成员去重；真实 SQLite 执行原 SQL，验证 JOIN 实际返回的列、按组分页，以及 Human 成员在 participant 分页缺少 `message_view_scope` 时回退 `Full`。前者不证明 SQL 实际选择了这些列。

如果环境允许，再安装并运行 workspace Clippy；安装全局组件前需获得用户确认。否则在最终报告中明确标记为未验证项。

### 8.3 数据库访问成本

本设计不新增查询，不改变现有分页 SQL 和事务边界。Group 聚合只减少内存中的重复查找；Event Store 只合并纯校验逻辑。因此预期数据库 round trips、读取行数和写入行数不增加。

如果重构分页查询时需要改变 SQL 构造，必须额外验证：

- `kind` 过滤下的 LIMIT / OFFSET 结果；
- 空结果和坏行；
- 多 Participant Group；
- Memory / MySQL 结果一致性。

## 9. 风险和回滚

### 风险

1. Group 行解码的严格/容错行为被意外统一，或分页缺列 quirk 被无意修复；
2. Event Store 校验迁移时错误顺序或错误消息变化；
3. `memory.rs` 机械拆分时改变锁获取顺序、`.await` 位置或 trait 委托边界，以及拆分后仍超过文件行数门禁；
4. CLI 工具移动后影响隐藏的集成测试、二进制入口或内部 `#[path]` overlay；
5. 将 resolver 局部 URL 优先级误当成 clap 解析后的端到端行为；
6. 过度追求去重，破坏了 Service API / Port 的边界表达。

### 控制措施

- 每个阶段只修改一个功能簇；
- 先补充或确认回归测试，再删除重复实现；
- 不跨阶段混合 schema、协议和架构调整；
- 对 strict/tolerant、Memory/DB、adapter/core 这些边界保留显式命名；
- 变更前后比较公开符号和依赖图。

### 回滚

本设计不包含 migration 和公开协议变更，回滚只需要恢复对应 crate 的代码提交。阶段性提交应保持独立，便于按功能簇回退。

## 10. 验收标准

以下标准用于验收本批的 Group、Event 和 CLI 工作；Manifest 只核对未变更，不计为已实现：

1. Group Store 的 1 处严格构造和 7 个 JOIN 构造不再维护多份等价 Group 行构造逻辑；
2. 七个 JOIN Participant 聚合不再对同一 Group 进行重复的 O(n²) 查重，事件式单次成员检查不在此范围；
3. Event Store Memory / DB 共用 §2.2 的七组纯校验（列表限制仅指 Delivery）、replacement 的下一 revision 谓词和 append 的非空字段循环，replacement/append 的后端首错差异保持不变；
4. CLI 八个 session/URL 函数只有一个权威实现源文件，`get_current_env` 复用 lib 的公开实现，两个 target 专属 resolver 包装保留且成对测试，clap 与 resolver 两层既有行为均保持不变；
5. Manifest 功能簇保持暂缓：本批不增加 `bcs-config-api` 公开方法、不修改两个 adapter 的私有实现，并在 `validation.md` 记录暂缓原因；后续重新启用时另建独立变更并解决 API-1/version 策略；
6. 删除的每个本次新增孤儿 helper/import 都有引用扫描、编译和测试证据，不删除无关存量代码；
7. 既有公开 API、协议、数据库和兼容 fixture 保持不变；
8. 阶段测试、明确的 Memory/DB 负例和 workspace 构建通过；真实 MySQL 等条件验证若未执行，必须记录为缺口而非通过；
9. 现有基线失败与本次新增失败分开报告；
10. 代码 diff 不包含全局格式化和无关文件变更，并以文件清单逐项核对归属；
11. Event Store 布局和 CLI URL 优先级的 owning CONTEXT/注释同步更新；Manifest 暂缓原因、overlay、MySQL、Clippy 等未验证项在 `validation.md` 单列。

## 11. 实现计划

现有 [implementation plan](plan.md) 与本 spec 一并审阅，不再等待审阅后另行生成。计划已为每个阶段列出修改文件、预期接口、测试先行步骤、删除证据、验证命令和回滚点；后续对本 spec 的范围或行为约束作出修订时，必须同步核对并更新该计划。
