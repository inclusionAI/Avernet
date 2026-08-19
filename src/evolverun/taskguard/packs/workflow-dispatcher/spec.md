# 工作流调度 — Spec

## 目标

将 workflow-dispatcher SKILL 的4步流程（数据预处理（含权限检查）→ 意图识别 → 任务生成（含风险域判断）→ 任务分发 → 边界回复）转化为 ClawMind workflow YAML 配置，每个节点执行一个对应的 skill。

## 输入参数

| 参数名 | 必填 | 说明 |
|--------|------|------|
| raw_message | 是 | 原始消息，包含 text 和 attachments |
| metadata | 是 | 元数据，包含 sender_id、sender_name、channel、group_name、group_id 等 |

### raw_message 结构

```json
{
  "text": "为什么2088xxx不能收款",
  "attachments": [{"type": "image/png", "local_path": "/xxx.png"}]
}
```

### metadata 结构

```json
{
  "sender_id": "487153",
  "sender_name": "嘉耀",
  "channel": "钉钉单聊|钉钉群聊|感知工单",
  "group_name": "群名称",
  "group_id": "cidxxx"
}
```

## 身份与去重

- key: `{{input.digest}}`
- label: `工作流调度`
- duplicatePolicy: allow

## 输出产物

| 输出名 | 来源 | 说明 |
|--------|------|------|
| task_result | `{{nodeOutput.task-dispatch.result}}` | 任务分发结果 |

## 流程设计

### 阶段划分

- **P1: 输入处理** — 数据预处理（含权限检查）、意图识别（串行，任一步触发边界则终止）
- **P2: 任务生成** — 风险域判断 + 子任务列表生成（generate_task_list.py 内部处理高敏判断）
- **P3: 任务分发** — Bot 任务分发、Duty 任务分发、状态更新

### 边界处理策略

workflow YAML 没有原生的"条件跳转到指定节点"机制，因此采用以下策略：

1. **边界面内处理**：每个可能触发边界的节点，在 prompt 中包含完整的边界处理指令 —— 若检测到边界条件，该节点内部调用 boundary-reply-user skill 完成保存+回复，并在输出中设置 `boundary_triggered: true`
2. **后继节点跳过**：后续节点的 prompt 包含检查上游 `boundary_triggered` 字段的指令 —— 若为 true，则跳过执行并透传该标记
3. **无独立边界节点**：boundary-reply-user skill 由触发边界的节点内部调用，不作为独立 workflow 节点

### 节点列表

| ID | 标题 | 阶段 | 依赖 | 执行器 | Skill | 说明 |
|----|------|------|------|--------|-------|------|
| data-preprocessing | 数据预处理 | P1 | - | embedded-agent | data-preprocessing | 元数据提取、权限检查(check_permission.py)、图片上传、历史对话、COT判断、实体提取。边界：permission_denied / domain_other / blocked_account |
| intent-recognition | 意图识别 | P1 | data-preprocessing | embedded-agent | intent-recognization | 识别用户意图（解除账户限制/风险排查/其它）。边界：other_intent |
| task-generation | 任务生成 | P2 | intent-recognition | embedded-agent | task-generator | 风险域判断(userid-punish-search) + 子任务列表生成(generate_task_list.py内部处理高敏判断)。边界：no_entity / empty_user_id / empty_domain / unspecified_domain / unsupported_domain / empty_tasks |
| task-dispatch | 任务分发 | P3 | task-generation | embedded-agent | task-dispatcher | Bot任务BCS分发(bcs_task_dispatcher.py) + Duty任务hitl_request通知(duty_task_notifier.py) + 子任务状态更新(update_subtask_status.py) |
| done | 流程完成 | P3 | task-dispatch | done | - | 标记流程完成 |

### 数据流

```
input.params (raw_message + metadata)
  ↓
data-preprocessing
  → query, source, source_detail, task_source, entity_info, url_list, combined_text, visual_analysis, allowed, permission_reason, receive_time
  → boundary_triggered (bool)
  ↓
intent-recognition
  ← combined_text, source (from data-preprocessing output)
  → intent, confidence, thought
  → boundary_triggered (bool)
  ↓
task-generation
  ← source, source_detail, task_source, receive_time, combined_text, entity_info, url_list, intent
  → task_list, sub_task_id_list, bot_task_file, duty_task_file, task_id, task_round, domain_list, is_sensitive, is_boundary
  → boundary_triggered (bool)
  ↓
task-dispatch
  ← bot_task_file, duty_task_file, task_id, task_round, sub_task_id_list
  → bot_dispatched, duty_dispatched, status_updated
  ↓
done
```

### 特殊逻辑

1. **权限检查在数据预处理内**：check_permission.py 作为 step 1.3 在 data-preprocessing 节点内执行，不再独立成节点
2. **高敏判断由 generate_task_list.py 内部处理**：脚本内部自动调用 check_sensitive.py，不再作为独立子步骤
3. **no_entity 边界在任务生成步骤**：需要先知道 intent 才能判断（钉钉/单聊 + 解除账户限制 + 无实体）
4. **Fire and Forget**：bot-dispatch 使用 BCS 异步调用，启动后即视为完成
5. **boundary_triggered 透传**：每个节点的 prompt 中包含：若上游任一节点的 boundary_triggered=true，则跳过执行，直接输出 boundary_triggered=true

## 脚本路径汇总

| 脚本 | 路径 |
|------|------|
| check_permission.py | `~/.openclaw/workspace/skills/skills-local/employee-permission-checker/scripts/check_permission.py` |
| read_dialogue_history.py | `~/.openclaw/workspace/skills/skills-local/data-preprocessing/scripts/read_dialogue_history.py` |
| save_user_query.py | `~/.openclaw/workspace/skills/skills-local/data-preprocessing/scripts/save_user_query.py` |
| userid_punish_search.py | `~/.openclaw/workspace/skills/skills-local/userid-punish-search/scripts/userid_punish_search.py` |
| generate_task_list.py | `~/.openclaw/workspace/skills/skills-local/task-generator/scripts/generate_task_list.py` |
| bcs_task_dispatcher.py | `~/.openclaw/workspace/skills/skills-local/task-dispatcher/scripts/bcs_task_dispatcher.py` |
| duty_task_notifier.py | `~/.openclaw/workspace/skills/skills-local/task-dispatcher/scripts/duty_task_notifier.py` |
| update_subtask_status.py | `~/.openclaw/workspace/skills/skills-local/task-dispatcher/scripts/update_subtask_status.py` |
| save_boundary_response.py | `~/.openclaw/workspace/skills/skills-local/boundary-reply-user/scripts/save_boundary_response.py` |
| send_dingtalk.py | `~/.openclaw/workspace/skills/skills-local/boundary-reply-user/scripts/send_dingtalk.py` |

## 产出文件

- `workflow.pack.yaml`
- `workflows/workflow-dispatcher.yaml`