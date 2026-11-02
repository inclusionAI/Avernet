import type { BotEditorRoutineInput } from '@/domain/botEditor';
export type RoutineStage = 'draft' | 'verify' | 'online';
export const stageNames: Record<RoutineStage, string> = { draft: '草稿态', verify: '验证态', online: '生产态' };
export interface RoutineTarget {
  runtime_stage?: RoutineStage;
  device_uuid?: string;
  operator_id?: string;
}
export interface RoutineEnvironment {
  runtime_stage: RoutineStage;
  available: boolean;
  reason?: string;
  requires_instance: boolean;
  instances: string[];
}
export interface RoutineContext {
  bot_id: string;
  bot_name: string;
  owner_id: string;
  bot_type: string;
  engine: string;
  caller_mode: boolean;
  supported: boolean;
  can_manage: boolean;
  environments: RoutineEnvironment[];
}
export interface ManagedRoutine extends RoutineTarget {
  id: string;
  key: string;
  name: string;
  cron: string;
  command: string;
  enabled: boolean;
  timezone: string;
  model?: string;
  timeoutSecs: number;
  nextRunAt?: string;
  lastRunAt?: string;
}
export interface RoutineForm extends BotEditorRoutineInput, RoutineTarget {}
export interface RoutineRun {
  id: string;
  status: string;
  startedAt?: string;
  finishedAt?: string;
  duration?: string;
  output?: string;
  error?: string;
  target: RoutineTarget;
}
export const targetKey = (target: RoutineTarget) =>
  JSON.stringify([target.runtime_stage ?? '', target.device_uuid ?? '', target.operator_id ?? '']);
export const routineKey = (routine: RoutineTarget & { id: string }) => JSON.stringify([routine.id, targetKey(routine)]);
/** Target fields are routing identity, never silently replaced by the current viewer. */
export function targetParams(context: RoutineContext, target: RoutineTarget, actorId: string): RoutineTarget {
  if (context.bot_type !== 'service') return {};
  if (!target.runtime_stage) throw new Error('请选择生效环境');
  if (context.caller_mode && target.runtime_stage !== 'online') throw new Error('Caller 模式仅支持生产态');
  const noInstance = context.engine.toLowerCase() === 'teclaw' && target.runtime_stage === 'online';
  if (!noInstance && !target.device_uuid) throw new Error('请选择运行实例');
  return {
    runtime_stage: target.runtime_stage,
    ...(!noInstance ? { device_uuid: target.device_uuid } : {}),
    operator_id: target.operator_id || actorId,
  };
}
