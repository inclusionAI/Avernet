# BCS 代码与测试构建精简（第一期）

## 状态、目标与权威来源

- 状态：用户已审阅并要求编写实现计划；尚未实施代码变更。
- 日期：2026-11-05；当前复核基线：`origin/dev@f8adbab061`（完整 SHA 见复核记录）。
- 最初调查基线 `520be5ed6e` 保留作历史证据，当前实施以远端复核基线为准。
- 所属模块：BCS；参与范围：BCS Rust workspace、相关 CI 调用与覆盖率映射。
- 用户已确认：以构建收益为主，允许 workspace 成员数持平。
- 实现计划：[plan.md](plan.md)；远端复核：[origin-dev-review.md](origin-dev-review.md)。
- 目标：在保留现有功能、唯一测试用例和架构边界的前提下，减少无效依赖、
  重复源码编译、重复迁移测试注册及测试可执行文件数量。

权威来源：

- [仓库指导](../../../../AGENTS.md)、[BCS 指导](../../AGENTS.md)及
  [模块架构](../../CLAUDE.md)。
- [架构宪法](../../../../docs/arch/arch.rules.md)与
  [CI 约束](../../../../docs/arch/ci.enforce.md)。
- [调查证据](../../../../docs/research/2026-11-05-bcs-code-and-build-footprint.md)。
- [已有 CI 优化](../../../../docs/research/2026-10-29-bcs-unit-ci-performance.md)。
- [Service Session policy 退役边界](../2026-10-27-retire-service-session-policies/spec.md)：
  该设计明确保留 legacy Task callback，本期不得顺带删除其 WS 接口。

本设计不改变系统领域模型、对外 Service API 或 Plugin API 语义，不替代任何
已接受 ADR。迁移代码的库化是 BCS 内部职责调整；新模块须声明 CONTEXT.md，
并更新受影响的依赖检查。Backend 的 context metadata 格式和 Python protocol
测试布局不直接套用于 Rust 模块。

## 基线和收益口径

| 指标 | origin/dev 复核基线 | 本期预期 |
| --- | ---: | --- |
| workspace members | 112 | 删除 bcs-callback，新增 bcs-db-migrations，预计仍为 112 |
| Rust 物理行（含测试） | 701,233 | 确认删除候选为 callback 的 63 行；不承诺总行数下降比例 |
| 集成测试 targets | 601 | 两个试点收敛后规划 506，区间 499～507，见下述条件 |
| message-flow / HTTP targets | 60 / 53 | 规划 9 / 10（包含需要隔离的独立入口） |
| 无源码标识符引用的普通依赖声明 | 34 条，涉及 21 个包 | 逐项验证后删除，记录保留原因 |
| migration 源码跨位置包含 | 29 处 | 全部替换为库依赖；独立迁移测试不随调用者重复注册 |

499～507 的推算仅包含两个试点由 113 个入口变为 12～20 个，另移除一个
误发现的 e2e_helpers target；新迁移库的测试放在 library test harness 中。
具体计划采用 6+3 个 message-flow 入口和 8+2 个 HTTP 入口，即
`601 - (60 - 9) - (53 - 10) - 1 = 506`。
这是规划目标，不是已经测量的结果。发现隔离需求时保留独立入口并说明差额，
不得为了凑数删除测试、改成 ignored 或缩小测试选择范围。

成员数、源码行数、依赖声明数、test target 数和构建产物体积分别报告。
源码移动不计为删除；移除直接依赖声明不代表传递依赖消失；减少测试重复注册
不代表删除业务覆盖。产物体积和耗时必须通过同条件前后实验验证。

## 方案选择与范围

选择“无效项清理 + 编译与测试结构精简”，分批验证：

| 方案 | 优点 | 限制 | 决定 |
| --- | --- | --- | --- |
| 只删空壳和无用依赖 | 改动小，容易验证 | 对体积问题的覆盖有限 | 作为第一批 |
| 迁移库化并收敛测试入口 | 直接消除已发现的重复工作 | 需要维护测试身份、路径和 CI 选择器 | 本期主线 |
| 合并业务 crate、裁剪功能或插件 | 可能进一步缩小构建图 | 涉及产品范围和架构边界 | 本期不做 |

本期纳入：callback 空壳、e2e_helpers 的 target 误发现、34 条依赖候选、SQLite
迁移执行器与测试的库化、message-flow 和 HTTP 两个 crate 的测试入口收敛。

后续单独设计：生产 Noop 与 bcs-test-support 的装配解耦、跨 crate fixture 库
拆分、HTTP/WS 依赖版本统一、全局 feature 收窄和本地 profile 调整。本期在
suite 内复用现有 fixture，不额外创建大型共享 fixture crate。

不删除业务模块、外部 API、历史 migrations、兼容性 fixture、生产插件、panel
资源或用户本地缓存；不修改 debug/incremental 设置来混淆结构精简收益。

上游已完成 message-flow `contract_message_flow.rs`、CLI `client.rs`、Group
`management.rs` 的按职责拆分，本期沿用现有子模块结构，不重复拆分或还原旧文件。
新增 `bcs-tracing-api` 和 `bcs-network-tracing` 是有效能力，保留其契约、传输
feature、无 provider 行为和全部新测试。复核也已覆盖 native task Caller 身份/TTL
的新场景；这些用例和嵌套 support 模块都必须计入更新后的测试基线。

## 设计一：清理已确认的无效项

### Callback 空壳

移除 `crates/services/bcs-callback`，同步更新 workspace member、workspace
dependency、Cargo.lock 和当前 README。删除仅验证空桩能返回的两个测试，
并在测试清单差异中单独列出。

删除前再次核对整个仓库的 manifest、脚本、构建/发布清单与 Rust 引用。
如果发现实际消费者，保留该 crate，先处理其兼容性，不将删除视为已完成。
外部发布消费者无法仅凭仓库静态扫描排除；发现发布记录或外部消费线索时，
需补齐发布/消费证据再删除。

`bcs-ws::bot::TaskCallbackHook`、`BotDispatchState.task_callback` 及 task.complete
兼容分支保留。Provider callback、事件订阅和 state-machine 行为不在删除范围。

### Helper target

把 `bootstrap/bcs/tests/e2e_helpers.rs` 移至同一 tests 树的 support 子目录，
更新六个调用入口。保留辅助函数及行为，只消除 Cargo 自动发现的空测试入口。
通过 Cargo metadata 证明 `e2e_helpers` 不再是独立 test target。

### 依赖声明

对调查中的每个候选记录：所属包、原依赖种类和 feature、直接使用情况、删除
或保留结论、验证命令。检查 derive 宏、link-time registration、build script、
feature forwarding 和间接开启的 feature，不能仅依据文本搜索删除。

对每个实际改变的包，验证独立构建、所有 target 和适用 feature 组合，再验证
workspace 构建。默认、关闭默认以及全部 feature 的组合仅在包实际支持时运行；
不以“不常用”为由跳过已支持组合。已有依赖版本保持锁定，不夹带升级。

新增候选是 `bcs-a2a-http` 中的 `a2a-client`（包名 `a2a-client-lf`）。上游 REST
实现已改为 `ClientWithDefaults`，但该 SDK 仍间接启用 reqwest 0.13 的 JSON、
stream、query、charset、HTTP/2 和 system-proxy 等 feature。删除声明时先在
实际消费者显式保留现有 feature 和传输语义，再做独立构建验证；不把去掉这些
能力当作依赖精简收益。A2A 的模型 SDK `a2a`、protobuf/gRPC 与 tracing 适配保留。

## 设计二：独立迁移库

### 职责和依赖

新增 `crates/services/bcs-db-migrations`，角色为服务数据库 schema 的基础设施
实现，与 store 层同类；不是 Core、Plugin API 或具体数据库驱动。

它拥有当前 SQLite baseline、版本执行器、schema repair、检查与报告逻辑及
相关测试。普通依赖限制为 `bcs-db-api` 和实现必需的工具库；SQLite driver
实现仅作为测试依赖。不得依赖 bootstrap、HTTP/WS、具体业务 service/store、
全量 test-support，也不把服务 schema 塞入 bcs-db-local 或 bcs-db-api。

新 CONTEXT.md 声明提供的函数、允许依赖、消费者、schema 所有权和测试方式。
增加依赖检查来防止该库反向依赖装配或业务服务，不放宽既有架构门禁。

### 接口和调用路径

迁移并保留现有函数与报告类型的签名和语义，包括：

- `run_sqlite_migrations`、`run_sqlite_migrations_with_report`。
- `check_sqlite_migrations`、`sqlite_target_version`、`sqlite_migration_count`。
- `run_sqlite_bootstrap_tables`、`run_sqlite_bootstrap_indexes`、
  `run_sqlite_versioned_migrations`。
- `SqliteMigrationReport`、`SqliteMigrationPlan`。

bootstrap 通过普通库依赖调用新执行器；现有公开 `bcs::migrations` 模块保留
薄 re-export，避免仓库外装配消费者因导入路径迁移而直接失败。

目前 29 处 `#[path]` 调用全部改为该库的开发依赖或对应测试所需的库导入。
业务集成测试仍然初始化独立数据库、执行真实迁移链，然后运行原业务断言。
新库的 49 个已有迁移测试仅在所属库注册；调用者不包含该库测试源码。

### 数据和错误契约

- 已提交 `.sql` 内容、版本、顺序、名称和已记录的 checksum 原样保留。
- 嵌入 Rust 的历史 SQLite DDL/迁移内容同样冻结；只移动职责和更新资源路径。
- 不把当前 schema 回填到初始迁移，不新增业务迁移，不创建测试专用短路 schema。
- 不改变事务、执行顺序、错误传播、repair 条件或启动失败方式。
- 调整 include_str 等路径后，比较 SQL 资源及运行时生成的迁移计划、schema、
  迁移历史和报告，避免仅比较源文件名就宣称等价。
- MySQL runner 不迁入此库；现有 MySQL chain 和 admin 契约保持运行。

生产数据库往返、每次启动的读写行数和 repair 工作量应与基线一致；测试因
重复注册消失而减少初始化次数。不得把这个重构顺带改成批处理、缓存或查询优化。
已有错误/持久化失败必须继续返回错误，不能通过吞错维持表面兼容。

## 设计三：按业务域收敛测试入口

先在 `bcs-message-flow` 试点，完成等价性与构建对照后再处理 `bcs-http`。
每个 crate 保持自身测试边界；以合计 6～10 个顶层入口为目标（含隔离入口）。
文件按业务域作为子模块组织，而不是把内容拼成巨型 Rust 文件。

message-flow 可按消息路由、队列/交付、caller/relay、协调事件、预算/租约与
存储集成分组；HTTP 可按 Bot/Provider、Group、Session/消息、鉴权、协作运行
和管理接口分组。最终文件到 suite 的映射在实现计划中逐项列明。

公共 setup 和 fixture 在同一 suite 内只声明一次。共享模块的改名和导入调整
不得改变 fake 的默认行为、断言、超时或场景输入；跨 crate 共享方式暂不扩张。
需要不同 fixture 实现的同名模块不能通过粗暴归并丢失差异。

为 nextest 和 `cargo test` 分别检查执行行为。特别检查环境变量、全局日志
subscriber、静态状态、进程级资源和端口隔离；不能把 nextest 的执行隔离当作
本地 `cargo test` 也安全的证明。确需不同进程的测试保留独立 target。

保持 ignore 属性和原因。若 suite 重命名影响 `.config/nextest.toml` 的 binary
过滤、MySQL 定向 runner 或脚本，必须同步更新，验证选择结果非空、无歧义。
仍禁止重试掩盖失败，不通过全局串行化整个 workspace 来换取合并后的通过。

当前隔离清单包含 message-flow 三个全局 subscriber 测试入口，以及 HTTP 的
`request_observations` 和新增的 `network_tracing_otel`，后两者都修改全局
OpenTelemetry propagator。`bot_chat_contract` 与 `bot_events_contract` 也设置
同型 W3C propagator，计划放入同一 Bot suite；合并前核对同 suite 所有用例的
初始状态假设，并在普通 cargo test 下验证，无效时保留独立入口。

## 测试身份、覆盖率和构建证据

### 测试清单守恒

实施前导出包含 package、binary、完整测试名、ignored 状态的 inventory。
为重命名建立显式旧身份到新身份映射，且维护以下允许差异：

1. 删除 callback 的两个空桩测试。
2. 多份完全相同的迁移测试归并到新库中各一份，列出原注册位置。
3. 空 helper target 消失，不对应任何用例删除。

其余唯一用例必须一一对应，新增用例另列。不能仅去掉模块前缀或按测试函数
尾名去重，以免把不同场景或不同实现的 conformance 用例误当成重复。
关键兼容性 fixture `baas_command_output_end_without_name.json` 保持原样。

### 覆盖率语义

运行完整 workspace 单测与现有报告 gate：100% 通过率、总体行覆盖率严格
大于 87%；超过 50 行可计量变更时，变更行覆盖率严格大于 85%。
少量变更的现有报告规则保持不变。

迁移源码移动后，unit 和 Singlebox/E2E 原有生产代码分母必须持续覆盖迁移
执行器。若路径筛选未纳入新位置，修正映射并说明原因；不能因文件移出原目录
而从分母消失，也不能把新的测试文件计入生产分母抬高结果。
保留单测报告与后续 MySQL 契约报告的隔离和已有构建复用。

### 性能实验

同一机器、工具链、锁文件、features、profile、job 数和环境对比基线与变更。
普通开发构建和 CI instrumented 构建分别测量，target 目录彼此独立；不混用
两种产物，不删除用户已有缓存，不把一次性的历史缓存清理算成优化收益。

记录以下数据与命令：

- workspace 和试点 crate 的 target 数，实际生成的测试 binary 数。
- 测试 binary 总字节、target 下 deps/build/incremental 的分类字节、服务 binary
  字节；明确排除日志、报告和原始 profile，并注明去重/硬链接统计口径。
- 冷构建、无修改重建、代表性 fixture/测试变更后的重建和完整测试耗时。
- 代表性重建使用同一语义改动，测量后恢复；不把首次依赖下载混入编译时间。

时间对照各至少三次，报告中位数和范围；不能把几秒的单次波动当成收益。
试点合并进入下一批的条件：测试清单与 gate 通过，测试 binary 总字节下降，
且没有可归因于该批修改的稳定耗时退步。若中位数退步超过 5%，复测并定位；
确认退步后调整 suite 划分或回滚该批，不能仍宣称达到优化目标。

不预先承诺减少某个 GB 或固定编译时间百分比。受机器/环境限制无法完成的
项目必须明确记录为未验证，最终性能结论等待同平台证据。

## 验收清单

- [ ] 重新采集实施基线及测试 inventory，记录与调查基线的差异。
- [ ] callback 删除或因实际消费者保留的结论有证据；WS Task callback 保留。
- [ ] e2e_helpers 不再单独成为 test target，原辅助行为和六个入口可用。
- [ ] 34 条依赖候选逐项有删除验证或保留理由，无意外版本升级。
- [ ] 迁移库边界明确，29 处源码包含消除，原公开导入路径兼容。
- [ ] 49 个迁移测试各保留一份；业务集成测试仍执行完整迁移链。
- [ ] 历史迁移资源、存储结果、错误行为一致；SQLite 和真实 MySQL 链通过。
- [ ] 两个试点 target 数下降，唯一测试身份/ignored 状态保持，CI 选择器有效。
- [ ] 普通 cargo test 与 nextest 验证隔离，报告性能对照与保留独立入口的原因。
- [ ] 架构检查、当前完整 BCS 单测覆盖率门禁和 Singlebox gate 通过；新迁移库
  的生产路径没有从原覆盖范围消失。
- [ ] 运行 `verify_singlebox_coverage_artifacts.py` 验证统一报告目录，保留当前
  BCS 的 pass-rate、line/method、endpoint/CLI 和 changed-line 门槛。
- [ ] 所有新增或修改的源文件不超过 1,000 行；仅对受本期影响的文件按职责拆分，
  不扩展其他计划的历史豁免，不运行全局 cargo fmt。
- [ ] PR 描述区分实际删除、代码迁移、测试去重和实测收益，列出所有未验证项。

## 分批交付与回滚

建议按四批交付：无效项清理；迁移库化；message-flow 试点；HTTP 推广。
每批保留独立 diff、用例映射和验证结果。新迁移库的完整抽取与消费者替换
作为一个可验证批次，不留下双份迁移实现。

回滚某批恢复对应依赖、源码位置、test target 和 CI 选择器即可；本期不写入新
schema 版本，无需数据逆迁移。已完成批次可以保留。性能或隔离不达标时允许
回退某个 suite 的合并，不降低测试和覆盖率要求。

## 文档阶段验证

本次交付限于 spec 和前置调查，未运行实现阶段的 Rust 构建、单测、数据库或
性能实验。文档审阅通过后再编写逐批实现计划；上述清单未勾选项均不代表已经
完成。实施 PR、提交号和实际验证结果在实施结束时追加到本目录的验证记录。
