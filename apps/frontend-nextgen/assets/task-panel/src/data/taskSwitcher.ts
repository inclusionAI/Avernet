import { mainSessionId, recordTitle, type TaskListRecord } from './taskClient';
import { normalizeTaskStatus } from './taskStatus';
import { TASK_STATUS_TONES } from '../panel/tokens';
import type { TaskSwitcherItem } from '../panel/v2/TaskSwitcherBar';
import { mapDashboard } from '../panel/taskPanelMapper';
import { resolveNodeSessionTarget } from '../panel/sessionResolution';
import type { TaskDashboardResponse } from '../panel/contract';

/** Same main conversation plus authoritative by_session relay associations; first record wins. */
export function filterTasksForCurrentConversation(
  records: readonly TaskListRecord[], currentTaskId: string, associatedTaskIds: readonly string[] = [],
): TaskListRecord[] {
  const current = records.find(record => record.task_id === currentTaskId);
  if (!current) return [];
  const session = mainSessionId(current);
  const associated = new Set(associatedTaskIds);
  const byId = new Map<string, TaskListRecord>();
  for (const record of records) {
    if (record.task_id !== currentTaskId && !associated.has(record.task_id) &&
      !(session && mainSessionId(record) === session)) continue;
    if (!byId.has(record.task_id)) byId.set(record.task_id, record);
  }
  return [...byId.values()];
}

export function toSwitcherItem(record: TaskListRecord): TaskSwitcherItem {
  const status = normalizeTaskStatus(record.status);
  return { taskId: record.task_id, title: recordTitle(record),
    status: ['EXECUTING', 'REVIEWING'].includes(status) ? 'running' : ['DRAFTING', 'DEFINED'].includes(status) ? 'pending' : 'done',
    statusLabel: ['DRAFTING', 'DEFINED'].includes(status) ? '待开始' : TASK_STATUS_TONES[status]?.label ?? record.status };
}

/** A missing dashboard/first node must never become a whole-task blind navigation. */
export function firstTaskNodeTarget(dashboard: TaskDashboardResponse, record?: TaskListRecord) {
  const task = mapDashboard(dashboard);
  const first = task.nodes[0];
  if (!first) return null;
  const target = resolveNodeSessionTarget(first, { ...task,
    mainSessionId: task.mainSessionId ?? (record ? mainSessionId(record) : null),
    sourceGroupId: task.sourceGroupId ?? (record?.execution_config?.source_group_id?.trim() ||
      record?.task_spec?.context?.extend_props?.teamclaw_context?.source_group_id?.trim() || null),
  });
  return target ? { nodeId: first.id, target } : null;
}
