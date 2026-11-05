# BCS 代码精简与构建体积调查

日期：2026-11-05。基线：`520be5ed6e`。状态：调查结果，尚未实施清理。

## 结论

优先精简重复编译、重复测试注册和不必要的依赖，再处理业务代码。
目前最明确的机会是迁移源码被跨 crate 重复包含、过多独立集成测试
target、生产代码引入整个测试支持库，以及一个无调用者的旧 callback crate。
没有证据支持直接删除某个大型业务模块。

本次没有修改 Rust、Cargo 配置或 CI，没有删除本地缓存。当前工作区没有
`apps/bcs/target`，因此无法给出本机已有 Rust 产物的分类体积；以下收益均未做
构建前后对照测量，不能据此承诺具体 GB 或耗时降幅。

## 基线与统计口径

| 项目 | 结果 | 口径 |
| --- | ---: | --- |
| 显式 workspace members | 110 | 根 Cargo.toml |
| Rust 文件 | 2,245 | Git 跟踪文件 |
| Rust 物理行 | 698,224 | 包括注释、空行和测试 |
| 显式测试文件中的物理行 | 327,836 | tests 路径、tests.rs、*_tests.rs；不含所有内联测试 |
| library targets | 108 | Cargo metadata |
| integration test targets | 595 | Cargo metadata 的 kind=test；不是测试用例数 |
| binary targets | 7 | 包含受 feature 控制的 E2E binary |
| 解析后的包数 | 635 | 包括 workspace、开发依赖及平台相关包，不等于单次实际编译数 |
| 超过 1,000 行的 Rust 文件 | 64 | 存量情况，不代表均可合并或删除 |

集成测试 target 最多的 crate：`bcs` 74、`bcs-message-flow` 60、
`bcs-http` 52、`bcs-service-api` 37、`bcs-bot-store` 32、`bcs-bot` 31。

本地 `apps/bcs` 占约 141 MiB，其中 panel 的 `node_modules` 约 96 MiB。
Git 跟踪的 panel 文件合计仅 633,140 字节。目录磁盘大小不能直接用来推断
Rust 可执行文件大小，也不能据此删除 panel 功能。

## 优先处理清单

### 1. 去掉 migration 测试的重复编译和注册

证据：

- `crates/bootstrap/bcs/src/migrations.rs:673` 起通过 `#[cfg(test)]` 挂载
  六个测试模块；其子树共定义 49 个 `#[test]` / `#[tokio::test]`。
- 仓库有 29 处 `#[path = ".../bootstrap/bcs/src/migrations.rs"]`，分布于
  store、application、message-flow、collaboration-runtime 和 admin 的测试。
- 例如 `crates/services/bcs-event-store/tests/conformance_event_store.rs:13`
  和 `crates/services/bcs-message-flow/tests/conformance_direct_a2a.rs:16`。

这些测试 crate 包含迁移源码时也启用了 `cfg(test)`，不仅复制迁移执行器，
还会带入它的迁移测试。29 是源码包含位置数，不能直接当作独立二进制数；
实际重复注册数应以 nextest inventory 核对。

建议把迁移执行器移到独立基础设施库，由 bootstrap 和测试通过普通库依赖
调用；迁移测试只在所属库运行一次。各业务集成测试仍然必须初始化真实完整
schema，并保留自己的断言。这会减少重复源码编译和重复迁移测试执行。

边界：不修改已提交迁移的 SQL、版本、顺序、名称、checksum，也不引入
跳过迁移链的测试专用 schema。需要同步架构依赖规则，并验证 SQLite 和真实
MySQL 的完整迁移链。移动后迁移测试的编译期相对路径也要更新。

### 2. 收敛集成测试入口，保留用例

595 个独立 test target 分散了编译和链接工作。优先在 `bcs-message-flow`
和 `bcs-http` 内按业务域组织少量 suite 入口，原文件成为子目录模块。
先做一个 crate 的对照实验，再决定是否推广；不跨 crate 合并架构边界。

此外：

- `crates/bootstrap/bcs/tests/e2e_helpers.rs` 有 944 行辅助代码，没有测试
  定义或测试子模块，但 Cargo 确认将其识别为独立 `e2e_helpers` test target。
- 它被六个测试入口通过 `mod e2e_helpers` 使用。移到 support 子目录并更新
  引用，可以去掉这个无用入口；辅助函数本身仍要保留。

风险与验证：合并可能改变全局状态、日志 subscriber、环境变量及端口的
隔离方式，需逐项检查。保留需要进程隔离的入口。CI 的 MySQL 定向执行依赖
binary/test 名称，若改名需同步调用者。比较归一化后的用例清单、ignored
契约清单、通过率和覆盖率；不要只比较总数量，因为第一项会消除重复注册。

### 3. 从生产依赖中分离测试支持代码

证据：

- `crates/test-support/bcs-test-support` 共 19,606 行 Rust，其中 `src/contract`
  为 14,624 行；lib.rs 无条件导出 contract、authority fixture 等模块。
- 该包出现在 66 条 workspace 直接依赖声明中，包括普通依赖与 dev-dependency
  的重复声明，不能解释成 66 个不同消费者。
- `crates/adapters/http/bcs-http/Cargo.toml:52` 和
  `crates/bootstrap/bcs/Cargo.toml:175` 将它作为非可选普通依赖。
- HTTP 的 `src/state.rs:594` 使用其中 NoopConnectService 和
  NoopAdmissionService；bootstrap 的若干内存装配路径也使用它们。
- 沿 manifest 中非可选普通依赖递归，test-support 可达 31 个其他 workspace
  crate，包括具体 service/store 和 SQLite plugin。
- 它自己的 CONTEXT.md 声明不参与生产装配，并禁止 shared harness 依赖具体
  services/plugins；当前实现已超出该边界。

建议把生产需要的 Noop 实现放回适当的运行时实现层并通过 composition root
注入，使 test-support 回归开发依赖；将契约 harness 与依赖具体数据库/业务
实现的 fixture 分开。共享 fixture 的划分需避免反向依赖和循环依赖。

收益是减少不必要的编译依赖和变更传播。不能把 19,606 行全部算成最终
binary 的可回收体积：链接器可能已经移除了未引用代码，而完整 workspace
测试仍需要编译并执行契约 harness。

### 4. 共享测试 fixture 从源码包含改成库复用

`crates/test-support/message_flow_contract_support.rs` 被 40 处 `#[path]`
包含，它引入的五个文件共 1,302 行；message-flow 的
`tests/support/session.rs` 另有 30 处 `#[path]` 包含。

这些文件虽然在磁盘上只有一份，仍会随不同测试 crate 重复编译。优先结合
测试入口收敛消除重复；如果仍需跨 crate 复用，再提取职责明确的测试 fixture
库。不要直接继续扩大现有 test-support 的具体业务依赖。

### 5. 删除旧 bcs-callback 空壳

`crates/services/bcs-callback` 没有 workspace 反向依赖。
`src/lib.rs` 仅导出 legacy；legacy.rs 的两个异步函数只打印 warning，
两个测试只调用这些空桩。共 63 行 Rust。

这是高置信度删除候选：同步移除 workspace member、workspace dependency、
更新锁文件和当前 README。收益有限，主要是减少无效维护和一个测试 harness。
历史设计记录应保留，不需要改写成“当时不存在”。仍需确认没有仓库外消费者
把它当作发布的独立库使用。

相关但不能直接一起删除的接口：WS 的 `TaskCallbackHook` 没有找到实现，
bootstrap 传入 `task_callback: None`；但它是公开导出的接口，应按兼容性清理
单独处理，不能仅凭名称相同认定它就是该 crate 的调用者。

### 6. 清理无引用的依赖声明

静态扫描在 20 个包发现 33 条普通依赖声明，其依赖标识符未出现在该包 Rust
源码中。该扫描只给出候选，不替代编译验证，也可能遗漏宏生成或注册机制。

优先核对：

| crate | 候选依赖 |
| --- | --- |
| bcs | dirs、form_urlencoded、if-addrs、native-tls、regex、tokio-native-tls |
| bcs-cli | base64、bcs-config-api、dirs、encoding_rs |
| bcs-auth-github | axum、bcs-jwt、serde_json |
| bcs-auth-google | bcs-jwt、serde_json |
| bcs-ws | bcs-route-security |
| bcs-routing | serde、serde_json |

逐包删除候选声明后，检查所有 target 和 feature 组合，并运行最近的测试；
另用 workspace 构建排查 feature 统一掩盖的问题。仅删除直接声明不一定让
传递依赖消失。例如 native-tls 仍被 mysql_async 和 WS 客户端使用。

### 7. 依赖版本与 feature 精简作为后续工作

`cargo tree` 确认：

- tokio-tungstenite 0.26.2：bootstrap、bridge、rule-bot、ding-logger 等直接
  使用；0.29.0 由 axum 0.8.9 带入。
- reqwest 0.12.28 与 0.13.5 共存；后者来自 bcs-a2a-http / a2a-client-lf。
- workspace 全局启用了 tokio/full 和 axum/ws。

可评估统一 WS 客户端版本、按实际消费者启用 framework feature；HTTP 客户端
统一需要检查 A2A SDK 的类型和接口约束。不能把 Cargo.lock 中所有多版本项
视为错误，也不能为了精简直接移除 MySQL、OAuth、A2A 等有效能力。
feature 收窄更可能帮助聚焦本地构建，完整 workspace 测试中的 feature 统一
可能抵消收益，必须实测。

## 不应重复做或误做的事

- `.github/workflows/unit-tests.yml:126` 已设置 dev/test 的
  `line-tables-only`，并设置 `CARGO_INCREMENTAL=0`。不能把这些当成新的 CI
  优化成果。本地 Cargo.toml 尚未配置同等 profile；是否采用更轻调试信息
  应结合调试需求单独决定。
- `scripts/ci/reusable_coverage.sh` 已导出 instrumented build 的 metadata，
  后续 MySQL 契约复用它。此前调查见
  [2026-10-29-bcs-unit-ci-performance.md](2026-10-29-bcs-unit-ci-performance.md)。
  其中历史执行结果和门槛不是本次基线，当前以 CI/AGENTS.md 为准。
- 不通过降低覆盖率门槛、删除唯一契约场景或排除生产路径来降低成本。
- 不因 migration 旧、目录名含 legacy/v1、文档有 sunset 日期就直接删除。
  HTTP CONTEXT.md 仍明确要求若干 legacy alias 和注册兼容行为。
- 不把“拆分 4,864 行的 CLI client.rs”等同于降低 binary 体积。拆文件有维护
  价值，但构建收益必须来自依赖、编译单元或重复实现的实际减少。
- 不为压缩 crate 数合并 Service API、Plugin API、Core、adapter 的强制边界。

## 实施选择与推荐顺序

1. **小范围清理**：callback 空壳、误发现的 helper target、逐包无引用依赖。
   风险较低、容易验证，但不应期待解决全部体积问题。
2. **编译与测试结构精简（推荐主线）**：先迁移测试去重，再试点测试入口收敛、
   拆开生产 Noop 和测试 fixture。最直接针对已发现的重复工作。
3. **功能裁剪/可选构建**：按产品需要选择可选插件和工具，最后评估。
   需要明确发行能力矩阵，改动和验证成本最高，不作为默认删代码方案。

先做 1，再分批做 2；每一批独立验证和回滚。3 只在已有证据表明仍需裁剪且
产品范围明确后进行。

## 验证记录和下一轮验收

本次已完成：

- `cargo metadata --no-deps --format-version 1 --offline`。
- `cargo metadata --format-version 1 --offline --locked`。
- `cargo tree --offline --locked -i ...` 核对 WS、reqwest 和 TLS 反向依赖。
- Git 跟踪文件、源码引用、Cargo targets、测试声明和本地目录体积的静态统计。
- 对照模块指导、架构约束、当前 workflow 和此前 CI 优化记录。

本次未运行 Rust 编译、单测、覆盖率、MySQL 或 E2E：交付内容是调查文档，
尚无实现变更，当前工作区也没有可用 Rust target 可做产物采样。

实施前后应在同一工具链、平台、profile 和测试范围下比较冷/热构建时间、
target 的 deps/build/incremental 分类体积、测试 binary 总体积和最终服务 binary
体积，并保存 nextest inventory。保留所有唯一行为及兼容性用例，执行受影响
crate 的测试、架构门禁、当前完整 BCS 单测覆盖率门禁；涉及数据库或跨模块
装配时补齐真实数据库与 Singlebox 验证。不得把静态候选当作已验证删除成果。
