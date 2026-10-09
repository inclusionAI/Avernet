import type { MessageViewScope } from '@/domain/collaboration/types';
import type { CreateGroupBody } from '@/services/backendApi/collaboration/collaborationGroupController';

export interface CreateGroupInput {
  name: string;
  strategy: 'chat' | 'manager_worker' | 'state_machine';
  deliveryPolicy?: 'send_to_driver' | 'inject_observers';
  definitionYaml?: string;
  driverBotUuid: string;
  /** 具体 Human/Bot actor id；合成「我」与空值被省略（后端回落 Human Principal）。 */
  originator: string;
  participants: Array<{ actor_id: string; message_view_scope?: MessageViewScope }>;
  context?: string;
  participantBindings?: Array<{ binding: string; actor_ids: string[] }>;
}

/**
 * Human 共同建群的 originator 归一：只有拿到具体身份 id（mine 的 human actor id、
 * 或选中的 Bot id）才下发；'me'/空值一律省略，不伪造 originator。
 * 可见性保持缺省：BCS 建群默认 private Group，body 不携带 visibility。
 */
export function normalizeGroupOriginator(originator: string): string | undefined {
  const trimmed = typeof originator === 'string' ? originator.trim() : '';
  return trimmed && trimmed !== 'me' ? trimmed : undefined;
}

export function buildCreateGroupBody(input: CreateGroupInput): CreateGroupBody {
  const actorIds = Array.from(new Set(input.participants.map((participant) => participant.actor_id)));
  const participants = input.participants.map((participant) => ({
    actor_id: participant.actor_id,
    role:
      participant.actor_id === input.driverBotUuid
        ? input.strategy === 'manager_worker'
          ? ('manager' as const)
          : ('driver' as const)
        : input.strategy === 'chat'
        ? ('consultant' as const)
        : ('worker' as const),
    ...(participant.message_view_scope ? { message_view_scope: participant.message_view_scope } : {}),
  }));
  const base = {
    group_kind: 'normal' as const,
    name: input.name,
    context: input.context,
    participants,
    driver_bot_uuid: input.driverBotUuid,
    ...(normalizeGroupOriginator(input.originator)
      ? { originator: normalizeGroupOriginator(input.originator) }
      : {}),
  };
  if (input.strategy === 'chat') {
    return {
      ...base,
      collaboration: {
        strategy: 'chat',
        delivery_policy: { bot_final_delivery: input.deliveryPolicy ?? 'send_to_driver' },
      },
    };
  }
  if (input.strategy === 'manager_worker') {
    return { ...base, collaboration: { strategy: 'manager_worker' } };
  }
  // 后端 StateMachineConfiguration：definition 与 participant_bindings 是【兄弟】字段，
  // participant_bindings 不可嵌在 definition 内（后端 definition 仅认 content_yaml，否则 422
  // "unknown field participant_bindings, expected content_yaml"）。
  return {
    ...base,
    collaboration: {
      strategy: 'state_machine',
      definition: {
        content_yaml: input.definitionYaml ?? '',
      },
      participant_bindings: input.participantBindings?.length
        ? input.participantBindings
        : [{ binding: 'role-1', actor_ids: actorIds }],
    },
  };
}
