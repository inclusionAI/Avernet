import type { EditLockDto } from '@/services/backendApi/bots/botEditorController';
import { sendRoutineRequest } from './http';
import { loadRoutineContext } from './metadata';
import { loadRoutineLock } from './locks';
import { batchOutcome, isBatch, runOutcome } from './results';
import {
  createRequestTargets,
  routineKey,
  routineIdentityParams,
  targetParams,
  type RoutineAddress,
  type RoutineStage,
  type ManagedRoutine,
  type RoutineContext,
  type RoutineForm,
  type RoutineRun,
  type RoutineScope,
  type RoutineDto,
  type RoutineRunDto,
  type RuntimePage,
  type RoutineTarget,
  type OperationResult,
  type RoutineOperationBatch,
} from './types';
export type RoutineLock = EditLockDto;
function retainTarget<T extends RoutineTarget>(row: T, addressed: RoutineTarget): T {
  return {
    ...row,
    runtime_stage: row.runtime_stage ?? addressed.runtime_stage,
    device_uuid: row.device_uuid ?? addressed.device_uuid,
    operator_id: row.operator_id ?? addressed.operator_id,
  };
}
export function mapManagedRoutine(row: RoutineDto, scope: RoutineScope): ManagedRoutine {
  const address = {
    ...targetParams(row),
    id: row.routine_id,
    bot_id: row.bot_id || scope.botId,
    owner_id: row.owner_id ?? scope.ownerId ?? scope.entityId ?? scope.userId,
  };
  return {
    ...address,
    key: routineKey(address, scope),
    name: row.name,
    cron: row.trigger.cron,
    command: row.command,
    enabled: row.enabled,
    timezone: row.timezone ?? '',
    model: row.model ?? undefined,
    timeoutSecs: row.timeout_secs ?? 86400,
    createdAt: row.gmt_create,
    modifiedAt: row.gmt_modified,
  };
}
function mapRun(row: RoutineRunDto): RoutineRun {
  const start = Date.parse(row.started_at || '');
  const end = Date.parse(row.finished_at || '');
  return {
    ...targetParams(row),
    id: row.run_id,
    routineId: row.routine_id,
    status: row.status || '未知',
    startedAt: row.started_at ?? undefined,
    finishedAt: row.finished_at ?? undefined,
    input: row.input ?? undefined,
    output: row.output ?? undefined,
    error: row.error ?? undefined,
    messages: row.messages ?? [],
    duration: Number.isFinite(start) && Number.isFinite(end) && end >= start ? `${(end - start) / 1000} 秒` : undefined,
  };
}
export function createRoutineClient(
  botId: string,
  entityId: string | undefined,
  actorId: string,
  stage?: RoutineStage,
  legacyOwnerId?: string,
) {
  const scope: RoutineScope = { botId, userId: actorId, entityId, ownerId: legacyOwnerId, stage };
  const base = `/openapi/v1/bots/${encodeURIComponent(botId)}`;
  const params = routineIdentityParams(scope);
  const path = (id: string) => `/${encodeURIComponent(id)}`;
  function request<T>(suffix: string, method = 'GET', query: object = {}, data?: unknown) {
    if (!actorId) throw new Error('当前登录身份尚未就绪');
    return sendRoutineRequest<T>(`${base}/routines${suffix}`, { method, params: { ...params, ...query }, data });
  }
  function completeTarget(target: RoutineTarget) {
    if ((target.device_uuid || target.operator_id) && !target.runtime_stage)
      throw new Error('运行目标缺少生效环境，不能自动补为草稿态');
    return targetParams(target);
  }
  return {
    scope,
    context: () => loadRoutineContext(scope),
    getLock: (context: Pick<RoutineContext, 'bot_type' | 'owner_id'>) => loadRoutineLock(scope, context),
    async list(target: RoutineTarget = {}, page = 1, pageSize = 20): Promise<RuntimePage<ManagedRoutine>> {
      const result = await request<RuntimePage<RoutineDto>>('', 'GET', {
        ...completeTarget(target),
        page,
        page_size: pageSize,
      });
      return {
        total: result.total,
        items: result.items.map((row) => mapManagedRoutine(retainTarget(row, target), scope)),
        failed_targets: result.failed_targets || [],
      };
    },
    async detail(_context: RoutineContext, target: RoutineAddress) {
      const row = await request<RoutineDto>(path(target.id), 'GET', targetParams(target));
      // A detail response can omit identity fields. Keep only previously verified address fields.
      return mapManagedRoutine(
        {
          ...row,
          bot_id: row.bot_id || scope.botId,
          runtime_stage: row.runtime_stage ?? target.runtime_stage,
          device_uuid: row.device_uuid ?? target.device_uuid,
          operator_id: row.operator_id ?? target.operator_id,
        },
        scope,
      );
    },
    async save(context: RoutineContext, form: RoutineForm, original?: ManagedRoutine): Promise<OperationResult> {
      const content = {
        name: form.name.trim(),
        trigger: { type: 'schedule' as const, cron: form.cron.trim() },
        command: form.command.trim(),
        enabled: form.enabled,
        timezone: form.timezone,
        model: form.model || undefined,
        timeout_secs: form.timeoutSecs,
      };
      if (!original) {
        const targets = createRequestTargets(context, form, actorId);
        if (targets.length > 1) {
          // 多实例创建：实例彼此独立，逐个提交；单个失败不阻断后续实例。
          const failures: Array<{ device_uuid: string; reason: string }> = [];
          for (const target of targets) {
            try {
              await request<RoutineDto>('', 'POST', {}, { ...content, ...target });
            } catch (reason) {
              failures.push({
                device_uuid: String(target.device_uuid),
                reason: reason instanceof Error ? reason.message : '创建失败',
              });
            }
          }
          if (failures.length > 0)
            return {
              level: 'error',
              message: `已创建 ${targets.length - failures.length}/${targets.length} 个实例；失败：${failures
                .map((failure) => `${failure.device_uuid}（${failure.reason}）`)
                .join('、')}。已成功创建的实例无需重复勾选`,
            };
          return { level: 'success', message: `任务已在 ${targets.length} 个实例上创建` };
        }
        const target = targets[0] ?? {};
        const row = await request<RoutineDto>('', 'POST', {}, { ...content, ...target });
        const routine = mapManagedRoutine(
          {
            ...row,
            ...targetParams({
              runtime_stage: row.runtime_stage ?? target.runtime_stage,
              device_uuid: row.device_uuid ?? target.device_uuid,
              operator_id: row.operator_id ?? target.operator_id,
            }),
          },
          scope,
        );
        return { level: 'success', message: '任务已创建', routine };
      }
      if (
        original.runtime_stage &&
        original.runtime_stage !== 'draft' &&
        !original.device_uuid &&
        !original.operator_id
      )
        throw new Error('当前发布态任务缺少精确目标，只能启停，不能提交完整编辑表单');
      const result = await request<RoutineDto | RoutineOperationBatch>(
        path(original.id),
        'PATCH',
        targetParams(original),
        content,
      );
      return isBatch(result)
        ? batchOutcome(result, 'update')
        : {
            level: 'success',
            message: '任务已更新',
            routine: mapManagedRoutine(retainTarget(result, original), scope),
          };
    },
    async toggle(_context: RoutineContext, task: ManagedRoutine): Promise<OperationResult> {
      const result = await request<RoutineDto | RoutineOperationBatch>(path(task.id), 'PATCH', targetParams(task), {
        enabled: !task.enabled,
      });
      return isBatch(result)
        ? batchOutcome(result, 'update')
        : { level: 'success', message: task.enabled ? '任务已停用' : '任务已启用' };
    },
    async remove(_context: RoutineContext, task: ManagedRoutine): Promise<OperationResult> {
      if (task.runtime_stage && task.runtime_stage !== 'draft' && !task.device_uuid && !task.operator_id)
        throw new Error('发布态删除需要明确的实例或 Caller 目标');
      const result = await request<{ deleted: boolean }>(path(task.id), 'DELETE', targetParams(task));
      if (!result.deleted) throw new Error('删除未确认，请刷新核实');
      return { level: 'success', message: '任务已删除' };
    },
    async run(_context: RoutineContext, task: ManagedRoutine): Promise<OperationResult> {
      const result = await request<RoutineRunDto | RoutineOperationBatch>(
        `${path(task.id)}/run`,
        'POST',
        targetParams(task),
      );
      return isBatch(result) ? batchOutcome(result, 'run') : runOutcome(result);
    },
    async runs(
      _context: RoutineContext,
      task: RoutineAddress,
      page = 1,
      pageSize = 20,
    ): Promise<RuntimePage<RoutineRun>> {
      const target = completeTarget(task);
      const result = await request<RuntimePage<RoutineRunDto>>(`${path(task.id)}/runs`, 'GET', {
        ...target,
        page,
        page_size: pageSize,
      });
      return {
        total: result.total,
        failed_targets: result.failed_targets || [],
        items: result.items
          .map(mapRun)
          .sort((a, b) => (Date.parse(b.startedAt || '') || 0) - (Date.parse(a.startedAt || '') || 0)),
      };
    },
  };
}
