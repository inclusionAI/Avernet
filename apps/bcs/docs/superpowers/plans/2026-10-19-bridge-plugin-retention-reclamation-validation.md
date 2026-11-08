# Bridge Plugin Retention Reclamation — Validation

> 日期：2026-10-19
> 状态：本地实现已验证；代码与记录随本次修复变更交付，未发布
> [Spec](../specs/2026-10-19-bridge-plugin-retention-reclamation-design.md)
> [Execute Plan](2026-10-19-bridge-plugin-retention-reclamation-execute-plan.md)

## 1. 实现范围

- `run_buffer.rs`：`VecDeque`、逐记录计费、受控连续消费提交、游标检查和保留高水位。
- `run.rs`：不可变保留模式，消费前缀回收接口、错误记录计费与单次余量、完成/超限诊断。
- `plugin.rs`：成功本地写入或既有过滤后回收；发送失败/超时/取消不提交。
  发送前校验 pending、route 和 buffer cursor；路由过期后的 tombstone 使用已消费游标。
- `runtime.rs` / `webhook.rs`：显式 `RunDelivery`，Plugin 不创建 Gateway stream；
  Gateway 先订阅后启动并保持完整重放。
- QwenWork runtime 测试适配 Rust 返回类型；CLI README 记录保留与可靠性边界。

Rust 源码接口调整包括 `RuntimeReply::Run.delivery`、返回 `Result` 的
`forward_stream` / `next_after`、封装后的 buffer 及新增模式/统计类型；不是 wire
协议变更。未新增配置项、数据库查询/写入、迁移、网络 RPC 或引擎业务逻辑。

原 `run.rs` 中的测试移至 `run_tests.rs`，保留原 Rust 测试模块路径。新增记录的
计费也包含保存字节数所需的结构体开销，不通过排除 thinking 降低统计。

## 2. 先失败、再通过的回归证据

`plugin::retention_tests::consumed_plugin_stream_can_exceed_twenty_thousand_records`
直接调用生产 `flush_events()`，每条事件都发送成功后才继续生产：

- 接入回收前：在第 **8193** 条失败，断言为已消费历史仍耗尽 retention。
- 接入回收后：**20,000** 条增量和一个正常 final 全部通过。
- 同一路径的两组合成故障构成（6924/424/844 与 7830/316/46）均通过，
  8192 条被消费后仍能正常发出 final，不含真实会话数据。
- 数量上限追加翻倍后，无消费对照改为保留 16384 条正常记录和一个
  `buffer_overflow`；字节上限仍为 32 MiB，未删除资源保护。

## 3. 最终公共测试

在工作树独占的 Cargo target 中执行，设置 loopback 不经过代理：

```bash
cargo test --offline --locked --no-fail-fast \
  -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli -p bcs-bridge-qwenwork
```

结果合计：**363 passed / 2 failed / 2 ignored**。

- core lib：159 passed / 2 failed / 1 ignored。
- 新 runtime retention 集成测试：4 passed。
- Plugin WebSocket：16 passed；Gateway webhook：23 passed；encoder contract：7 passed。
- 其他 app/CLI、golden frame、runtime、Codex 和 QwenWork 自动化用例通过。
- 被忽略的 200,000 条压力用例已在下一节显式运行并通过。
- QwenWork 实机问答用例需要真实桌面及私有配置，未执行。

### 3.1 两个宿主相关失败

失败测试：

1. `engine::cli::tests::cli_session_enlarges_stdout_pipe_for_long_jsonl_events`
2. `engine::cli::tests::cli_session_reads_nonblocking_long_jsonl_line_without_truncation`

当前宿主 stdout pipe 只能保持 4096 字节；第二个用例读取 4096 而非预期 7068。
在未修改的 `25e8027156d807335e24dca7b8d4c413427113e5` 检出执行：

```bash
cargo test --offline --locked -p bcs-bridge-core --lib \
  engine::cli::tests::cli_session_ -- --nocapture
```

结果为同样两个失败、另外两个通过。未修改相关引擎实现、测试或宿主配置来掩盖失败。
因此本次不能宣称整个公共套件全绿，但没有发现本改动引入的失败。

### 3.2 内部发行验证限制

对下游发行包执行锁定依赖测试时，Cargo 在编译前报告锁文件需要更新。
未修改的主检出（`09838ac3713f2db097e83ca6a216e1ef69abba39`）同样失败。
未更新发行锁文件，也未将该验证记为通过。该项需下游锁文件恢复一致后补跑。

## 4. 长流资源观测

显式运行同一生产 flush 路径的 20,000 / 200,000 条合成事件用例，
使用有界测试 collector，消费每条后清空捕获结果。通过独立子进程的 `wait4`
记录 Linux `ru_maxrss`：

| 输入增量条数（另加 1 条 final） | 结果 | 保留记录高水位 | 进程峰值 RSS | 单次耗时 |
| --- | --- | ---: | ---: | ---: |
| 20,000 | passed | 1 | 13,876 KiB | 1.234 s |
| 200,000 | passed | 1 | 13,736 KiB | 12.419 s |

每次成功消费后断言 retained bytes/events 为零，序号继续递增，final 正常送达。
10 倍累计事件量未呈现线性 RSS 增长；两次 RSS 差异属于单次环境/分配器波动，
不能解读为更长任务反而必然使用更少内存，也不是通用 RSS 上限保证。

这项测试使用可控 Sink，直接调用 flush，不包含生产 20 ms 定时调度或真实网络。
耗时不作为线上吞吐指标；真实握手、重连与 wire shape 由 WebSocket 套件覆盖。

可显式重跑压力测试：

```bash
cargo test --locked -p bcs-bridge-core --lib \
  plugin::retention_tests::consumed_plugin_stream_two_hundred_thousand_records \
  -- --exact --ignored
```

## 5. 验收映射

文件路径相对于 `crates/tools/bcs-bridge/core/`：

| Spec ID | 证据 |
| --- | --- |
| AC-01 | `src/plugin_retention_tests.rs` 长流；`tests/plugin_retention.rs` 公开 runtime 长流 |
| AC-02 | `run_buffer::tests::consuming_prefix_updates_exact_accounting_and_preserves_sequence` |
| AC-03 | buffer 幂等/越界/下溢测试；Plugin `inconsistent_cursors_are_rejected_before_any_network_write` |
| AC-04 | Plugin `failed_timed_out_and_cancelled_writes_keep_the_pending_record` |
| AC-05 | 既有 WebSocket 重连测试、runtime 长流幂等测试和过期 route/tombstone 测试 |
| AC-06 | 既有 raw 字节预算测试、无消费 count 测试、单次错误余量及消费归零测试 |
| AC-07 | 合成故障工具事件对照、encoder/golden frames、工具/HITL/QwenWork 套件 |
| AC-08 | `run::tests::concurrent_producer_consumer_preserve_final_and_accounting`、runtime abort 和既有终态测试 |
| AC-09 | Plugin 过滤/V2→V3 重新协商测试、WebSocket V2 fallback 与 V3 interaction 测试 |
| AC-10 | Gateway 双读取者测试、既有同 ID 重放与 broadcast lag 测试 |
| AC-11 | 原 delivered/tombstone/grace 测试；新增 expired-route 恢复游标测试 |
| AC-12 | 两组脱敏事件构成、多 run 测试与既有两 Bot 同组隔离测试 |
| AC-13 | runtime Plugin 无 Gateway subscriber、Gateway 启动前已有 subscriber 的断言 |
| AC-14 | overflow 文本字段断言、短锁内一致计费、上节资源观测及热路径无 DB 调用的代码核对 |

局部静态检查：`git diff --check` 通过；新建文件单独检查尾随空白和最终换行；
新增/修改 Rust 文件均不超过 1000 行，未运行全局 formatter。文档本地链接已检查。

## 6. 未完成事项

- 代码和文档随修复提交交付；Git/PR 记录为提交与推送状态的依据，尚未部署。
- 下游发行包锁文件不一致，尚未完成发行验证。
- 尚未进行生产部署、真实桌面实机测试或升级/回滚演练。
- 本修复不改变本地写入与服务端持久化 ACK 的区别，不提供进程崩溃恢复。
- Gateway 的累计历史上限、批量投递和 delta 合并均保持为后续范围。

## 7. 事件数量上限追加调整

按用户确认将共享事件数上限从 8192 翻倍为 16384，保持 32 MiB 字节预算不变。
Plugin 和 Gateway 使用相同的新数量常量，但只有 Plugin 按消费游标回收。
无消费错误边界相应改为第 16385 条；原始故障统计及首次红灯记录仍描述旧版本。
追加调整后的四个 bridge 包全套测试已执行，并在提交前复核日志：**363 passed / 2 failed / 2 ignored**。
16384 条积压边界单测、公开 runtime 无消费边界测试及 20,000 条消费长流测试均通过。
两个失败仍为第 3.1 节列出的 stdout pipe 环境限制；本轮未改动这些测试或引擎实现。
第 4 节的 200,000 条压力测试和 RSS 数据来自数量上限调整前，未将其标记为新上限下重测。
