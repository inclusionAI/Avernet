import type { TaskListItem } from './models';
/** Task-list association metadata, independent of the side-panel runtime. */
export function resolveTaskMainSessionId(record: TaskListItem): string | null {
  return record.execution_config?.main_session_id?.trim() ||
    record.task_spec?.context?.extend_props?.teamclaw_context?.main_session_id?.trim() || null;
}
