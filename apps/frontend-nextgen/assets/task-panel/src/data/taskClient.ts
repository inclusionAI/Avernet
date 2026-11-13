import type { TaskPanelHost } from '../contracts';
export interface TaskListRecord {
  task_id: string; status: string; gmt_create?: string; gmt_modified?: string;
  owner_bot_id?: string; owner_bot_name?: string; source_type?: string;
  execution_config?: { main_session_id?: string; source_group_id?: string; task_type?: string };
  task_spec?: { goal?: { objective?: string }; context?: { title?: string; extend_props?: {
    teamclaw_context?: { main_session_id?: string; source_group_id?: string };
  } } };
}
export function unwrapResponse(value: unknown): unknown {
  if (value && typeof value === 'object' && 'code' in value) {
    const body = value as { code: unknown; message?: string; data?: unknown };
    const numeric = Number(body.code);
    const httpStatus = Math.floor(numeric / 1000);
    const success = (value as { success?: boolean }).success === true || (httpStatus >= 200 && httpStatus <= 299);
    if (!success) {
      throw Object.assign(new Error(body.message || `业务错误码 ${body.code}`), { status: Number.isFinite(numeric) ? httpStatus : undefined, code: body.code });
    }
  }
  return value && typeof value === 'object' && 'data' in value ? (value as { data: unknown }).data : value;
}
function invalidResponse(path: string, expected: string): never {
  // Field paths are static schema locations: never include response values or keys.
  throw new Error(`Invalid task response at ${path}: expected ${expected}`);
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function responseItems(value: unknown): unknown[] {
  const data = unwrapResponse(value);
  if (Array.isArray(data)) return data;
  if (isRecord(data) && Array.isArray(data.items)) return data.items;
  return invalidResponse('items', 'an array or an object containing an items array');
}
function taskReference(value: unknown, index: number): { task_id: string } {
  if (!isRecord(value) || typeof value.task_id !== 'string' || !value.task_id.trim()) {
    invalidResponse(`items[${index}].task_id`, 'a non-empty string');
  }
  return value as { task_id: string };
}
// Validate only the metadata this client consumes; unknown backend fields remain opaque.
type Shape = { readonly [key: string]: 'string' | Shape };
const metadataShape: Shape = {
  status: 'string', gmt_create: 'string', gmt_modified: 'string',
  owner_bot_id: 'string', owner_bot_name: 'string', source_type: 'string',
  execution_config: { main_session_id: 'string', source_group_id: 'string', task_type: 'string' },
  task_spec: { goal: { objective: 'string' }, context: { title: 'string', extend_props: {
    teamclaw_context: { main_session_id: 'string', source_group_id: 'string' },
  } } },
};
function validateMetadata(value: Record<string, unknown>, shape: Shape, path: string): void {
  for (const [key, expected] of Object.entries(shape)) {
    const field = value[key];
    // Optional backend metadata may be absent or null.
    if (field === undefined || field === null) continue;
    const fieldPath = `${path}.${key}`;
    if (expected === 'string') {
      if (typeof field !== 'string') invalidResponse(fieldPath, 'a string');
    } else {
      if (!isRecord(field)) invalidResponse(fieldPath, 'an object');
      validateMetadata(field, expected, fieldPath);
    }
  }
}
export function createTaskClient(host: TaskPanelHost) {
  const base = host.taskApiBase ?? '/openapi/v1/collaboration/tasks';
  return {
    async bySession(sessionId: string, signal: AbortSignal): Promise<string[]> {
      return responseItems(await host.transport.requestJson(`${base}/by_session?session_id=${encodeURIComponent(sessionId)}`, signal))
        .map((value, index) => taskReference(value, index).task_id);
    },
    async list(userId: string, signal: AbortSignal): Promise<TaskListRecord[]> {
      const rows = responseItems(await host.transport.requestJson(`${base}/list?user_id=${encodeURIComponent(userId)}`, signal));
      return rows.map((value, index) => {
        taskReference(value, index);
        validateMetadata(value as Record<string, unknown>, metadataShape, `items[${index}]`);
        return value as TaskListRecord;
      });
    },
  };
}
export function mainSessionId(record: TaskListRecord): string | null {
  return record.execution_config?.main_session_id?.trim() || record.task_spec?.context?.extend_props?.teamclaw_context?.main_session_id?.trim() || null;
}
export function recordTitle(record: TaskListRecord): string {
  return record.task_spec?.context?.title?.trim() || record.task_spec?.goal?.objective?.trim() || `任务 ${record.task_id}`;
}
