# 嵌套固定 Loop 容量与数据库成本记录

- 测量日期：2026-09-23；2026-10-14 补充 PR #2678 评审修复与数据库审查。
- 代码基线：`83d906bf2`（相对 `1b9bfed2b`）及当前批量 skip / 诊断路径修复。
- 构建模式：本机 Rust `test`（debug），Memory/SQLite/RecordingDb、临时 MySQL 8.4.11；结果用于回归边界，
  不作为生产吞吐承诺。
- 默认限制：深度 4、每层最多 32 轮、2048 个编译叶子、plan 4 MiB。

## 可重复测量结果

| 场景 | 实际结果 | 回归断言 |
| --- | --- | --- |
| spec 两层示例 | 17 个 materialized Node Run；指定轨迹 15 Completed、2 Skipped | runtime 主故事与 seed compiler 测试 |
| 接近节点上限 | 外层 31 轮、内层 32 轮，编译 2047 个叶子；plan 2,272,486 bytes；debug 编译约 189 ms | 叶子数必须为 2047；节点上限保持 2048 |
| 接近 plan 上限 | 二分构造 17 叶子嵌套 plan，4,194,303 bytes；debug 编译约 190 ms；再增加 1 byte 输入即拒绝 | 上限内成功，上限外失败；默认仍为 4 MiB |
| 32 个并存 Run | Memory 中 32 个 Running Run、544 个 Node Run、32 个首节点 dispatch；顺序创建约 352 ms | Run ID 唯一且每个 Run 完整 materialize 17 个节点 |
| 大范围 early break（修复后） | 2047 叶子中最终 5 Completed、2042 Skipped；外层 break 跳过 1980 个节点；Memory debug 约 989 ms、整条故事约 5.26 s | 单节点 skip 为 0；10 次 scalar status 批读 + 10 次原子批写；包含最终发布/查询的计数窗口 `get_run=12`、`list_node_runs=8`、`get_node_run=105` |
| MySQL 批量 skip | 1980 个节点；最终复验 Text v1/v2 为 117/160 ms，Prepared v1/v2 为 143/160 ms（单次本机样本） | 10 SELECT + 10 UPDATE；每个 UPDATE 最多 200 行，独立短事务；不是整个 completion 请求的耗时 |
| 三层 previous result | 查询内层第二轮 Pending 时，2 次 `get_node_run` 都指向最近内层上一轮结果 | 1 次 LoopContext 结果读取 + 1 次既有静态 upstream probe；不逐祖先读取 |
| 2048 节点 Session 创建/rerun | RecordingDb 均为 43 个节点 INSERT；每条最多 48 行/818 bind 参数 | 创建事务为 2+43 steps，rerun 为 4+43 steps；SQLite 原子回滚与 rerun 同样通过 |

上述毫秒数来自一次本机 debug 运行，测试只断言结构、次数和边界，不断言机器相关耗时。

## Repo、SQL、行数与事务边界

1. `create_run_if_session_idle` 的 2048 个 Node Run 使用 `ceil(2048/48)=43` 条节点
   INSERT，加 1 行 Run 与 Session guard；全部位于一个事务。rerun 仍在一个事务内，增加源 Run、
   snapshot 与 Session 相关的固定步骤，不逐节点开事务。
2. 修复前外层 break 对 1980 个节点各做一次完整行 SELECT 和 guarded UPDATE，即约 3960 条
   串行 SQL；此成本不应当作为允许上限的验收标准。修复后 10 次只读 ID/status 的 SELECT 和
   10 个 UPDATE 事务（另计 BEGIN/COMMIT，约 40 条命令；Prepared 的准备开销另计）。
   行写入数仍为 1980，不是 O(1)。每批最多 200 个不同节点，任一节点不再 unstarted 则整批回滚；
   前台最多刷新重算 3 次，持续争用返回错误，由已有恢复退避处理。Skipped 节点仍可穿透，
   Running/终态/缺失节点阻断前台分支遍历，共享 join 仍受保护。单节点批次直接 guarded UPDATE，
   不新增事务往返。后台恢复同样批写，不再为每个 skip 单独读 Run、写 Node。
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
- 新增 Memory/SQLite/真实 MySQL 公共契约覆盖整批回滚、Running/终态/缺失节点、Run 取消、
  环境隔离、重复/超限 ID、超时字段清除与写失败传播。运行时覆盖读后 dispatch 竞争、取消、
  三次竞争上限及 2047 节点完成首批 200 个 skip 后中断并恢复（余下 1780 个节点 9 批）。
- MySQL 单 Run 批写已实测；仍未测生产网络延迟、锁等待分位数或多 Run 共享池争用。

## PR 整体数据库压力审查（2026-10-14）

结论：两条评审意见均已修复，但不能据此承诺开启嵌套 Loop 后数据库压力不涨。
原 PR 没有生产 Store SQL、DDL、索引或扫描调度修改；此次修复新增 bounded batch repo 操作。
默认节点/plan 上限仍为 2048 / 4 MiB，实际嵌套新 Run 的 admission 开关默认关闭。
风险主要来自业务开始使用更大的 materialized Run，而不是递归层级本身新增查询。

| 路径 | 实际 DB 成本 / 边界 | 风险判断 |
| --- | --- | --- |
| 编译/校验、loop_path、previous result | 编译纯内存；path 来自 plan，previous result 仍只读最近一层结果 | 无逐祖先 N+1；修复诊断路径不改变 plan/哈希/数据库 |
| 新建 / rerun | 一次性 Session 新建与 rerun：43 个 48 行以内的 INSERT，同一个创建事务；普通 Service 路径 `create_run` 仍为一个最多 2048 行、34816 bind 的 INSERT，外加 Run；snapshot 保存完整 JSON，rerun 复制 | 小 authoring 可展开到上限；批 INSERT 不等于小事务，写行数/redo/存储随展开节点数增长 |
| Bot/Human completion | `complete_run_if_done` 读完整节点；未结束时内部 `run_view` 再读一次，外层 progression 的 `run_view` 再读一次；view 每次读完整 plan，无 plan cache | 常见未结束路径至少 3N 行读取与重复 plan 传输；执行 N 个节点时累计读行可能为 O(N²)，这是既有路径被大 Run 放大 |
| Join / prompt / Judge | `all_upstreams_completed`、`judge_upstream_outputs` 仍逐个前驱读完整 Node 行 | 批 skip 后仍有扇入相关 N+1；2047 样本剩余 105 次单节点读取（含发布/查询），不能说 completion 已降到 20 条 SQL |
| 活跃 Run 恢复 | leader 默认 1 秒 tick、每页 32 Run、页内串行；等待中的普通 Run 仍有 3 次完整 Node 列表读取和一次完整 snapshot 读取 | 32×2048×3 = 196608 行/页，32×4 MiB = 128 MiB plan/页的理论边界；不是实测吞吐，也不是必然每秒达到，长扫描不叠加 |
| 已完成节点历史修复 | 每次活跃 Run sweep 遍历 Completed 节点；读取历史 checkpoint、幂等消息查询/append、确认；Human 还可能有 prompt checkpoint | 即使大量节点已完成、仅最后 HumanInput 长期等待，恢复也非 O(1)；终态 history-repair marker 不会替活跃 Run 消除这些操作 |
| Event / Human prompt / 历史 | 沿用既有行/事务，增加 loop_path/execution JSON 字节；messages-only 查询仍不读 Run/plan | 不新增逐层 SQL，但 JSON、网络和持久化体积增加；4 MiB plan 上限不约束所有输出、历史正文总量 |

`MySqlCollaborationStore` 这些路径无 Repo 读缓存。DB plugin 每次普通 query/execute 借还连接；
并不是旧版一次 break 独占一个连接执行全部 3960 条 SQL。新批写每个事务借一个连接，最多
200 个节点且不跨网络交付；并发大 Run、其他恢复队列和前台请求仍共用连接池。
现有 `(env, run_id, node_id)` unique key 与 Run 索引可支持定位；本次不新增索引或放宽门槛。

### 上线建议与尚需验证

- 可以在嵌套 admission 关闭的状态部署修复；存量 DAG/单层 Loop 需要以下完整受影响模块回归。
- 开启嵌套前按预计展开节点数、输出大小和活跃 Run 数做容量预算；小流量放量，关注数据库
  QPS、行读取/发送字节、连接等待、慢 SQL、锁等待、恢复页耗时和 API P95/P99。
- 不建议仅凭当前功能测试就全量开放 2048 节点并发。补测 32 个满容量 Run 的完整恢复页，
  特别是大量 Completed + 长期等待 HumanInput、取消/竞争和多实例 leader 切换场景。
- 如果这些路径超出预算，后续针对性减少重复完整 plan/Node 读取、批读 join 前驱和活跃 Run
  的已确认历史修复探测；不能以无效全局缓存或跳过 CAS/持久化确认来换吞吐。
- 关闭 nested admission 只能阻止新建/rerun，已接纳的嵌套 Run 仍继续推进和恢复，不是立即卸载开关。

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
cargo test --offline -p bcs-collaboration-store --test mysql_store branch_skip -- --nocapture
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression branch_skip_tests
```

## 未完成边界

- 2026-10-13 已在一次性 MySQL 8.4.11 上运行 DB plugin 及固定 Loop 持久化契约，
  Text/Prepared 双协议的 round-trip、rerun、写失败回滚和 2048 节点批量写均通过。
  这些是功能回归，不是锁持有时间、连接池竞争或 32 个满容量 Run（65,536 行）恢复页实测。
- 2026-10-14 增加批 skip 原子性与单 Run 1980 行成本实测，未改变上述多 Run 整体容量限制。
- 32 个并存 Run 使用 17 节点代表故事；2047 节点测试验证单 Run 编译/materialize/skip，
  没有把两者相乘后冒充可接受的生产吞吐。
- 仍需真实 MySQL 容量测量与通过统一 singlebox 门禁，才能将 T09 整体标记完成；
  2026-10-13 已生成完整 artifacts，但 Singlebox 未全绿，详情见 `validation.md`。
