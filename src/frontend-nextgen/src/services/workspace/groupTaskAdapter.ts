import type { ExecuteTaskRequest } from '@/services/tasks/taskModel';
import type { CreateGroupInput } from '@/services/workspace/groupCreateRequest';

/** 自定义协作群(state_machine) execute 建群挂载的任务副屏组件名(全路径)。 */
export const GROUP_PANEL_COMPONENT_NAME = 'taskPanel.TaskLoopView';
/**
 * 自定义协作群(state_machine) → task execute 请求适配器。
 *
 * 原 CreateGroupInput（POST /openapi/v1/collaboration/groups 请求参数）不在本函数内改动：
 * - 非自定义协作群(非 state_machine)：返回 null，由调用方回落原 createGroup 链路。
 * - state_machine：按已确认规则映射为 ExecuteTaskRequest：
 *   - task_spec.metadata.title ← input.name
 *   - task_spec.context.background ← input.context
 *   - instruction === objective === acceptances[0].acceptance === background
 *     （建群无独立"目标-验收"语义，统一填 context 内容，不留空）
 *   - task_spec.context.extend_props = {}（建群无会话/群/父任务上下文）
 *   - source_type = 'coop_group'；owner_bot_id = input.driverBotUuid；owner_user_id 由调用方补
 *   - execution_config：
 *     - task_type = 'yaml'
 *     - yaml ← input.definitionYaml
 *     - participant_bot_ids = 选中成员去掉 owner 后全部（去重）
 *     - participant_bindings：Array<{binding, actor_ids}> → Record<role, botId[]>
 *     - panel_component_name = GROUP_PANEL_COMPONENT_NAME（任务副屏 taskPanel.TaskLoopView）
 */
export function buildExecuteRequestFromGroup(input: CreateGroupInput, ownerUserId: string): ExecuteTaskRequest | null {
  if (input.strategy !== 'state_machine') return null;

  const bg = (input.context ?? '').trim();
  const ownerBotId = (input.driverBotUuid ?? '').trim();
  const participantBotIds = Array.from(
    new Set((input.participants ?? []).map((p) => p.actor_id).filter((id) => id && id !== ownerBotId)),
  );
  const participantBindings: Record<string, string[]> = {};
  for (const b of input.participantBindings ?? []) {
    if (b.binding && b.actor_ids.length) participantBindings[b.binding] = [...b.actor_ids];
  }

  return {
    task_spec: {
      metadata: { title: input.name.trim(), instruction: bg },
      context: { background: bg, extend_props: {} },
      goal: {
        objective: bg,
        acceptances: bg ? [{ id: 'ac1', acceptance: bg }] : [],
      },
    },
    source_type: 'coop_group',
    owner_user_id: ownerUserId,
    owner_bot_id: ownerBotId,
    execution_config: {
      task_type: 'yaml',
      yaml: input.definitionYaml ?? '',
      participant_bot_ids: participantBotIds,
      participant_bindings: participantBindings,
      panel_component_name: GROUP_PANEL_COMPONENT_NAME,
    },
  };
}
