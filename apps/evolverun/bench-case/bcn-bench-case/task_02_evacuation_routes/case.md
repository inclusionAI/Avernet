---
id: task_02_evacuation_routes
name: HiddenBench 疏散路线信息汇合（BCN 适配）
category: collaboration
grading_type: automated
timeout_seconds: 600
workspace_files: []
---

## Prompt

暴雨暂歇，四位社区负责人需要讨论并决定村民撤离目的地。可选地点为 West City、East Town、North Hill；场景与可用事实见各自工作区的 KNOWLEDGE.md。依据这些资料作出决定，不使用外部搜索，也不编造道路状况。

请协调员通过当前群的 BCN 协作工具组织所有成员讨论，收集并如实转达事实、疑问和判断，给各人依据讨论内容作出最终选择的机会。不要求全员一致，不代替任何成员发言。

讨论完成后，协调员请其余三位成员各自返回一份最终 JSON；每个成员的回复须来自其真实 BCN 委托链路。协调员收齐后，用本群正式完成工具提交自己的最终 JSON，不把其他人的选择表当作自己的交付。

每个人的最终 JSON 使用同一种格式（不加额外说明或 Markdown 围栏）：

```json
{"phase":"final","choice":"填写 West City、East Town 或 North Hill 中的一项","rationale":"简述自己选择的事实依据"}
```

只读取自己的工作区资料；通过交流取得其他成员的信息，不读取其他工作区、Case 文件、参考答案或评分材料。使用本群成员身份，不创建新 Agent 或新群。遇到失败如实说明，不编造回复或完成状态。

## Expected Behavior

配套团队为四人：manager 同时是负责人 A，另三人为负责人 B/C/D；每个人持有相同背景及四条公共事实，外加一条不同的事实。成员与角色由 Profile 决定，不增加 Case 专属元数据。

A 知道补给车困在 East Town 的隧道中；B 知道大火阻断补给车和其他交通；C 知道 North Hill 的步行道路已关闭；D 知道泥石流覆盖其车行道路。完整事实支持排除 East Town 与 North Hill，选择 West City。事实需要实际交流和使用，不能把成功派发等同于成员已参与。

协调员允许成员持不同意见，回收各人的真实最终回复，并用 bcs_task_complete 正式提交自己的 JSON。只要求有意义的信息交流，不要求固定发言顺序或固定讨论轮次。

## Grading Criteria

- [ ] 四位成员的最终选择分别来自本人的真实交付，不接受协调员转述或伪造的投票表。
- [ ] 最终 choice 为正确选项 West City，且符合约定的 JSON 格式。
- [ ] 根群任务正式完成，证据完整且已冻结；只出现答案关键词不构成通过。

## Automated Checks

主得分只使用讨论后成员平均正确率：四人中真实、格式有效且选择正确的成员数 / 4。返回单个检查项 `post_average_accuracy`，因此 Runner 的检查项平均值就是该得分。它不混入协作次数、Token、耗时或理由的主观质量。

| 证据 | 检查 |
| --- | --- |
| bcn-evidence.json | 当前成员映射、完整群消息、已完成根 Session 及真实完成工具结果 |
| 三位 Worker 的 assistant 消息 | 只接受被协调员派发的 task_id 对应的本人回复；按时间取正式完成前的最后一条回复 |
| 根 Session output | 作为协调员本人的最终选择，必须对应成功的正式完成工具提交 |

没有回复、错误选项、无效格式或只由经理代述的成员计 0 分；明确属于证据缺失/损坏的情况抛错，保留 score=null。超时沿 Runner 约定不评分，仍交接分析报告。

```python
import json
import math
from pathlib import Path


def grade(transcript: list, workspace_path: str) -> dict:
    evidence = json.loads((Path(workspace_path) / "bcn-evidence.json").read_text(encoding="utf-8"))
    run, result, session = evidence["run"], evidence["result"], evidence["session"]
    if not all(result.get(key) is True for key in
               ("group_messages_complete", "evidence_complete", "evidence_stable")):
        raise ValueError("Incomplete or unstable evidence")
    members, names = run["members"], run["member_names"]
    if (set(members) != {"manager", "member_b", "member_c", "member_d"}
            or set(names) != set(members) or run["entry_member"] != "manager"
            or len(set(members.values())) != 4 or len(set(names.values())) != 4):
        raise ValueError("Unexpected team identity mapping")
    if session.get("status") != "completed" or session.get("error_message"):
        raise ValueError("No successful root completion")
    messages = evidence["messages"]
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        raise ValueError("Invalid group messages")

    def time_of(message):
        value = message["timestamp"]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Expected BCS epoch-millisecond timestamp")
        return value

    def identities(alias):
        return {members[alias], names[alias]}

    def tool_payload(message, tool):
        meta = message.get("metadata") or {}
        if (message.get("sender") not in identities("manager")
                or message.get("role") != "tool_result"
                or meta.get("tool_name") != tool or meta.get("is_error") is not False):
            return None
        payload = meta["result"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Malformed tool evidence")
        return payload if payload.get("ok") is True else None

    completions = []
    for message in messages:
        if tool_payload(message, "bcs_task_complete") is not None:
            summary = message["metadata"].get("arguments", {}).get("summary")
            if isinstance(summary, str) and summary.strip() == str(session.get("output", "")).strip():
                completions.append(message)
    if not completions:
        raise ValueError("Root output has no matching successful completion evidence")
    cutoff = max(time_of(message) for message in completions)

    def correct_vote(content):
        if not isinstance(content, str):
            return False
        try:
            vote = json.loads(content)
        except json.JSONDecodeError:
            return False
        return (isinstance(vote, dict) and set(vote) == {"phase", "choice", "rationale"}
                and vote["phase"] == "final" and vote["choice"] == "West City"
                and isinstance(vote["rationale"], str) and bool(vote["rationale"].strip()))

    correct = int(correct_vote(session.get("output")))
    for alias in members:
        if alias == "manager":
            continue
        tasks = {}
        for message in messages:
            payload = tool_payload(message, "bcs_assign_task")
            if payload is None or time_of(message) > cutoff:
                continue
            target = message["metadata"].get("arguments", {}).get("target_bot")
            if target in identities(alias):
                task_id = payload.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    raise ValueError("Dispatch has no task id")
                tasks[task_id] = time_of(message)
        replies = [m for m in messages if m.get("sender") in identities(alias)
                   and m.get("role") == "assistant" and m.get("run_id") in tasks
                   and tasks[m["run_id"]] <= time_of(m) <= cutoff]
        if replies:
            latest = max(replies, key=time_of)
            correct += int(correct_vote(latest.get("content")))
    return {"post_average_accuracy": correct / 4}
```

## Additional Notes

来源：HiddenBench 原始 id=1 `evacuation_west_city`，固定源版本、原文和 MIT 许可见本目录 README.md 与 source/。Profile 保留原始事实内容；去掉原实验报酬及不确定结束时间描述，使用当前 Runner 的 600 秒上限和正式收卷机制。

本例为当前 Manager-worker、一次投题模式的 BCN 适配版，不复现原版讨论前投票、15 轮讨论及完整实验协议。主分对应讨论后成员平均正确率；多数正确条件为至少 3/4 人正确，可在分析报告中由成员选择推导，不额外混进主得分。协调员作为四人之一，不增加一个看到全部资料的第五人。

分析报告请列出各成员最后一条可关联回复、最终选择及依据；说明哪些信息被共享、是否仍有遗漏或错误共识。理由真实性、跨工作区绕读和过早结束属于需审阅轨迹的诊断项，不声称已经由这个答案 Judge 自动证明。相同系统用户下的工作区分隔不是强文件权限隔离。

仅 Prompt 投递给被测群，评分材料和 source/ 原始记录不复制到成员工作区。报告补全使用现有 evaluation-request.json 与 bench.py finalize，不启动额外报告服务。
