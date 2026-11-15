import type { BotEditorRoutineInput } from '@/domain/botEditor';
export type RoutineStage = 'draft' | 'verify' | 'online';
export const stageNames: Record<RoutineStage, string> = { draft: '草稿态', verify: '验证态', online: '生产态' };
export interface RoutineTarget {
  runtime_stage?: RoutineStage | null;
  device_uuid?: string | null;
  operator_id?: string | null;
}
export interface RoutineScope {
  botId: string;
  userId: string;
  entityId?: string;
}
export interface RoutineEnvironment {
  runtime_stage: RoutineStage;
  available?: boolean;
  reason?: string;
  instances: string[];
}
export interface RoutineContext {
  bot_id: string;
  bot_name: string;
  owner_id: string;
  bot_type: string;
  engine: string;
  caller_mode?: boolean;
  supported: boolean;
  can_manage: boolean;
  environments: RoutineEnvironment[];
  warning?: string;
}
export interface ManagedRoutine extends RoutineTarget {
  id: string;
  key: string;
  bot_id: string;
  owner_id?: string;
  name: string;
  cron: string;
  command: string;
  enabled: boolean;
  timezone: string;
  model?: string;
  timeoutSecs: number;
  createdAt?: string;
  modifiedAt?: string;
  nextRunAt?: string;
  lastRunAt?: string;
}
export interface RoutineAddress extends RoutineTarget {
  id: string;
  key: string;
}
export interface RoutineForm extends BotEditorRoutineInput, RoutineTarget {}
export interface RoutineRunMessage {
  role: string;
  content: string;
}
export interface RoutineRun extends RoutineTarget {
  id: string;
  routineId: string;
  status: string;
  startedAt?: string;
  finishedAt?: string;
  duration?: string;
  input?: string;
  output?: string;
  error?: string;
  messages?: RoutineRunMessage[];
}
export interface FailedTarget extends RoutineTarget {
  bot_id?: string | null;
  owner_id?: string | null;
  reason: string;
}
export interface RuntimePage<T> {
  total: number;
  items: T[];
  failed_targets: FailedTarget[];
}
export interface RoutineDto extends RoutineTarget {
  routine_id: string;
  bot_id: string;
  owner_id?: string | null;
  bot_name?: string | null;
  name: string;
  trigger: { type: 'schedule'; cron: string };
  command: string;
  enabled: boolean;
  timezone?: string | null;
  model?: string | null;
  timeout_secs?: number | null;
  gmt_create?: string;
  gmt_modified?: string;
}
export interface RoutineRunDto extends RoutineTarget {
  run_id: string;
  routine_id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  input?: string | null;
  output?: string | null;
  error?: string | null;
  messages?: RoutineRunMessage[] | null;
}
export interface RoutineOperationBatch {
  results: Array<
    RoutineTarget & {
      bot_id?: string | null;
      owner_id?: string | null;
      success: boolean;
      routine: RoutineDto | null;
      run: RoutineRunDto | null;
      reason: string | null;
    }
  >;
  succeeded: number;
  failed: number;
  failed_targets: FailedTarget[];
}
export interface OperationResult {
  level: 'success' | 'warning' | 'error';
  message: string;
  batch?: RoutineOperationBatch;
  routine?: ManagedRoutine;
}
export const isStage = (value: unknown): value is RoutineStage => ['draft', 'verify', 'online'].includes(String(value));
/** Only reported nonempty fields are transmitted. An omitted field never becomes draft/current user. */
export function targetParams(target: RoutineTarget): RoutineTarget {
  return Object.fromEntries(
    ['runtime_stage', 'device_uuid', 'operator_id'].flatMap((key) => {
      const value = target[key as keyof RoutineTarget];
      return value ? [[key, value]] : [];
    }),
  );
}
export const routineKey = (
  row: RoutineTarget & { id: string; bot_id?: string; owner_id?: string },
  scope: RoutineScope,
) =>
  JSON.stringify([
    row.owner_id ?? scope.entityId ?? scope.userId,
    row.bot_id || scope.botId,
    row.runtime_stage ?? null,
    row.device_uuid ?? null,
    row.operator_id ?? null,
    row.id,
  ]);
export function addressFromKey(key: string | null, scope: RoutineScope): RoutineAddress | undefined {
  if (!key) return undefined;
  try {
    const [owner, bot, stage, device, operator, id] = JSON.parse(key);
    if (bot !== scope.botId || owner !== (scope.entityId ?? scope.userId) || typeof id !== 'string' || !id)
      return undefined;
    if (stage !== null && stage !== undefined && !isStage(stage)) return undefined;
    if ((device !== null && device !== undefined && typeof device !== 'string') || (operator !== null && operator !== undefined && typeof operator !== 'string'))
      return undefined;
    return {
      id,
      key,
      ...targetParams({
        runtime_stage: stage ?? undefined,
        device_uuid: device ?? undefined,
        operator_id: operator ?? undefined,
      }),
    };
  } catch {
    return undefined;
  }
}
export function preciseTarget(context: RoutineContext, target: RoutineTarget) {
  if (context.bot_type !== 'service') return true;
  return Boolean(target.runtime_stage && (target.device_uuid || target.operator_id));
}
export function routineInstanceIds(context: RoutineContext, stage?: RoutineStage | null): string[] {
  return [
    ...new Set(
      (context.environments.find((env) => env.runtime_stage === stage)?.instances || []).filter(
        (id) => typeof id === 'string' && id.trim(),
      ),
    ),
  ];
}
export function routineInstanceMode(context: RoutineContext, stage?: RoutineStage | null) {
  if (context.bot_type !== 'service' || (context.engine === 'teclaw' && stage === 'online')) return 'none';
  if (!stage) return 'unknown';
  const count = routineInstanceIds(context, stage).length;
  return count >= 2 ? 'multiple' : count === 1 ? 'single' : 'unknown';
}
export function validateCreateTarget(context: RoutineContext, form: RoutineTarget, actorId: string): RoutineTarget {
  if (context.bot_type !== 'service') return {};
  if (context.caller_mode === undefined) throw new Error('尚未确认 Bot 的 Caller 配置，请刷新后重试');
  if (!form.runtime_stage) throw new Error('请选择生效环境');
  if (context.caller_mode && form.runtime_stage !== 'online') throw new Error('Caller 模式仅支持生产态');
  const mode = routineInstanceMode(context, form.runtime_stage);
  if (mode === 'unknown') throw new Error('当前环境尚无可用运行实例，请确认实例已就绪后刷新');
  if (
    mode === 'multiple' &&
    (!form.device_uuid || !routineInstanceIds(context, form.runtime_stage).includes(form.device_uuid))
  )
    throw new Error('请选择当前环境中已确认的运行实例');
  return targetParams({
    runtime_stage: form.runtime_stage,
    device_uuid: mode === 'multiple' ? form.device_uuid : undefined,
    operator_id: context.caller_mode ? actorId : undefined,
  });
}
