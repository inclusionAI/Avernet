---
id: task_00_connectivity
name: BCN 群协作连通性检查
category: bcn-connectivity
grading_type: automated
timeout_seconds: 600
---

## Prompt

这是一次群协作连通性检查，无需分析业务问题、写代码或调用外部业务工具。

请主协调 Agent 向本次群内每一个 Worker 各派发一次简短任务：“收到后请只回复 BCN_ACK”。使用当前群提供的成员身份和 BCN 协作工具，不创建其他子 Agent。

收到所有 Worker 的真实回复后，主协调 Agent 汇总收到的成员名单，并通过本群正式完成工具提交结果，结果中包含 BCN_CONNECTIVITY_OK。

若有成员尚未回复，继续等待；如确实失败则如实说明，不代替该成员回复，不提前声明全部连通。

## Expected Behavior

本 Bench 配套 `profiles/` 中的两个成员：manager 负责协调，worker 收到委托后回复 BCN_ACK。Profile 与 Case 一起维护和版本化。

预期发生一次 Manager → Worker 的真实派发和一次 Worker → Manager 的回复，Manager 随后正式完成任务。检查真实身份与当前 Session，不依赖固定 Bot ID 或展示名称。

## Grading Criteria

- [ ] Manager 向每个 Worker 真实派发任务，并收到该成员的 BCN_ACK。
- [ ] Manager 在回复之后正式完成根任务，交付包含 BCN_CONNECTIVITY_OK。

## Automated Checks

代码 Judge 使用 Runner 从当前 Session 回收的群消息、成员映射和正式终态。

| 检查项 | 通过条件 |
| --- | --- |
| 协作往返 | 每一个实际 Worker 均有本次 Session 中可关联的 BCN 派发与回复，回复包含 BCN_ACK。依据真实发送者及任务关联核对，不接受 Manager 的转述代替成员回复。 |
| 正式收卷 | 根 Session 已 completed、无 error_message，正式 output 包含 BCN_CONNECTIVITY_OK，并有对应 Manager 完成证据。 |

两项全部通过为 1 分，否则为 0 分。依赖的证据缺失或评分器异常为不可评分，score=null。执行超时沿 Runner 约定记 timeout、score=null，报告列出已回复和未回复成员。

不调用 LLM Judge，不检查业务方案质量，也不将 Token、速度作为通过门槛。用量和耗时只记录为观测数据。

```python
import json


def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    evidence = json.loads((Path(workspace_path) / "bcn-evidence.json").read_text(encoding="utf-8"))
    run, result = evidence["run"], evidence["result"]
    if not result.get("group_messages_complete"):
        raise ValueError("Group evidence is incomplete")
    members, names = run["members"], run["member_names"]
    manager = run["entry_member"]
    identities = lambda alias: {members[alias], names[alias]}
    messages = evidence["messages"]
    session = evidence["session"]
    completed = (session.get("status") == "completed" and not session.get("error_message")
                 and "BCN_CONNECTIVITY_OK" in str(session.get("output", "")))
    def successful_tool(message, tool):
        meta = message.get("metadata") or {}
        return (message.get("sender") in identities(manager)
                and message.get("role") == "tool_result"
                and meta.get("tool_name") == tool and meta.get("is_error") is False)
    complete_calls = [m for m in messages if successful_tool(m, "bcs_task_complete")]
    valid = completed and bool(complete_calls)
    for worker in members:
        if worker == manager:
            continue
        dispatches = []
        for message in messages:
            if not successful_tool(message, "bcs_assign_task"):
                continue
            meta = message["metadata"]
            if meta.get("arguments", {}).get("target_bot") not in identities(worker):
                continue
            payload = meta.get("result", {})
            payload = json.loads(payload) if isinstance(payload, str) else payload
            if payload.get("ok") is True and payload.get("task_id"):
                dispatches.append(message)
        replies = [m for m in messages if m.get("sender") in identities(worker)
                   and m.get("role") == "assistant" and str(m.get("content", "")).strip() == "BCN_ACK"]
        valid = valid and any(d.get("timestamp", 0) <= r.get("timestamp", 0) <= c.get("timestamp", 0)
                              for d in dispatches for r in replies for c in complete_calls
                              if r.get("run_id") == (json.loads(d["metadata"]["result"]) if isinstance(d["metadata"]["result"], str) else d["metadata"]["result"]).get("task_id"))
    return {"connectivity": float(bool(valid))}
```

## Additional Notes

验证 Agent 启动/注册、建群、目标投递、Manager-worker 往返和正式完成。600 秒只是最长等待时间，完成后立即收卷。

参考标准只保留在评测端；仅 Prompt 投递给被测群。运行时根据实际身份映射检查成员，不依赖显示名称中的临时后缀。

