import { botEditorController, type EditLockDto } from '@/services/backendApi/bots/botEditorController';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { BackendUnknownRecord } from '@/services/backendApi/types';
import {
  routineKey,
  targetParams,
  type ManagedRoutine,
  type RoutineContext,
  type RoutineForm,
  type RoutineRun,
  type RoutineTarget,
} from './types';
export type RoutineLock = EditLockDto;

interface CronEnvelope {
  success: boolean;
  message?: string;
  data?: unknown;
  failed_targets?: Array<{ message?: string }>;
}
const record = (value: unknown): BackendUnknownRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as BackendUnknownRecord) : {};
const text = (value: unknown): string | undefined => (typeof value === 'string' && value.trim() ? value : undefined);
function date(value: unknown): string | undefined {
  if (typeof value !== 'number') return text(value);
  const parsed = new Date(value);
  return value > 0 && Number.isFinite(parsed.getTime()) ? parsed.toISOString() : undefined;
}
function items(data: unknown): unknown[] {
  if (Array.isArray(data)) return data;
  const object = record(data);
  return Array.isArray(object.items) ? object.items : Array.isArray(object.runs) ? object.runs : [];
}
export function mapManagedRoutine(value: unknown): ManagedRoutine {
  const row = record(value);
  const bot = record(row.bot);
  const schedule = record(row.schedule);
  const payload = record(row.payload);
  const state = record(row.state);
  const target: RoutineTarget = {
    runtime_stage: (row.runtime_stage || bot.runtime_stage) as RoutineTarget['runtime_stage'],
    device_uuid: text(row.device_uuid) || text(bot.device_uuid),
    operator_id: text(row.operator_id) || text(bot.operator_id),
  };
  const id = text(row.task_id) || text(row.id) || text(row.routine_id) || '';
  return {
    ...target,
    id,
    key: routineKey({ ...target, id }),
    name: text(row.name) || '未命名任务',
    cron: text(row.schedule) || text(schedule.expr) || text(record(row.trigger).cron) || '',
    command: text(row.command) || text(payload.message) || text(payload.prompt) || '',
    enabled: row.enabled === true,
    timezone: text(row.timezone) || text(schedule.tz) || 'Asia/Shanghai',
    model: text(row.model) || text(payload.model),
    timeoutSecs: Number(row.timeout_secs ?? payload.timeout_secs) || 86400,
    nextRunAt: date(row.next_run_at) || date(row.next_run_at_ms) || date(state.next_run_at_ms),
    lastRunAt: date(row.last_run_at) || date(row.last_run_at_ms) || date(state.last_run_at_ms),
  };
}
export function createRoutineClient(botId: string, ownerId: string | undefined, actorId: string) {
  const base = { bot_id: botId, owner_id: ownerId, user_id: actorId };
  async function request(path: string, method = 'GET', params: object = {}, data?: unknown) {
    const response = await backendRequest<CronEnvelope>(`/api/cron${path}`, {
      method,
      params: { ...base, ...params },
      data,
      retryOnTransient: false,
    });
    if (response.success !== true) throw new Error(response.message || '定时任务操作失败');
    return response;
  }
  const path = (id: string) => `/${encodeURIComponent(id)}`;
  return {
    async getLock(): Promise<EditLockDto> {
      const response = await botEditorController.getEditLock(botId, ownerId);
      if (isEnvelopeFailure(response) || !response.data) throw new Error(response.message || '编辑锁状态不可用');
      return response.data;
    },
    async lockAction(action: 'acquire' | 'steal' | 'release') {
      const response =
        action === 'release'
          ? await botEditorController.releaseEditLock(botId, ownerId)
          : action === 'steal'
          ? await botEditorController.stealEditLock(botId, ownerId)
          : await botEditorController.acquireEditLock(botId, ownerId);
      if (isEnvelopeFailure(response)) throw new Error(response.message || '编辑锁操作失败');
      if (action !== 'release') {
        const result = response.data as EditLockDto | undefined;
        if (!(result?.acquired || (result?.need_lock === false && result.locked === false)))
          throw new Error('未获取编辑锁，请刷新后重试');
      }
    },
    async context(): Promise<RoutineContext> {
      return (await request('/targets')).data as RoutineContext;
    },
    async list() {
      const response = await request('');
      return { items: items(response.data).map(mapManagedRoutine), failures: response.failed_targets || [] };
    },
    async detail(context: RoutineContext, target: ManagedRoutine) {
      const response = await request(path(target.id), 'GET', targetParams(context, target, actorId));
      return {
        ...mapManagedRoutine(response.data),
        id: target.id,
        key: target.key,
        ...targetParams(context, target, actorId),
      };
    },
    async save(context: RoutineContext, form: RoutineForm, original?: ManagedRoutine) {
      const target = targetParams(context, original || form, actorId);
      const body = {
        name: form.name.trim(),
        schedule: form.cron.trim(),
        command: form.command.trim(),
        enabled: form.enabled,
        timezone: form.timezone,
        model: form.model || undefined,
        timeout_secs: form.timeoutSecs,
      };
      return original
        ? request(path(original.id), 'PUT', target, body)
        : request('', 'POST', {}, { ...base, ...target, ...body });
    },
    toggle: (context: RoutineContext, task: ManagedRoutine) =>
      request(path(task.id), 'PUT', targetParams(context, task, actorId), { enabled: !task.enabled }),
    remove: (context: RoutineContext, task: ManagedRoutine) =>
      request(path(task.id), 'DELETE', targetParams(context, task, actorId)),
    async run(context: RoutineContext, task: ManagedRoutine) {
      const response = await request(`${path(task.id)}/run`, 'POST', {
        ...targetParams(context, task, actorId),
        force: true,
      });
      const result = record(response.data);
      if (result.success === false || result.status === 'failed' || result.status === 'not_due')
        throw new Error(text(result.error) || '执行请求未被接受');
    },
    async runs(context: RoutineContext, task: ManagedRoutine): Promise<RoutineRun[]> {
      const response = await request(`${path(task.id)}/runs`, 'GET', {
        ...targetParams(context, task, actorId),
        limit: 100,
      });
      return items(response.data)
        .map((value) => {
          const row = record(value);
          const startedAt = date(row.started_at) || date(row.started_at_ms);
          const finishedAt = date(row.finished_at) || date(row.finished_at_ms);
          const duration =
            startedAt && finishedAt
              ? `${Math.max(0, (Date.parse(finishedAt) - Date.parse(startedAt)) / 1000)} 秒`
              : undefined;
          return {
            id: String(row.run_id || row.job_id || row.id || '—'),
            status: text(row.status) || '未知',
            startedAt,
            finishedAt,
            duration,
            output: text(row.output) || text(row.output_summary),
            error: text(row.error) || text(row.error_message),
            target: {
              runtime_stage: (text(row.runtime_stage) as RoutineTarget['runtime_stage']) || task.runtime_stage,
              device_uuid: text(row.device_uuid) || task.device_uuid,
              operator_id: text(row.operator_id) || task.operator_id,
            },
          };
        })
        .sort((a, b) => (Date.parse(b.startedAt || '') || 0) - (Date.parse(a.startedAt || '') || 0));
    },
  };
}
