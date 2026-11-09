import type { BackendUnknownRecord } from '@/services/backendApi/types';
import { sendRoutineRequest } from './http';
import { isStage, type ManagedRoutine, type RoutineContext, type RoutineEnvironment, type RoutineScope } from './types';
const string = (value: unknown) => (typeof value === 'string' ? value : '');
/** Reuse published Bot/editor/Caller APIs; the previous local /api/cron/targets is not a contract. */
export async function loadRoutineContext(scope: RoutineScope): Promise<RoutineContext> {
  const path = `/openapi/v1/bots/${encodeURIComponent(scope.botId)}`;
  const params = { user_id: scope.userId, entity_id: scope.entityId };
  const bot = await sendRoutineRequest<BackendUnknownRecord>(path, { params });
  const owner = string(bot.owner_id) || string(bot.owner_entity_id) || scope.entityId || '';
  const service = String(bot.bot_type).toLowerCase() === 'service';
  let canManage = Boolean(owner && owner === scope.userId);
  let caller: boolean | undefined = service ? undefined : false;
  const warnings: string[] = [];
  const [roles, callerContext] = await Promise.allSettled([
    canManage
      ? Promise.resolve({ items: [] })
      : sendRoutineRequest<{ items: Array<{ user_id: string; role: string }> }>(`${path}/editors`, { params }),
    service
      ? sendRoutineRequest<{ bot_call_type: 'caller' | 'owner' }>(`${path}/caller-context`, {
          params: { ...params, stage: 'draft' },
        })
      : Promise.resolve({ bot_call_type: 'owner' }),
  ]);
  if (roles.status === 'fulfilled')
    canManage ||= roles.value.items.some((item) => item.user_id === scope.userId && item.role === 'admin');
  else warnings.push('无法确认管理员权限，当前仅可查看');
  if (callerContext.status === 'fulfilled' && ['caller', 'owner'].includes(callerContext.value.bot_call_type))
    caller = callerContext.value.bot_call_type === 'caller';
  else if (service) warnings.push('Caller 配置读取失败，暂不能创建任务，请刷新重试');
  const engine = string(bot.engine).toLowerCase();
  const environments: RoutineEnvironment[] = (['draft', 'verify', 'online'] as const).map((stage) => ({
    runtime_stage: stage,
    instances: [],
    reason: caller && stage !== 'online' ? 'Caller 模式仅支持生产态' : '当前尚未取得该环境的可信实例清单',
  }));
  // This existing endpoint reports ONLINE only. Never reuse its UUIDs for draft/verify or Caller bindings.
  if (service && caller === false && engine !== 'teclaw') {
    try {
      const containers = await sendRoutineRequest<{ instances: Array<{ id: string }> }>(`${path}/containers`, {
        params,
      });
      environments[2].instances = containers.instances.flatMap((item) => (item.id ? [item.id] : []));
    } catch {
      warnings.push('生产运行实例暂不可读取；可使用任务回包中已确认的实例');
    }
  }
  return {
    bot_id: scope.botId,
    bot_name: string(bot.bot_name) || scope.botId,
    owner_id: owner,
    bot_type: service ? 'service' : 'personal',
    engine,
    caller_mode: caller,
    can_manage: canManage,
    supported: Boolean(engine && engine !== 'hermes' && (bot.bot_type !== 'desktop' || engine === 'openclaw')),
    environments,
    warning: warnings.join('；') || undefined,
  };
}
export function addReportedTargets(context: RoutineContext, rows: ManagedRoutine[], actorId?: string): RoutineContext {
  return {
    ...context,
    environments: context.environments.map((env) => ({
      ...env,
      instances: [
        ...new Set([
          ...env.instances,
          ...rows
            .filter(
              (row) =>
                isStage(row.runtime_stage) &&
                row.runtime_stage === env.runtime_stage &&
                (!context.caller_mode || row.operator_id === actorId),
            )
            .flatMap((row) => (row.device_uuid ? [row.device_uuid] : [])),
        ]),
      ],
    })),
  };
}
