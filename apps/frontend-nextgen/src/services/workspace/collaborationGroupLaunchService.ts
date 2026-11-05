import { createGroup } from '@/services/backendApi/collaboration/collaborationGroupController';
import {
  canExecuteValidatedCollaboration,
  collaborationDefinitionService,
  formatValidationErrors,
} from './collaborationDefinitionService';
import { buildCreateGroupBody, type CreateGroupInput } from './groupCreateRequest';
import type { DomainResult } from './identityService';

export interface CollaborationGroupLaunchReceipt {
  groupId: string;
  driverBotId: string;
  initialSessionId?: string;
}

function failure(code: string, friendlyMessage: string): DomainResult<never> {
  return { ok: false, error: { code, friendlyMessage, canRetry: code !== 'GROUP_CREATE_OUTCOME_UNKNOWN' } };
}

async function withTimeout<T>(timeoutMs: number, request: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await request(controller.signal);
  } finally {
    clearTimeout(timer);
  }
}

/** 提交快照在预检前固定；OpenAPI 建群同时创建初始会话并启动状态机。 */
export async function launchCollaborationGroup(
  input: CreateGroupInput,
): Promise<DomainResult<CollaborationGroupLaunchReceipt>> {
  const snapshot: CreateGroupInput = JSON.parse(JSON.stringify(input));
  if (snapshot.strategy !== 'state_machine' || !snapshot.context?.trim() || !snapshot.definitionYaml?.trim()) {
    return failure('GROUP_CREATE_INVALID', '请填写协作目标和自定义协作 YAML');
  }
  const yaml = snapshot.definitionYaml;
  const validation = await withTimeout(15_000, (signal) => collaborationDefinitionService.validate(yaml, signal));
  if (!validation.ok) return validation;
  if (!validation.data.valid) return failure('GROUP_CREATE_INVALID', formatValidationErrors(validation.data.errors));
  if (!canExecuteValidatedCollaboration(validation.data.warnings)) {
    return failure('GROUP_CREATE_INVALID', formatValidationErrors(validation.data.warnings));
  }
  const bindings = snapshot.participantBindings ?? [];
  const slots = validation.data.participants;
  const members = new Set(snapshot.participants.map((participant) => participant.actor_id));
  if (
    !slots.length ||
    !members.has(snapshot.driverBotUuid) ||
    slots.some(
      (slot) => slot.required && !bindings.some((item) => item.binding === slot.binding && item.actor_ids.length),
    ) ||
    bindings.some(
      (item) => !slots.some((slot) => slot.binding === item.binding) || item.actor_ids.some((id) => !members.has(id)),
    )
  ) {
    return failure('GROUP_CREATE_INVALID', '角色定义或绑定已变化，请重新校验并完成角色绑定');
  }
  try {
    const { data } = await withTimeout(60_000, (signal) => createGroup(buildCreateGroupBody(snapshot), signal));
    if (!data?.group_id) return failure('GROUP_CREATE_OUTCOME_UNKNOWN', '建群结果暂未确认，请先到协作群列表查看');
    return {
      ok: true,
      data: {
        groupId: data.group_id,
        driverBotId: snapshot.driverBotUuid,
        initialSessionId: data.initial_session_id,
      },
    };
  } catch (error) {
    const status = (error as { status?: number }).status;
    if (status && status >= 400 && status < 500 && status !== 408) {
      return failure(
        'GROUP_CREATE_REJECTED',
        error instanceof Error ? error.message : '建群请求被拒绝，请检查参数和权限',
      );
    }
    return failure('GROUP_CREATE_OUTCOME_UNKNOWN', '建群结果暂未确认，请先到协作群列表查看');
  }
}
