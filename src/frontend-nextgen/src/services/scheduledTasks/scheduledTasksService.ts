import {
  getBotRoutine,
  listBotRoutineRuns,
  listBotRoutines,
  runBotRoutine,
  type BotRoutineDto,
  type BotRoutineRunDto,
} from '@/services/backendApi';
import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';

export interface ScheduledRoutineRecord {
  id: string;
  botId: string;
  name: string;
  botName: string;
  model: string;
  frequency: string;
  timezone?: string;
  nextRunAt?: string;
  lastRunAt?: string;
  prompt?: string;
  raw: BotRoutineDto;
}

export interface ScheduledRoutineRunRecord {
  id: string;
  botId: string;
  botName?: string;
  routineId: string;
  routineName: string;
  instanceNo: string;
  status: string;
  plannedTriggerAt?: string;
  actualTriggerAt?: string;
  duration?: string;
  taskName?: string;
  outputSummary?: string;
  errorMessage?: string;
  raw: BotRoutineRunDto;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function pickString(source: Record<string, unknown> | undefined, keys: string[]): string | undefined {
  if (!source) return undefined;
  for (const key of keys) {
    const value = asString(source[key]);
    if (value) return value;
  }
  return undefined;
}

function pickNestedString(source: Record<string, unknown> | undefined, path: string[]): string | undefined {
  let cursor: unknown = source;
  for (const key of path) {
    if (!isRecord(cursor)) return undefined;
    cursor = cursor[key];
  }
  return asString(cursor);
}

function unwrapPage<T>(payload: { data?: BackendApiPage<T> | T[] | null } | null | undefined): T[] {
  const data = payload?.data;
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

function formatDuration(start?: string, end?: string): string | undefined {
  if (!start || !end) return undefined;
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) return undefined;
  const minutes = Math.max(1, Math.round((endMs - startMs) / 60_000));
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

export function mapScheduledRoutineRecord(item: BotRoutineDto): ScheduledRoutineRecord {
  const source = isRecord(item) ? item : {};
  const trigger = isRecord(source.trigger) ? source.trigger : undefined;
  const model = pickString(source, ['model', 'llm_model', 'bot_model', 'owner_bot_model']) ?? '—';
  const scheduleText = pickString(source, ['frequency', 'schedule_text', 'cron_text', 'trigger_text']);
  const cronText = pickNestedString(trigger, ['cron']) ?? pickString(source, ['cron']);
  const botId = pickString(source, ['bot_id', 'botId', 'owner_bot_id', 'ownerBotId']) ?? 'unknown-bot';
  const frequency = scheduleText ?? (cronText ? `cron: ${cronText}` : '—');
  return {
    id: pickString(source, ['routine_id', 'routineId', 'id']) ?? 'unknown-routine',
    botId,
    name: pickString(source, ['name', 'routine_name', 'title']) ?? '未命名定时任务',
    botName:
      pickString(source, ['bot_name', 'owner_bot_name', 'owner_name', 'bot_id', 'botId', 'owner_bot_id']) ?? botId,
    model,
    frequency,
    timezone: pickString(source, ['timezone', 'tz']),
    nextRunAt: pickString(source, ['next_run_at', 'next_run_time', 'next_fire_at', 'nextTriggerAt']),
    lastRunAt: pickString(source, ['last_run_at', 'last_run_time', 'last_finished_at', 'gmt_modified']),
    prompt:
      pickString(source, ['prompt', 'command', 'description', 'summary']) ??
      pickNestedString(source, ['task_spec', 'metadata', 'instruction']),
    raw: item,
  };
}

export function mapScheduledRoutineRunRecord(
  item: BotRoutineRunDto,
  routine?: ScheduledRoutineRecord,
): ScheduledRoutineRunRecord {
  const source = isRecord(item) ? item : {};
  const runInfo = isRecord(source.run_info) ? source.run_info : undefined;
  const botId = pickString(source, ['bot_id', 'botId']) ?? routine?.botId ?? 'unknown-bot';
  const plannedTriggerAt =
    pickString(source, ['planned_trigger_at', 'planned_at', 'scheduled_at', 'schedule_time']) ??
    pickString(runInfo, ['planned_trigger_at', 'planned_at', 'scheduled_at']);
  const actualTriggerAt =
    pickString(source, ['actual_trigger_at', 'started_at', 'actual_started_at']) ??
    pickString(runInfo, ['actual_trigger_at', 'started_at', 'actual_started_at']);
  const finishedAt =
    pickString(source, ['finished_at', 'completed_at', 'ended_at']) ??
    pickString(runInfo, ['finished_at', 'completed_at', 'ended_at']);
  const duration =
    pickString(source, ['duration', 'cost_time', 'elapsed_time']) ??
    pickString(runInfo, ['duration', 'cost_time', 'elapsed_time']) ??
    formatDuration(actualTriggerAt, finishedAt);
  return {
    id: pickString(source, ['run_id', 'instance_id', 'id', 'execution_id']) ?? 'unknown-run',
    botId,
    botName:
      pickString(source, ['bot_name', 'owner_bot_name']) ??
      routine?.botName ??
      pickString(source, ['bot_id', 'botId']) ??
      botId,
    routineId: pickString(source, ['routine_id', 'routineId']) ?? routine?.id ?? 'unknown-routine',
    routineName: pickString(source, ['routine_name', 'name', 'title']) ?? routine?.name ?? '未命名定时任务',
    instanceNo:
      pickString(source, ['instance_no', 'instanceNo', 'run_no']) ??
      pickString(source, ['run_id', 'instance_id', 'id']) ??
      '—',
    status: pickString(source, ['status']) ?? 'unknown',
    plannedTriggerAt,
    actualTriggerAt,
    duration,
    taskName:
      pickString(source, ['task_name', 'taskName']) ??
      pickNestedString(source, ['task_spec', 'metadata', 'title']) ??
      routine?.name,
    outputSummary:
      pickString(source, ['output_summary', 'summary', 'message']) ??
      pickNestedString(runInfo, ['output_summary']) ??
      pickNestedString(runInfo, ['output', 'summary']),
    errorMessage:
      pickString(source, ['error_message', 'error', 'reason']) ??
      pickNestedString(runInfo, ['error_message']) ??
      pickNestedString(runInfo, ['error']),
    raw: item,
  };
}

export async function fetchScheduledRoutines(
  botId: string,
  params: BackendUnknownRecord = {},
): Promise<ScheduledRoutineRecord[]> {
  if (!botId) return [];
  const result = await listBotRoutines(botId, { page: 1, page_size: 100, ...params });
  return unwrapPage(result).map(mapScheduledRoutineRecord);
}

export async function fetchScheduledRoutineDetail(
  botId: string,
  routineId: string,
): Promise<ScheduledRoutineRecord | null> {
  if (!botId || !routineId) return null;
  const result = await getBotRoutine(botId, routineId);
  if (!result.data) return null;
  return mapScheduledRoutineRecord(result.data);
}

export async function fetchScheduledRoutineRuns(
  botId: string,
  routineId: string,
  params: BackendUnknownRecord = {},
  routine?: ScheduledRoutineRecord | null,
): Promise<ScheduledRoutineRunRecord[]> {
  if (!botId || !routineId) return [];
  const result = await listBotRoutineRuns(botId, routineId, { page: 1, page_size: 100, ...params });
  const currentRoutine = routine ?? (await fetchScheduledRoutineDetail(botId, routineId).catch(() => null));
  return unwrapPage(result).map((item) => mapScheduledRoutineRunRecord(item, currentRoutine ?? undefined));
}

export async function triggerScheduledRoutine(botId: string, routineId: string) {
  if (!botId || !routineId) {
    throw new Error('缺少 botId 或 routineId');
  }
  return runBotRoutine(botId, routineId);
}

export const scheduledTasksService = {
  getOverview() {
    return {
      module: 'scheduledTasks',
      description: '定时任务 Service 通过 botRoutineController 接入任务列表和运行记录。',
    };
  },
  fetchScheduledRoutines,
  fetchScheduledRoutineDetail,
  fetchScheduledRoutineRuns,
  triggerScheduledRoutine,
};
