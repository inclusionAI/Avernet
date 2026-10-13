# avernet-goal-cli

面向用户和第三方 Agent 的 Avernet Goal 提交工具。认证由 Ant CLI SDK 统一处理；CLI 只调用 Gateway 相对路径，不接受 Backend URL、Token 或运行时策略参数。

## 单个 Goal

```bash
# Human 发起，目标由 Backend 默认解析
avernet-goal-cli "分析支付失败率"

# 一个 Goal 发给多个 Bot：每个目标独立创建 Intake Session 和 Task
avernet-goal-cli "分析支付失败率" \
  --to bot:risk-bot-a \
  --to bot:risk-bot-b

# Human 授权名下 Bot 作为 A2A 权限代理
avernet-goal-cli "分析支付失败率" \
  --from bot:my-assistant \
  --to bot:risk-bot:target-owner

# 中心化执行模式
avernet-goal-cli "分析支付失败率" --mode centralized
```

`--from bot:<ref>` 只负责 A2A 转发和权限证明，不改变任务 owner、澄清 Bot 或执行者。首版 `--to` 只支持 `bot:<ref>`；`group:` 会在联网前以 `UNSUPPORTED_TARGET_TYPE` 拒绝。

## 恢复与查询

```bash
avernet-goal-cli resume <session-id>
avernet-goal-cli get <task-id>
```

`resume` 重新进入与首次提交相同的澄清和确认控制器。没有独立的 `reply`、`confirm` 或 `cancel` 子命令。

## Batch

Agent 应先读取帮助、Schema 和示例，再构造 Batch 文件：

```bash
avernet-goal-cli batch --help
avernet-goal-cli batch schema --output json
avernet-goal-cli batch example --format yaml
avernet-goal-cli batch validate --file goals.yaml --output json
avernet-goal-cli batch submit --file goals.yaml --output json
```

Batch YAML：

```yaml
schema_version: "1"
defaults:
  from: human
  mode: relay
goals:
  - id: goal1
    goal: 分析支付失败率
    to:
      - bot:risk-bot
  - id: goal2
    goal: 分析退款失败率
    from: bot:my-assistant
    to:
      - bot:risk-bot-a:owner-a
      - bot:risk-bot-b:owner-b
```

每个 `Goal × target` 都是独立执行单元：独立 Session、独立澄清、独立确认、独立 Task。整个文件会在产生任何网络副作用前完成校验。

## Agent/自动化模式

```bash
avernet-goal-cli "分析支付失败率" --non-interactive --output json
avernet-goal-cli resume <session-id> --non-interactive --output json
```

`--non-interactive` 不等待输入，也不自动确认。需要补充或确认时返回当前 Session 状态并以退出码 `3` 结束；Agent 之后使用 `resume <session-id>` 继续。

JSON 模式保证 stdout 只写一个 JSON 对象，诊断信息写 stderr。

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部成功 |
| `2` | CLI 或 Batch 输入非法 |
| `3` | 需要继续澄清或显式确认 |
| `4` | Batch/多目标部分成功 |
| `5` | 未认证或无权限 |
| `6` | Gateway/Backend 暂时不可用 |
| `7` | 所有执行失败 |

## 开发验证

```bash
cargo fmt --all -- --check
cargo test
cargo clippy --all-targets -- -D warnings
```
