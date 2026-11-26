import { serviceBotStageLabel } from '@/domain/botWorkshop';
import type { BotEditorRoutineInput } from '@/domain/botEditor';
export type RoutineStage = 'draft' | 'verify' | 'online';
export const stageNames: Record<RoutineStage, string> = { ...serviceBotStageLabel, draft: '草稿态' };
export interface RoutineTarget {
  runtime_stage?: RoutineStage | null;
  device_uuid?: string | null;
  operator_id?: string | null;
}
export interface RoutineScope {
  botId: string;
  userId: string;
  entityId?: string;
  /** 链接查询参数指定的生效环境；线上容器清单等判定依赖它（详情记录不含发布状态）。 */
  stage?: RoutineStage;
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
/** 创建目标选择的结构类型：RoutineTarget 之上带可选的实例多选清单，兼容裸 target 与完整表单。 */
export interface RoutineCreateSelection extends RoutineTarget {
  device_uuids?: string[];
}
export interface RoutineForm extends BotEditorRoutineInput, RoutineTarget {
  /** 多实例环境下的多选运行实例；单选（device_uuid）与编辑路径不使用。 */
  device_uuids?: string[];
}
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
/** 实例选择模式：none=无需实例；single/multiple=已确认清单，multiple 必须显式选；
 * defer=未取得可信清单（草稿/预发没有清单接口），仅按 stage 提交并由后端解析
 * binding，环境未发布/不活跃时后端返回阶段化提示。 */
export function routineInstanceMode(context: RoutineContext, stage?: RoutineStage | null) {
  if (context.bot_type !== 'service' || (context.engine === 'teclaw' && stage === 'online')) return 'none';
  if (!stage) return 'none';
  const count = routineInstanceIds(context, stage).length;
  return count >= 2 ? 'multiple' : count === 1 ? 'single' : 'defer';
}
/** 多实例创建时已确认勾选的实例清单：去重并过滤掉环境清单外的成员；未勾选时回退到单选 device_uuid。 */
export function selectedCreateInstances(context: RoutineContext, form: RoutineCreateSelection): string[] {
  const confirmed = routineInstanceIds(context, form.runtime_stage);
  const picked = [...new Set(form.device_uuids || [])].filter((id) => confirmed.includes(id));
  if (picked.length > 0) return picked;
  return form.device_uuid && confirmed.includes(form.device_uuid) ? [form.device_uuid] : [];
}
export function validateCreateTarget(
  context: RoutineContext,
  form: RoutineCreateSelection,
  actorId: string,
): RoutineTarget {
  if (context.bot_type !== 'service') return {};
  if (context.caller_mode === undefined) throw new Error('尚未确认 Bot 的 Caller 配置，请刷新后重试');
  if (!form.runtime_stage) throw new Error('请选择生效环境');
  if (context.caller_mode && form.runtime_stage !== 'online') throw new Error('Caller 模式仅支持正式运行环境');
  const mode = routineInstanceMode(context, form.runtime_stage);
  if (mode === 'multiple' && selectedCreateInstances(context, form).length === 0)
    throw new Error('请选择当前环境中已确认的运行实例');
  return targetParams({
    runtime_stage: form.runtime_stage,
    device_uuid: mode === 'multiple' ? form.device_uuid : undefined,
    operator_id: context.caller_mode ? actorId : undefined,
  });
}
/** 创建请求目标展开：多实例环境按勾选实例逐个展开（每目标一个 device_uuid），
 * 其余模式维持单目标；Caller 的 operator_id 随基础目标进入每个请求。 */
export function createRequestTargets(
  context: RoutineContext,
  form: RoutineCreateSelection,
  actorId: string,
): RoutineTarget[] {
  const base = validateCreateTarget(context, form, actorId);
  if (routineInstanceMode(context, form.runtime_stage) !== 'multiple') return [base];
  return selectedCreateInstances(context, form).map((device_uuid) => ({ ...base, device_uuid }));
}
