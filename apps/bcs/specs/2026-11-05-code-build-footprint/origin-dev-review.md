# origin/dev 基线复核（2026-11-05）

## 复核对象与方式

用户要求根据远端 dev 重新审阅 [spec.md](spec.md) 和 [plan.md](plan.md)。
本次执行 `git fetch origin dev` 后固定复核对象为：

- 完整 SHA：`f8adbab061e63fe72a8042b0a2a9bf1a7d7c525c`。
- 提交时间：2026-11-05 20:36:36 +08:00。
- 提交：`refactor(bcs): split CLI BcsClient by responsibility (#3676)`。
- 原源码基线：`520be5ed6e`；原文档提交：`b45ba367a0`、`f6409854d9`。

通过 Git archive 把固定 SHA 的 BCS 和相关门禁文件提取到临时目录，运行离线
Cargo metadata 并与原始映射比较。没有 merge/rebase 当前分支，没有修改生产
源码；因此当前文档工作区的旧代码不能用来验证更新后的成员数和文件结构。
实施时须从已获取的远端基线建工作分支，再带入审阅后的文档。

## 数量变化与更新决定

| 指标 | 原基线 | 本次远端基线 | 文档处理 |
| --- | ---: | ---: | --- |
| workspace members | 110 | 112 | 删除 callback、新增迁移库后预计仍 112 |
| Rust 文件数 | 2,245 | 2,319 | 记录远端跟踪源码，排除本地构建产物 |
| Rust 物理行 | 698,224 | 701,233 | 更新 spec 基线，不把移动代码算成删除 |
| library targets | 108 | 110 | 新增 tracing 契约和 adapter |
| integration test targets | 595 | 601 | 总目标重算为 506 |
| message-flow targets | 60 | 60 | 保留已拆分的嵌套结构 |
| HTTP targets | 52 | 53 | 补入 network_tracing_otel 并保留独立入口 |
| 两个试点入口数 | 112 | 113 | 全部映射，无删除和遗漏 |
| 依赖候选 | 33 / 20 包 | 34 / 21 包 | 增加 A2A SDK client 候选及 feature 约束 |
| migration 源码包含 | 29 | 29 | 抽取范围保持 |
| migration 单测静态属性数 | 49 | 49 | 仍待真实 inventory 验证重复注册数量 |
| 本计划触及的超长入口 | 13 | 12 | 删去已经完成的 contract_message_flow 拆分任务 |

当前规划计算为 `601 - (60 - 9) - (53 - 10) - 1 = 506`，减少 95 个
integration test target，约 15.8%。workspace 净成员数预计保持 112。
这些是结构规划，不是已实现的耗时/体积收益；测试完整性和隔离要求仍优先。
完整 metadata 解析为 637 个包，包括开发及平台依赖，不等于单次编译的包数。

## 上游已完成的工作

- `100a7179b1` / #3666：message-flow `contract_message_flow.rs` 从 4,936 行
  拆成 74 行父入口、12 个业务模块和 6 个 support 文件。计划改为保持子树、
  随父入口一起移入 cases；不重复拆分、不重命名已有主题模块。
- `52b9cd7770` / #3671：Group `management.rs` 已拆为 85 行入口。本期无需
  清理其旧巨型文件，相关测试仍由原 target 发现。
- `f8adbab061` / #3676：CLI `client.rs` 已拆为 484 行入口。本期仅保留其
  manifest 依赖候选核验，不再次拆分 client 实现。

上游另一个拆分 spec 仍写有“尚未实施”，该状态不能覆盖实际提交和源码证据。
原 [调查记录](../../../../docs/research/2026-11-05-bcs-code-and-build-footprint.md)
保留历史基线，本记录及更新后的 spec/plan 作为当前执行依据。

## 新能力和测试的保留范围

`79e8f8f561` / #3615 新增 `bcs-tracing-api`、`bcs-network-tracing`，并新增
六个 integration target：

- `bcs-network-tracing`：`conformance`、`http_clients`、`propagation`、`routes`。
- `bcs-storage-baas`：`tracing_scope`。
- `bcs-http`：`network_tracing_otel`。

network-tracing 和 storage-baas 的五个入口不在本期合并范围。HTTP 新入口在
第 100 行设置全局 W3C
propagator，与 `request_observations` 一样保留独立；HTTP 计划调整为 8 个 suite
加 2 个独立入口。该测试依赖的 blocking/reqwest13 feature 和真实 Provider
传输不能被当作无用测试依赖删除。

另核对到 `bot_chat_contract` 和 `bot_events_contract` 都设置同型 W3C
propagator；它们仍拟放入 Bot suite，但计划明确要求验证进程初始状态假设与
普通 cargo test 并发，不以“同型”直接断言合并安全。

`b281b03683` / #3625 增加 native task Caller 身份/TTL 行为及嵌套
`support/native_owner_task.rs`、`support/protected_dispatch_io.rs` 场景。
入口数未增加不代表测试数量没变；计划要求保留其挂载和最新唯一用例清单。

## 新依赖候选的 feature 风险

`bcs-a2a-http/Cargo.toml` 中声明别名 `a2a-client`（包名 `a2a-client-lf`）。
原 REST 实现使用 `a2a_client::{A2AClient, rest::RestTransport}`；上游改用
`bcs_network_tracing::ClientWithDefaults` 后，Rust 源码已没有该 SDK client 引用。

但锁定版本 `a2a-client-lf 0.2.9` 的 manifest 为 reqwest 0.13 启用了
`json`、`stream`、`charset`、`http2`、`system-proxy`、`query`。当前 A2A 包自身
只声明 `rustls`；因此不能机械删除依赖，必须把实际现有 feature 显式移交给
直接 reqwest 声明并做独立包验证。该建议保留当前传输能力，不属于 feature
裁剪。模型 SDK、gRPC/protobuf、tracing adapter 和请求安全策略不变。

其余原 33 条候选仍存在；它们继续是待编译验证的候选，不能视作已经安全删除。

## CI 与架构规则

当前 root/BCS 指导、架构文档、单测 workflow 的本期相关约束未相对原基线变更。
总体覆盖率 >87%、变更行门槛和现有统一 Singlebox 验证要求保持。

`check-import-rules.sh` 已新增 Cargo 集成测试组合根的精确排除，同时保持
`src/**`（包括 src/tests）和契约内部依赖检查。计划新增运行上游
`scripts/ci/tests/import_scope_regression.sh`，不重做排除、不放宽生产扫描。

R25 checker 仍使用 `*/tests/conformance_*.rs` 范围；原计划的嵌套 conformance
发现修正仍然需要，保持缺失 harness、无入口时失败的要求。

## 本次验证及限制

- 已获取远端分支，并固定 SHA；静态统计基于该 SHA 的 archive。
- `cargo metadata --offline --locked --no-deps --format-version 1` 成功。
- `cargo metadata --offline --locked --format-version 1` 成功。
- 已逐项比对两个试点 target、依赖别名、迁移源码包含位置、超长文件和新增
  模块的 CONTEXT.md；编译依赖 feature 来源来自锁定包 manifest 与 resolved graph。
- 文档映射检查通过：60+53 个入口完整且不重复，34 条/21 包依赖候选与实际
  manifest 一致，29 处迁移源码包含与附录一致，12 个超长文件的行数匹配。
- 上游 `import_scope_regression.sh` 的 11 个正反例通过；计划中的嵌套 R25
  示例在临时目录中对原 checker 失败、对计划修正后的 checker 通过。
- 在额外的临时副本中试验删除 `a2a-client` 并显式保留 reqwest feature；离线
  metadata 解析成功，reqwest 0.13 的 resolved feature 集合不变，没有升级依赖，
  解析图仅移除了 `a2a-client-lf 0.2.9` 和 `auto_impl 1.4.0`。这不是编译或行为
  验证；原源码快照和当前工作区 manifest 未修改。
- 文档链接、空白、占位项以及 Python/TOML/Bash 示例语法检查通过。真实测试
  inventory、Rust 编译/单测、MySQL、Singlebox 及性能实验仍属于后续实施验证。

本记录不宣称清理已经完成，也不将上游已完成的文件拆分归为本计划的收益。
