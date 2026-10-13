# 嵌套固定 Loop 容量与数据库成本记录

- 测量日期：2026-09-23
- 代码基线：本工作区未提交实现
- 构建模式：本机 Rust `test`（debug），Memory/SQLite/RecordingDb；结果用于回归边界，
  不作为生产吞吐承诺。
- 默认限制：深度 4、每层最多 32 轮、2048 个编译叶子、plan 4 MiB。

## 可重复测量结果

| 场景 | 实际结果 | 回归断言 |
| --- | --- | --- |
| spec 两层示例 | 17 个 materialized Node Run；指定轨迹 15 Completed、2 Skipped | runtime 主故事与 seed compiler 测试 |
| 接近节点上限 | 外层 31 轮、内层 32 轮，编译 2047 个叶子；plan 2,272,486 bytes；debug 编译约 189 ms | 叶子数必须为 2047；节点上限保持 2048 |
| 接近 plan 上限 | 二分构造 17 叶子嵌套 plan，4,194,303 bytes；debug 编译约 190 ms；再增加 1 byte 输入即拒绝 | 上限内成功，上限外失败；默认仍为 4 MiB |
| 32 个并存 Run | Memory 中 32 个 Running Run、544 个 Node Run、32 个首节点 dispatch；顺序创建约 352 ms | Run ID 唯一且每个 Run 完整 materialize 17 个节点 |
| 大范围 early break | 2047 叶子中最终 5 Completed、2042 Skipped；外层 break 阶段跳过 1980 个未来父子节点，约 961 ms；整条故事约 5.17 s | `skip_node` 1980 次；该阶段 `get_run<=12`、`list_node_runs<=8`、`get_node_run<=2100` |
| 三层 previous result | 查询内层第二轮 Pending 时，2 次 `get_node_run` 都指向最近内层上一轮结果 | 1 次 LoopContext 结果读取 + 1 次既有静态 upstream probe；不逐祖先读取 |
| 2048 节点 Session 创建/rerun | RecordingDb 均为 43 个节点 INSERT；每条最多 48 行/818 bind 参数 | 创建事务为 2+43 steps，rerun 为 4+43 steps；SQLite 原子回滚与 rerun 同样通过 |

上述毫秒数来自一次本机 debug 运行，测试只断言结构、次数和边界，不断言机器相关耗时。

## Repo、SQL、行数与事务边界

1. `create_run_if_session_idle` 的 2048 个 Node Run 使用 `ceil(2048/48)=43` 条节点
   INSERT，加 1 行 Run 与 Session guard；全部位于一个事务。rerun 仍在一个事务内，增加源 Run、
   snapshot 与 Session 相关的固定步骤，不逐节点开事务。
2. 2047 节点 early break 并非 O(1)。本次轨迹先在内层 early break 跳过 62 个节点，随后外层
   break 对未来 30 个父实例调用 1980 次 guarded `skip_node`；其余 Repo 读取来自既有推进、
   barrier、投影和最终发布。当前没有足够依据引入 batch skip 或全局 plan cache。
3. 完整 `loop_path` 从 immutable plan 内存解析，不产生 Repo 调用。三层 entry 的 previous result
   仍只定位一个 execution ID；第二次相同 ID 读取来自既有静态 upstream artifact probe，深度从
   1 增至 3 不会形成逐祖先读取。
4. 历史接受沿用每个有效 attempt 一条 checkpoint 的原子写、消息幂等 append 和确认写。
   `terminal_history_repair_is_batched_durable_and_retries_failed_confirmation` 验证 32 个终态 Run
   只做一次主键批量 marker 查询；16 个已确认时读取 16 行，重启后读取 32 行；不读取 Run、
   Node 或 producer payload。Pending checkpoint 页上限仍为 100，扫描间隔配置仍为 5 秒。
5. messages-only 继续由 Session `created_at` 与 cutoff 选路；现有隔离测试用会失败的 Run/Node/
   definition repo 证明该路径只读消息表。嵌套路径随冻结 checkpoint/message payload 传递，
   4 MiB 只约束 plan，不限制这些历史正文的总字节数。

## 失败与恢复范围

- 持续 dispatch/skip/snapshot 写失败由 recovery page 返回逐 Run failure，重复扫描不伪造成功；
  `a_corrupt_run_does_not_starve_the_next_cursor_page` 验证失败 Run 不阻塞后一页。
- 部分 inner skip、outer continue/break、Judge claim/commit、通知提交、历史 append/confirm、
  cancel 与 lease 竞争均由现有 recovery/SQLite restart 测试覆盖；扫描页仍为 32 个 Run。
- 这些故障测试验证有界、收敛和幂等语义，但 RecordingDb 不能给出真实 MySQL 的网络往返、
  锁等待或连接池占用时间。真实 MySQL 成本仍须在可销毁测试库上补测。

## 命令

```text
cargo test --offline -p bcs-collaboration-runtime --test fixed_loop_compiler \
  nested_capacity_boundaries_cover_near_maximum_nodes_and_plan_bytes -- --nocapture
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression \
  thirty_two_nested_runs_coexist_with_complete_materialization -- --nocapture
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression \
  near_capacity_outer_break_scans_and_skips_the_materialized_future_graph -- --nocapture
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression \
  three_level_human_context_reads_only_the_nearest_previous_result
cargo test --offline -p bcs-collaboration-store --test mysql_store batched_nodes -- --nocapture
```

## 未完成边界

- 2026-10-13 已在一次性 MySQL 8.4.11 上运行 DB plugin 及固定 Loop 持久化契约，
  Text/Prepared 双协议的 round-trip、rerun、写失败回滚和 2048 节点批量写均通过。
  这些是功能回归，不是锁持有时间、连接池竞争或 32 个满容量 Run（65,536 行）恢复页实测。
- 32 个并存 Run 使用 17 节点代表故事；2047 节点测试验证单 Run 编译/materialize/skip，
  没有把两者相乘后冒充可接受的生产吞吐。
- 仍需真实 MySQL 容量测量与通过统一 singlebox 门禁，才能将 T09 整体标记完成；
  2026-10-13 已生成完整 artifacts，但 Singlebox 未全绿，详情见 `validation.md`。
