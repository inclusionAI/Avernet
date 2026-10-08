# Task TopN 多采样执行

## Summary

中心化动态任务的搜推派发支持配置 `task_dispatch.sample_count=N`。当 `N>1` 时，系统按搜推排序选择最多 N 个不同 Bot 独立执行同一逻辑节点，随后由任务 owner Bot 根据节点目标、验收标准和候选终态选择最佳结果。Graph 只接收一次规范回投，仍由 `TaskGraphService` 推进节点生命周期。

## Configuration

```yaml
user_config:
  task_dispatch:
    sample_count: 1
```

`sample_count` 必须为 `1..5` 的整数。缺失或非法配置回退为 `1`。默认值 `1` 保持既有单次派发行为。

## Behavioral contract

1. `/search` 和 Bot discovery 仍是候选排序事实来源；本功能不修改搜索算法。
2. 当 `sample_count>1` 时，Dispatcher 取排序后的不同可执行 Bot identity，最多 N 个，并将稳定采样计划持久化到 `RuntimeInfo.extend_props.dispatch_samples`。
3. TopN 表示独立采样，不表示协作关系；不创建 BCS 协作群，也不新增公开 `run_mode`。节点仍使用 `single_bot`，第一名仅作为兼容主 assignee。
4. Runner 为每个样本构造相同任务上下文并并发执行，设置 `skill_report_enabled=false`，禁止样本直接改变逻辑节点状态。
5. 每个样本必须产生合法终态 `{"success":bool,"data":Any,"gaps":list[str]}`。传输失败、非 COMPLETED、非法 JSON 或非法验收结构只使该样本失效，不取消其它样本。
6. 至少一个样本产生合法终态后，Runner 调用任务 owner Bot 作为 judge。judge 只能从提供的 `sample_id` 中选择一个结果。
7. judge 失败时采用确定性回退：先取排名最高的验收通过终态；若无通过终态，则取排名最高的合法失败终态。
8. Runner 通过既有 result sink 发出一次规范 callback。被选结果的 `success`、`data`、`gaps` 保持不变；采样数量、各样本状态、选中 Bot 和 judge 模式仅放入 `_ext_info.multi_sample` 供审计。
9. 全部样本失效时回投 `exec_error=multi_sample_all_failed`，由既有 Harness 重试和熔断策略处理。
10. Harness 重试复用已持久化采样计划，不重新选择候选。

## Architecture boundaries

- `TaskDispatcher` 只选择并记录候选，不执行 Bot 调用。
- `TaskExecutor` / `MultiSampleExecutor` 负责投递、等待和 judge 调用，不直接写 Graph。
- `TaskGraphService.report(...)` 仍是节点事实和状态迁移的唯一入口。
- 不新增 transport 依赖、外部 URL、公开 callback 协议或 Graph 状态。

## Acceptance criteria

- [x] `sample_count=1` 保持原行为。
- [x] `sample_count>1` 按搜推顺序选取最多 N 个不同 Bot。
- [x] 每个候选独立执行，任一候选失败不取消其它候选。
- [x] owner Bot judge 只选择已完成的合法候选。
- [x] judge 异常有确定性排名回退。
- [x] Graph 仅收到一次规范结果回投。
- [x] 被选失败终态的 gaps 被保留并进入既有重规划流程。
- [x] 全部样本失效进入既有 Harness 流程。
