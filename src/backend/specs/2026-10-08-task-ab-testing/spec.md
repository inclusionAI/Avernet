# Task A/B Testing

## Problem

任务模块已有可冻结的 `TaskRuntimeProfile` 和中心化/Relay 双编排模式，但缺少任务创建时的稳定实验分流。若在执行阶段临时选择策略，同一任务的重试、恢复或接力可能进入不同实验组，结果不可归因。

本能力支持三个相互独立的实验维度：

1. 搜推接口：按任务选择已注册的候选发现实现。
2. 编排模式：在中心化规划和 Relay 接力之间分流。
3. 任务能力：选择中心化规划策略、派发策略及其他受治理运行时策略。

## Contract

调用方可在 `execution_config.ab_test` 声明一个任务级实验：

```json
{
  "task_type": "dynamic",
  "ab_test": {
    "experiment_id": "fine-grained-task-v2",
    "unit": "owner_user",
    "variants": [
      {
        "name": "control",
        "weight": 50,
        "orchestration_mode": "centralized",
        "runtime_profile": {
          "search_strategy": "catalog-v1",
          "planner_strategy": "workflow",
          "dispatcher_strategy": "search"
        }
      },
      {
        "name": "treatment",
        "weight": 50,
        "orchestration_mode": "relay",
        "runtime_profile": {
          "search_strategy": "catalog-v2"
        }
      }
    ]
  }
}
```

- `experiment_id` 必须为非空字符串。
- `unit` 可选 `task`、`owner_user`、`owner_bot`，默认 `task`。
- `variants` 至少包含两个名称唯一的变体。
- `weight` 是正整数相对权重，不要求总和为 100。
- 变体只允许覆盖 `runtime_profile` 的受治理字段和 `orchestration_mode`；禁止任意合并整个 `execution_config`。
- `orchestration_mode=relay` 只允许用于动态任务。

### Fine-grained capability slots

| 字段 | 作用范围 | 语义 |
| --- | --- | --- |
| `orchestration_mode` | 任务入口 | `centralized` 与 `relay` 模式实验 |
| `runtime_profile.search_strategy` | 中心化 + Relay | 选择 composition root 注册的搜推/候选发现接口 |
| `runtime_profile.planner_strategy` | 中心化 | 选择 `TaskPlanner` 的具名规划策略 |
| `runtime_profile.dispatcher_strategy` | 中心化 | 选择 `TaskDispatcher` 的具名派发策略 |
| `runtime_profile.runner_strategy` | 冻结配置 | 为运行能力实验保留的受治理槽位；当前执行器尚未按该字段切换实现 |
| `runtime_profile.allowed_run_modes` | 中心化执行 | 限制允许的执行模态 |

Relay 的规划和派发决定由 Relay Skill/event protocol 上报，不会错误复用中心化 `TaskPlanner` 或 `TaskDispatcher`。Relay 与中心化共享 `search_strategy`，因此两条链路在同一任务内使用同一个被冻结的搜推接口。

搜推实现由 composition root 以稳定名称注册。现有单一 `discover` 端口自动注册为 `default`；可通过 `TaskService(search_strategies={...})` 增加实验实现。任务配置只保存名称，不保存 transport 或 provider 对象。若变体选择了未注册名称，执行会以 `TaskStateError` 明确失败，不静默回退到默认接口。

## Assignment and freezing

TaskService 在任务持久化和建图之前完成一次分流：

1. 取 `experiment_id + NUL + unit value` 的 SHA-256。
2. 用摘要前 64 bit 对总权重取模。
3. 按声明顺序落入累计权重区间。
4. 将变体覆盖合并到任务的执行配置。
5. 写入 `execution_config.ab_assignment`，随后与任务和 Graph 一起持久化。

`ab_assignment` 包含：

```json
{
  "experiment_id": "fine-grained-task-v2",
  "variant": "treatment",
  "unit": "owner_user",
  "bucket": 93,
  "total_weight": 100,
  "assignment_key_digest": "<sha256>"
}
```

不记录原始分桶值。相同实验和相同分桶单元稳定命中同一变体；任务重试、Harness 恢复、中心化推进及 Relay 接力均复用已冻结配置，不重新分桶。

## Compatibility and failures

- 未配置 `ab_test` 或 `enabled=false` 时保持原行为。
- 变体覆盖优先于任务模块运行时默认值和调用方基础 profile，以确保实验选择真实生效。
- 非法字段、权重、策略 profile、运行模态或编排模式在任务落库前以 `TaskStateError` 明确失败，不静默回退。
- HTTP `execution_config` 透传实验对象；核心分配器在统一任务入口执行严格校验，因此 HTTP 与非 HTTP 调用方遵循同一契约。
