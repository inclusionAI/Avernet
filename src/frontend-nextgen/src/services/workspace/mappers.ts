import type {
  GroupKind,
  GroupView,
  ParticipantRole,
  ParticipantView,
  SessionKind,
  SessionView,
} from '@/domain/collaboration';
import type { SessionDetailData } from '@/services/backendApi/collaboration/sessionController';

type GroupStrategy = 'chat' | 'manager_worker' | 'state_machine';
export interface GroupListItemDto {
  group_id: string;
  version?: number;
  kind: 'normal' | 'dm';
  status: 'active' | 'dissolved';
  visibility: 'private' | 'public';
  membership?: 'direct' | 'session_only';
  originator_actor_id: string;
  participant_count: number;
  driver_bot_uuid: string;
  strategy: GroupStrategy;
  name?: string;
  created_at: number;
  updated_at: number;
}
export interface GroupSessionDto {
  session_id: string;
  group_id: string;
  title?: string;
  kind?: SessionKind;
  status?: 'running' | 'completed';
  /** 会话成员总数（列表接口可能直接返回，替代 participants 列表）。 */
  participant_count?: number;
  /** 会话成员（列表/详情接口可能返回；缺省视为未知）。 */
  participants?: SessionDetailData['participants'];
  created_at: number;
  updated_at: number;
  collected?: boolean;
}

const STRATEGY_TO_KIND: Record<GroupStrategy, GroupKind> = {
  chat: 'free_chat',
  manager_worker: 'task_master_slave',
  state_machine: 'task_dag',
};
const ROLE_NATIVE_TO_DOMAIN: Record<string, ParticipantRole> = {
  owner: 'owner',
  driver: 'driver',
  manager: 'manager',
  consultant: 'member',
  worker: 'member',
  observer: 'member',
};

export function mapGroupListItem(dto: GroupListItemDto): GroupView {
  return {
    groupId: dto.group_id,
    name: dto.name ?? '未命名群',
    kind: STRATEGY_TO_KIND[dto.strategy],
    status: dto.status === 'dissolved' ? 'dissolved' : 'active',
    participants: [],
    sessions: [],
    lastMessageAt: dto.updated_at,
    createdAt: dto.created_at,
    participantCount: dto.participant_count,
    ...(dto.membership ? { membership: dto.membership } : {}),
    isPublic: dto.visibility === 'public',
    deliveryPolicy: 'send_to_driver',
  };
}

export function mapParticipant(dto: {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name?: string;
  role: string;
  mode: 'auto' | 'muted' | 'present' | 'absent';
}): ParticipantView {
  return {
    actorId: dto.actor_id,
    kind: dto.actor_kind,
    name: dto.name ?? dto.actor_id,
    role: ROLE_NATIVE_TO_DOMAIN[dto.role] ?? 'member',
    mode: dto.mode,
  };
}

export function mapSessionListItem(dto: GroupSessionDto): SessionView {
  return {
    sessionId: dto.session_id,
    groupId: dto.group_id,
    title: dto.title ?? '未命名会话',
    kind: dto.kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status: dto.status ?? 'running',
    participants: (dto.participants ?? []).map(mapParticipant),
    ...(dto.participant_count !== undefined ? { participantCount: dto.participant_count } : {}),
    lastMessageAt: dto.updated_at,
    createdAt: dto.created_at,
    favorite: dto.collected ?? false,
  };
}

/** BCS raw 会话参与者（execute 建群返回的本地 BCS 群）：bot_uuid/bot_name 对应预发的 actor_id/name。 */
export interface BcsParticipantRaw {
  actor_kind?: 'human' | 'bot';
  bot_uuid?: string;
  bot_id?: string;
  actor_id?: string;
  bot_name?: string;
  name?: string;
  role?: string;
  mode?: 'auto' | 'muted' | 'present' | 'absent';
  joined_at?: number;
}

/** BCS raw 会话项（/groups/{id}/sessions 返回的 items 元素）：session_id/id、session_title、session_kind。 */
export interface BcsSessionRaw {
  session_id?: string;
  id?: string;
  group_id?: string;
  session_title?: string;
  title?: string;
  session_kind?: string;
  status?: 'running' | 'completed';
  participant_count?: number;
  participants?: BcsParticipantRaw[];
  created_at?: number;
  updated_at?: number;
  collected?: boolean;
}

/** BCS raw 会话项 → SessionView 兜底映射（execute 建群链路专用，不碰预发 mapSessionListItem）。
 *  BCS 用 session_id/id、session_title、session_kind、bot_uuid/bot_name，按实际结构兜底取值。 */
export function mapBcsSessionItem(raw: BcsSessionRaw, fallbackGroupId: string): SessionView {
  return {
    sessionId: raw.session_id ?? raw.id ?? '',
    groupId: raw.group_id ?? fallbackGroupId,
    title: raw.session_title ?? raw.title ?? '未命名会话',
    kind: raw.session_kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status: raw.status ?? 'running',
    participants: (raw.participants ?? []).map((p) =>
      mapParticipant({
        actor_id: p.bot_uuid ?? p.actor_id ?? p.bot_id ?? '',
        actor_kind: p.actor_kind ?? 'bot',
        name: p.bot_name ?? p.name ?? p.bot_uuid ?? p.actor_id ?? '',
        role: p.role ?? 'observer',
        mode: p.mode ?? 'auto',
      }),
    ),
    ...(raw.participant_count !== undefined ? { participantCount: raw.participant_count } : {}),
    lastMessageAt: raw.updated_at ?? 0,
    createdAt: raw.created_at ?? 0,
    favorite: raw.collected ?? false,
  };
}
