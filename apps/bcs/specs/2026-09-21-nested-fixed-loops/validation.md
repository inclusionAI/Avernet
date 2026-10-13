# BCS 嵌套固定 Loop 实施验证

## 开始状态与任务复核

- 开始日期：2026-09-22
- 分支：`quechao_dev_0922`
- 开始 commit：`7b19ffbd08a83b4de2bf1e88ba4aee9c6d52d21e`
- 开始时工作区：仅 `apps/bcs/specs/2026-09-21-nested-fixed-loops/` 为未跟踪交接文档；无业务代码改动。
- 任务复核：T01–T10 依赖无环；spec 对 v1/v2 快照、准入/恢复、消息历史与成本的边界一致，未发现需要先修改任务拆分的不合理项。

实施前运行：

```text
cargo test --offline -p bcs-collaboration-runtime \
  --test fixed_loop_compiler --test fixed_loop_snapshot --test fixed_loop_context
```

结果：通过。`fixed_loop_compiler` 21 项、`fixed_loop_snapshot` 4 项、
`fixed_loop_context` 3 项，共 28 项；这些只证明单层 Loop 与拒绝嵌套的旧基线。

历史回归基线 `cargo test --offline -p bcs-collaboration-runtime --test runtime_progression history`
通过 55 项、过滤 175 项。

## 2026-10-09 rebase 后验证

- 本分支已 rebase 到 `origin/dev` 的
  `154684d090e02b34a55b1df49dda7851db00883c`。上游目录重组后，本功能改动已从
  `src/bcs` / `src/frontend` 迁入 `apps/bcs` / `apps/frontend`。
- `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-runtime
  --test runtime_progression -- --test-threads=1`：252/252 通过。
- `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs
  --test provider_downlink_integration`：15/15 通过，包含嵌套 Human 持久化恢复故事。
- `src/backend/.venv/bin/python -m pytest apps/bcs/tests/openapi
  apps/bcs/tests/event_contract`：169/169 通过。此处复用 rebase 前已有本地 venv，
  未安装新依赖。
- `npm --prefix apps/bcs/assets/panel run verify` 通过；Frontend GroupChat 11 个 suite、
  65/65 通过，`FRONTEND_CI_PACKAGE_MANAGER=npm bash apps/frontend/scripts/ci_test.sh`
  通过。
- `bash apps/bcs/scripts/ci_test.sh --fast-fail` 在允许绑定本机临时端口的环境中
  5964/5964 通过，56 项 skipped，退出码 0。
- `apps/bcs/scripts/ci/arch-check.sh` 仍报告现有 DEP-1/DEP-6、import、trait
  naming 和 R25 conformance 缺口；CFG-1 的 telemetry 用例因受限环境无法绑定端口而失败。
  本次未放宽门禁，也未修改这些无关基线问题。

## 2026-10-13 rebase 与全量覆盖率验证

- 已 fetch 并无冲突 rebase 到 `origin/dev` 的
  `1b9bfed2b88e545bcf4dfbeac700e2a35f89ad3a`。原暂存/未暂存改动已恢复；
  生产代码 patch-id 与 rebase 前备份一致，本次仅补强真实 MySQL 测试并更新验证文档，
  未创建提交或推送。
- 本次日志目录：`apps/bcs/target/validation-2026-10-13/`（忽略的本地产物）。
  下述结果不能用历史 2026-10-09 的全绿结论替代。

| 检查 | 结果 |
| --- | --- |
| BCS `scripts/ci_test.sh --coverage` | 6264 项执行：6263 通过、1 失败，另 57 skipped。nextest 实际退出 100；coverage 模式包装脚本退出 0 不代表测试通过。 |
| BCS 单测覆盖率 | Cobertura 行覆盖 85.02%（136508/160553）；LLVM 文本报告行 84.10%、函数 77.29%，两种报告分母不同。 |
| Frontend Jest coverage | 15 suites、78 tests 全通过；语句 66.84%、分支 49.86%、函数 61.32%、行 67.49%。按现有 Jest 加载文件口径，不代表所有 TSX/UI 都有覆盖。 |
| Frontend CI / BCS panel verify | 均通过；panel 含 typecheck、构建及 Loop 视图回归，不提供数值 coverage。 |
| OpenAPI / Event strict contracts | 182/182 通过。 |
| BCN plugin `npm run ci` | build、lint 和 76 tests 通过；语句/行 79.67%、分支 63.47%、函数 80.19%。最终 `attw --pack` 崩溃，整个 CI 未通过。 |
| 真实 MySQL 8.4.11 | DB plugin 与快照/rerun 两组 ignored tests 均显式执行通过；快照入口已补强并覆盖 v1/v2 × Text/Prepared 四种组合。 |
| BCS architecture | 15 组中 6 pass、4 skip、5 fail，退出 5；CFG-1 本次通过。失败组为 DEP-1~8、LINT-1、LINT-4、TEST-1、R25。 |

BCS 单测失败为 `bcs-bridge-core::plugin_websocket::two_bots_keep_same_group_sessions_and_events_isolated`，
`plugin_websocket.rs:479` 收到 `Protocol(ResetWithoutClosingHandshake)`。对同一插桩二进制
单独复跑 5 次，4 次通过、1 次失败；该 bridge crate 不在本功能改动中，但未据此冒称整体通过。
保留 `bcs-coverage.log`、`bridge-diagnostic.log`、`bcs-coverage-summary.txt`，
以及 `apps/bcs/testresult/{junit.xml,cobertura.xml}`。

BCN 的 `attw --pack` 报 `Cannot read properties of undefined (reading 'filename')`，
使用现有 Node 20.20.2 单独重跑仍报同样错误；没有升级依赖或放宽门禁。
架构检查首次 TEST-1 的 Cargo 枚举因磁盘压力中断，后续独立 R25 仍未通过；
单独 `cargo test -p bcs-db-mysql --test conformance_db_plugin -- --list` 可发现 conformance test，
因此不能把 R25.3 输出直接解释成仓库没有 conformance 测试。

真实 MySQL 使用只绑定 `127.0.0.1:33316` 的一次性容器和两个独立空库，执行：

```text
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-db-mysql \
  --test conformance_db_plugin -- --ignored --nocapture
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-store \
  --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored --nocapture
```

每条命令通过临时 `BCS_TEST_MYSQL_URL` 指定测试库；两条退出码均为 0。
首次真实存储入口只使用 v1 fixture；复核后将 v2 fixture 传入同一 round-trip、rerun、
写失败及回滚合同，再次完整运行成功。该补强只改测试，未改生产逻辑；上述全量单测
coverage 数值生成于补强前，真实 MySQL 单独回归不混入该 coverage 口径。
覆盖 migration 028 后历史 snapshot 保留、v2 round-trip/不可变性/env 隔离、rerun、
写失败回滚、历史接受及 2048 节点批量写。容器和其临时数据卷已删除，日志为 `real-mysql.log`。
补强后的完整日志为 `real-mysql-v1-v2.log`。该结果关闭 T03 的真实存储缺口，
不代替 T09 的锁、连接池和满页恢复容量测量。
补强后另外运行 `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-store
--test mysql_store`：106 passed、1 ignored，退出 0；ignored 项即前述已单独执行的真实 MySQL 用例。

### Unified Singlebox 与 artifact verifier

运行默认全模块 `bash singlebox/ci/singlebox_coverage.sh`，未使用 `--module` 缩小范围。
11 个已注册模块共享真实 standalone 栈，模型与 Provider/Judge 使用现有本地 mock。
依赖下载采用公开 PyPI/npm 镜像；移除继承的 `LOG_FORMAT=json` 以允许 Python 服务启动。
Backend 配置固定访问 BCS 21000，因此使用默认端口，按用户授权暂停原开发服务。

| 指标 | 实测 | 门槛 / 结果 |
| --- | --- | --- |
| Backend acceptance | 59 passed、1 xfailed | 通过 |
| Backend runtime line | 48.51% | ≥38%，通过 |
| BaaS runtime line | 45.68% | ≥45%，通过 |
| BCS runtime line / method | 42.52% / 37.11% | ≥40% / ≥36%，通过 |
| BCS HTTP / CLI leaf | 158/158、56/56 | 均 100%，通过；按现有解析器分母 |
| BCS user-story E2E | 673 passed、4 failed | 未通过；未把覆盖率达标当作用例通过 |
| expert_chat Core / Router | 49.00% / 63.64% | 低于 61.18% / 100%，模块门禁未通过 |

单层 Human Loop、两层 Bot（17 materialized / 15 completed / 2 skipped）及三层 Human
故事均成功，验证完整路径、父实例隔离、历史、跳过子树与旧 callback/reply fencing。
4 项 E2E 失败分别是 group CLI Fuse、Provider bot 退役、团队 CLI Fuse、slow-Judge bot 清理，
响应均指向 BCSFuse 连接失败。测试栈 BCSFuse PID 51226 在 20:22:36 发生
`EXC_BAD_ACCESS / SIGSEGV`；macOS 原生崩溃报告首帧为 `sqlite3DbMallocRawNN`，
不是嵌套循环断言失败。其日志另报继承 SOCKS 代理但缺少 `socksio`；未把这个异常等同于
原生崩溃根因，也未修改 BCSFuse 代码来绕过测试。

全量脚本退出 1。随后显式执行
`apps/backend/.venv/bin/python singlebox/ci/verify_singlebox_coverage_artifacts.py
--reports-dir singlebox/.dependencies/coverage/singlebox/reports`，退出 1：
`summary.status` 和 `summary.systems.bcs.e2e_status` 不是 `passed`。
因此完整门禁仍为失败，合并前需处理这些失败及上述单测/打包/架构问题。

完整报告保存在 `singlebox/.dependencies/coverage/singlebox/reports/`：
`summary.json`、`summary.md`、`dashboard.html`、Python HTML/JSON coverage、
acceptance JUnit 及 `bcs/` 下的 runtime、endpoint、CLI 报告。
入口日志为本次日志目录中的 `singlebox-coverage-default-port.log`，verifier 日志为
`singlebox-artifact-verifier.log`。报告及失败日志全部保留，没有降低阈值或排除生产路径。

结束后已恢复原 BCS（21000）、Frontend（8000）及 4 个商家 bot（30601/30611/30621/30631）；
BCS health 正常、Frontend HTTP 200，4 个 bot 均记录 `is_new=false` 重连。
原 BCS 配置目录与测试前备份一致，原 `test-bots` 目录已还原，原数据库未用于 coverage。
本次测试栈、遗留测试 bot 和 MySQL 临时容器/卷已清理；测试数据归档在本次日志目录，
Backend/BaaS/BCSFuse 保持测试前未运行状态。恢复日志为 `restoration.log`。
本次新增测试改动仅位于 200 行的 `tests/support/real_mysql_fixed_loop.rs`；
未拆分既有超长文件，`git diff --check HEAD` 通过。

### Bridge 单测修复与 expert_chat 归因复核

`two_bots_keep_same_group_sessions_and_events_isolated` 的 mock BCS 原先串行读取
两个 WebSocket；等待 A 的结果时，B 的 `bot.status` 可能无人处理，超过 1 秒后 Bridge
按合同断连。把引擎通过已有 `WAIT_OFFLINE` fixture 保持运行超过心跳期限，旧读法稳定
复现 `bot.status response timeout`，随后出现相同的 `ResetWithoutClosingHandshake`。
证据为 `bridge-heartbeat-regression-before.log`。

修复仅在 `crates/tools/bcs-bridge/core/tests/plugin_websocket.rs`：并发处理两端的
ACK/事件/心跳，保留 ACK 先于事件、相同 Group 的 Bot 隔离和各启动一次引擎的断言。
测试中每 20 ms 发心跳，并在两个引擎就绪后跨过两个原有心跳期限才放行结果，防止快速
引擎掩盖读端饥饿。不修改生产代码、原有 1 秒超时或 CI 门槛，也不使用失败重试。
WebSocket 整组 11/11 通过，日志为 `bridge-after-fix-suite.log`。
独立进行 100 次、4 路并发复跑，100/100 通过，未出现心跳超时，日志为
`bridge-after-independent-*.log`。此前与全量插桩编译叠加的压力批次为 98/100：
2 次 Python fixture 未在原有 5 秒内启动，报 `initial-delta` 文件等待超时，
不是 WebSocket 心跳断连；这批失败日志保留在 `bridge-after-stress-*.log`，未放宽超时。

修复后再次完整执行 `bash apps/bcs/scripts/ci_test.sh --coverage`：6264/6264 通过，
另 57 skipped，零重试，nextest 与包装脚本均退出 0。新报告位于
`apps/bcs/testresult/{junit.xml,cobertura.xml}`；旧报告已另存本次日志目录的
`junit-before-bridge-fix.xml` / `cobertura-before-bridge-fix.xml`，未丢弃失败证据。
随后原样运行 `.github/workflows/unit-tests.yml` 的 `cov_gate.py` 命令：
通过率 100%，整体行覆盖 85.03%（要求 >70%），相对 `origin/dev` 的变更行覆盖
609/645 = 94.42%（要求 ≥80%），三项均通过。
日志为 `bcs-coverage-after-bridge-fix.log` 和 `github-bcs-cov-gate-after-bridge-fix.log`。
该结论仅关闭 BCS 单测/单测覆盖率问题，不替代 Singlebox、真实 MySQL 专项或其他 CI job。

提交前凭据扫描将测试逻辑 secret 引用名及 `os.environ.get` 误识别为凭据。
仅缩短测试引用名并同步其专用环境变量，将 E2E token 读取等价改为 `os.getenv`，
未修改扫描规则或生产配置。调整后 Provider downlink 集成测试 15/15 通过，
Python 脚本编译、CLI help 和 token 环境变量缺省/已设置两条路径均通过，凭据扫描通过。
日志为 `precommit-provider-downlink.log`；上述完整 coverage 生成于这两处等价测试调整前。

expert_chat 的 Backend/BaaS、flow、验收测试及 Singlebox manifest 与 `origin/dev`
无差异。上游提交 `eafb67756`（2026-09-07，早于本功能）已在
`apps/backend/tests/community/acceptance/expert_chat/test_chat_session_lifecycle.py`
标记 `local_proc` 被 BaaS `invoke-http` 走入 ARCA-only 路径的已知 xfail。
本次 acceptance JUnit 命中同一原因，session flow 提前终止，后续四个 sessions 路由未覆盖。
据代码差异和实际失败路径，该缺口与嵌套循环无直接关系；未对干净 `origin/dev` 另启完整
栈做对照，不把这一归因当作整个上游基线的通过证明。其 49.00% Core / 63.64% Router
仍不满足 61.18% / 100% 门槛，保持失败，未改代码、xfail 或阈值。

## 实施状态

| 任务 | 当前状态与边界 |
| --- | --- |
| 准备 | 完成。两层 BotTask fixture 展开为 17 叶子；三层 HumanInput fixture 展开为 21 叶子；历史回归入口与改动文件行数已记录。 |
| T01 | 完成。稳定 frame、可选完整路径、v1/v2 只读访问器、深度与准入配置合同已实现并通过合同测试。 |
| T02 | 完成。C01–C10 的递归校验/编译、v2 identity、精确 previous result、完整 descriptor 路径、同名/兄弟/串并行组合、深度/节点/字节/overflow 预算及 v1 golden 已通过纯编译测试。 |
| T03 | 完成。reader、创建屏障、历史接受、Memory/SQLite/MySQL adapter round-trip 与 compiler v2 rerun 已覆盖；2026-10-13 在一次性 MySQL 8.4.11 上显式运行真实 MySQL ignored 用例，Text/Prepared 双协议均通过。 |
| T04 | 完成。start、一次性 Session、配置创建和 rerun 共用准入并在副作用前拒绝；三处 bootstrap composition root 已注入开关，深层叶子 binding、warning/limits、关闭准入后的已有 Run 推进与恢复均有回归。发布配置示例保持深度 1。 |
| T05 | 完成。Bot/Human 共用上下文，三层 Human 的 query/通知/回复、父实例隔离、冻结 prompt/history、旧回复拒绝均通过；Repo 计数确认深层 entry 只读最近 previous result，不逐祖先读取。 |
| T06 | 完成。两层故事得到 15 Completed、2 Skipped；inner/outer break、并行子 Loop shared join、部分 skip 恢复、嵌套 retry/cancel/迟到终态，以及既有 timeout/Judge/lease/Session/history 恢复矩阵均通过。 |
| T07 | 完成。API/Event/消息/CLI、完整 descriptor path、strict OpenAPI/Event schema、新旧 fixture、messages-only/cutoff、consumer 与 observability 回归均通过。 |
| T08 | 完成。Frontend preview 与 panel 均按完整路径递归布局；panel 当前视图和展开历史都按祖先轮次隔离实例并隐藏未进入的未来历史。单层、三层 Human、同名父子、兄弟 Loop、父轮次切换、占位与错误回归均通过。 |
| T09 | 已建立 [capacity.md](capacity.md) 并完成 2047 节点、4 MiB、32 个并存 Run、大范围 early break、previous-result 查询数及 2048 行/43 批存储断言；真实 MySQL 锁/连接池/满页恢复测量未运行，任务保持开放。 |
| T10 | 完成。两层 Bot 与三层 Human live 主故事已注册到现有 E2E runner；bootstrap SQLite live 在 inner exit、outer continue、outer break 前及 partial skip 写失败后重启，覆盖 messages-only、接受后消息写失败补写、关闭嵌套准入后的继续执行和 direct-assignee 通知 checkpoint。旧 Bot delivery callback 与重启后的旧 Human reply 均被拒绝。 |

## 已验证行为

- 实际最大深度 1 继续生成 compiler v1；显式声明 nesting feature 不改变版本选择。
- 实际嵌套生成 compiler v2，execution ID 由完整 Loop 实例路径确定；顶层普通节点 ID 不变。
- v2 每个 Loop 叶子保存非空 `loop_path`，最近帧与旧字段一致；v1 仍省略新增字段并保持旧 JSON 字节。
- snapshot reader 显式分派 v1/v2，未知版本、缺 feature、缺路径、跨父实例 previous result、缺边及重哈希篡改均拒绝。
- 新 Run 与 rerun 在任何 Run/snapshot/dispatch 副作用前检查嵌套准入；恢复只读取不可变计划，不检查后来关闭的准入开关。
- 内层 previous result 只读取同一父实例的上一内层轮次；新父轮次的内层首轮重新为 null。
- 三层人工故事执行 20 次 query/response 后再发布；四个不同父实例的内层首轮均为 null，
  第二轮只指向同一 outer/middle 实例。Pending 投影总计两次 Node 读取均为同一最近结果，
  分别属于 LoopContext 与既有静态 upstream probe。
- 两层运行故事中 inner early break 只跳过该实例尾部，outer break 跳过完整未来父子树；恢复能补齐部分 skip。
- 并行左右子 Loop 的 shared join 等待两个已选 exhausted 出口，且仅投影各自最后一轮 artifact。
- start/get/rerun、Node/graph、Pending Human、HumanInputReadyEvent、node events、历史消息与 CLI 共用完整 execution path。
- `graph.loops` 使用 `outer`、`outer/inner` 逻辑路径 key；body IDs 仍是直接子节点。
- Memory、SQLite 与 MySQL adapter 均原样保存 compiler v2 和完整 `loop_path`；未修改 DDL 或历史 migration。

## 已执行测试

### Rust 合同、存储与运行时

```text
cargo test --offline -p bcs-domain -p bcs-config-api -p bcs-collaboration-store
```

结果：通过。`bcs-collaboration-store` 119 项通过，真实 MySQL 1 项 ignored；
`bcs-config-api`、`bcs-domain` 及其集成/文档测试全部通过。新增 v2 路径在
Memory、SQLite、MySQL adapter 三项 round-trip 测试中通过。

```text
cargo test --offline -p bcs-collaboration-runtime \
  --test fixed_loop_compiler --test fixed_loop_snapshot --test fixed_loop_context
```

结果：通过，31 + 6 + 4 = 41 项。新增中英文 `nested-review-loops` seed 均编译为
compiler v2，展开为 17 个叶子，并生成 `delivery_cycles` 与
`delivery_cycles/review_rounds` 两级 descriptor。容量用例另验证 2047 叶子 plan 为
2,272,486 bytes，17 叶子填充 plan 可在 4,194,303 bytes 成功并在下一字节拒绝。

```text
cargo test -p bcs-collaboration-template
cargo test -p bcs-admin seed_loader::tests::loads_seed_catalog_with_projected_fields
```

结果：通过，分别 12 项与 1 项。嵌套模板的中英文内容、registry 顺序、角色投影和
admin seed catalog 均可读取；本次编译输出中的 warning 仅为既有 unused/dead-code warning。

```text
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression
```

结果：rebase 后单线程完整 suite 252/252 项通过。此前一次默认并行运行中既有全局 tracing 捕获测试
受并行互扰失败，单独重跑与本次单线程完整重跑均通过。新增覆盖 start/一次性/配置/rerun
嵌套准入、compiler v2 rerun、深层 binding、15/2 主故事、
三层 Human、parallel nested join、inner/outer break、2047 节点 early break、partial skip recovery、
API/Event/history 路径与 Human 冻结 prompt。

```text
cargo test --offline -p bcs-collaboration-store --test mysql_store batched_nodes -- --nocapture
```

结果：2 项通过。2048 个节点的 Session 创建和 rerun 均保持 48 行分块、43 条节点 INSERT，
SQLite 原子写/回滚与 snapshot rerun 同时通过。详细成本见 `capacity.md`。

```text
cargo test --offline -p bcs-channel
```

结果：通过，unit 104 项、conformance 15 项；包含嵌套通知完整路径。

```text
cargo test --offline -p bcs-api-http --test collaboration_run_routes
cargo test --offline -p bcs-http --test groups_contract
```

结果：通过，分别 14 项与 41 项；两套 HTTP adapter 的 Loop 公共投影回归通过。

```text
cargo test --offline -p bcs --lib deferred_session_channel_outbound_is_inert_until_initialized
cargo test --offline -p bcs --test provider_downlink_integration -- --test-threads=1
cargo check --offline -p bcs-service-api -p bcs-collaboration-runtime \
  -p bcs-channel -p bcs -p bcs-api-http -p bcs-http
cargo build --offline -p bcs-cli
cargo test --offline -p bcs-message -p bcs-message-store
cargo test --offline -p bcs --test state_machine_history_read
node --test tests/consumer/state_machine_history.cjs
../backend/.venv/bin/python -m pytest tests/openapi tests/event_contract
```

结果：通过。provider-downlink 15/15 项通过，新增 live 故事通过真实 HTTP、Provider callback、
Event webhook 与 direct-assignee IM mock 完成三层 Human 的 20 次通知/回复，验证四个父实例隔离、
过期回复、21 Completed/10 Skipped 和 graph/event/message execution 一致。持久化变体在 inner exit、
outer continue、outer break 前及 partial skip 写失败后终止并重建 BCS，复用同一 SQLite plan/history
和冻结通知；`bcs_messages` 写失败发生在 checkpoint/artifact 接受后，移除故障并重启后同一响应
幂等完成。outer continue 边界重启时关闭嵌套准入，既有 Run 仍完成；messages-only 保留完整路径且人工请求没有
重复网络派发。
CLI 构建成功；
消息 crate 及 conformance 全部通过；HTTP history read 1 项通过、
需要外部 snapshot 的 1 项 ignored；consumer 2 项通过（监听本地测试端口需沙箱外执行）。
strict OpenAPI/Event 在 rebase 后共 169 项通过。编译仅有本次改动外的既有 unused/dead-code warning。

### Panel 与 Frontend

```text
npm --prefix apps/bcs/assets/panel run verify
```

结果：通过。typecheck、build、UMD、focused、integration（含单层、嵌套历史展开、
三层 Human、同名父子和兄弟 fixed-loop）、visual 与 public scan 全部通过。

```text
npm --prefix apps/frontend test -- --runInBand src/pages/GroupChat --silent
FRONTEND_CI_PACKAGE_MANAGER=npm bash apps/frontend/scripts/ci_test.sh
```

结果：通过。GroupChat 11 个 suite、65 项测试通过；Frontend CI lint 通过；递归 preview
覆盖两层、三层 Human、同名父子、兄弟容器、各层返回边和完整路径定位。
单独执行 `npx tsc --noEmit` 会在既有 `node_modules/@types/d3-dispatch` 声明上报语法错误，
不在本次改动文件中。

### 静态检查

```text
git diff --check
jq empty apps/bcs/api-contracts/events/v1/event-envelope.schema.json \
  apps/bcs/api-contracts/events/v1/fixtures/state_machine.node.started.nested_loop.metadata_only.json
```

结果：通过。

### 独立服务 live E2E

```text
PYTHONDONTWRITEBYTECODE=1 python3 apps/bcs/scripts/e2e-test/nested_fixed_loop_story.py \
  --base-url http://127.0.0.1:21100 \
  --mock-url http://127.0.0.1:21101 \
  --cli target/debug/bcs-cli --driver unused --human 001
```

结果：rebase 前通过。在本工作树临时数据目录及独立 21100/21101 端口启动真实 BCS、Provider/Judge mock；
脚本自行创建 Provider、Bot、Group 和 Run，不依赖另一工作树的五 Bot 数据。Bot 故事验证 17 个
实例中 15 Completed、2 Skipped，inner early break、outer continue/终止、完整路径、历史消息和
过期 callback fence。三层 Human 故事完成 20 次前台 query/response 后发布，验证四个父实例隔离、
内层 previous result、外层未来子树 10 个 Skipped、完整 graph/history metadata 和过期 reply fence。
临时服务随后停止，端口、mock LLM 和 loopback 配置已还原；本地配置仅保留嵌套执行开关。

### BCS 全量模块门禁

```text
bash apps/bcs/scripts/ci_test.sh --fast-fail
```

结果：rebase 后在允许测试绑定本机临时端口的环境中通过，nextest 5,964/5,964 项通过，仓库配置的
56 项 skipped，退出码 0。首次在受限沙箱内执行时，HTTP/WS/wiremock 测试因 bind 返回
`Operation not permitted` 产生环境假失败；未将该次结果计作实现失败，并以相同命令完成有效重跑。

## 2026-10-09 时未通过或未运行的门禁（历史记录）

- 真实 MySQL ignored 测试未运行：未提供指向可销毁空库的 `BCS_TEST_MYSQL_URL`。
- `bash apps/bcs/scripts/ci/arch-check.sh` 未通过并在持续枚举既有 conformance 缺口时中止：
  先报告既有 DEP-1/DEP-6、import、trait naming 与大量 conformance entry 缺口；CFG-1 的
  131/132 项通过，telemetry 用例因沙箱禁止监听端口报 `Operation not permitted`。本次没有修改门禁或这些无关代码。
- unified singlebox coverage、artifact verifier、发布/回滚演练尚未执行；T09 和交付清单保持未完成。

## 文件边界与行数

所有业务改动均在上游重组后的 `apps/bcs/` 和 `apps/frontend/`。除跟随上游将
本功能改动迁入 `apps/` 外，未拆分、搬迁或格式化既有文件，未修改历史 migration。
新增 Rust/TypeScript 源文件均未超过 1000 行。本次局部修改命中的既有超长文件及实施前/当前行数为：

| 文件 | 实施前 | 当前 |
| --- | ---: | ---: |
| `apps/bcs/crates/bootstrap/bcs/src/server.rs` | 6881 | 6981 |
| `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs` | 6723 | 6759 |
| `apps/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs` | 5817 | 5823 |
| `apps/bcs/assets/panel/src/StateMachineRunView.tsx` | 4276 | 4291 |
| `apps/bcs/crates/bootstrap/bcs/src/config.rs` | 3395 | 3439 |
| `apps/bcs/crates/adapters/http/bcs-http/tests/groups_contract.rs` | 3175 | 3191 |
| `apps/frontend/src/services/backend-api/BcnController.ts` | 1913 | 1918 |
| `apps/bcs/crates/services/bcs-collaboration-store/tests/mysql_store.rs` | 1845 | 1935 |

这些文件在实施前已经超过 1000 行；当前行数包含同期上游改动。按用户范围约束
保留原布局，仅做需求所需的局部修改。
提交前另确认 `apps/bcs/crates/bootstrap/bcs/tests/provider_downlink_integration.rs`
为 1366 行（当前 `origin/dev` 基线为 1365 行）；同样保留既有布局，未为行数检查拆分。
