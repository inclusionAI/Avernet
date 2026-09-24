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
  /** 会话创建者 actor_id（GET sessions/{sid} 与 GET groups/{gid}/sessions 返回）。 */
  created_by?: string;
  /** 发起调用的主体标识。 */
  caller_principal?: string;
}

const STRATEGY_TO_KIND: Record<GroupStrategy, GroupKind> = {
  chat: 'free_chat',
  manager_worker: 'task_master_slave',
  state_machine: 'task_dag',
};

function safeString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function safeNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function isGroupStrategy(value: unknown): value is GroupStrategy {
  return value === 'chat' || value === 'manager_worker' || value === 'state_machine';
}

function isParticipantRole(value: unknown): value is string {
  return typeof value === 'string';
}

function isParticipantKind(value: unknown): value is 'human' | 'bot' {
  return value === 'human' || value === 'bot';
}

function isParticipantMode(value: unknown): value is 'auto' | 'muted' | 'present' | 'absent' {
  return value === 'auto' || value === 'muted' || value === 'present' || value === 'absent';
}

function isMessageViewScope(value: unknown): value is 'full' | 'participant' {
  return value === 'full' || value === 'participant';
}
const ROLE_NATIVE_TO_DOMAIN: Record<string, ParticipantRole> = {
  owner: 'owner',
  driver: 'driver',
  manager: 'manager',
  consultant: 'member',
  worker: 'worker',
  observer: 'member',
};

export function mapGroupListItem(dto: GroupListItemDto): GroupView {
  const strategy = isGroupStrategy(dto.strategy) ? dto.strategy : 'chat';
  const membership = dto.membership === 'direct' || dto.membership === 'session_only' ? dto.membership : undefined;
  return {
    groupId: safeString(dto.group_id),
    name: safeString(dto.name, '未命名群'),
    kind: STRATEGY_TO_KIND[strategy],
    status: dto.status === 'dissolved' ? 'dissolved' : 'active',
    participants: [],
    sessions: [],
    lastMessageAt: safeNumber(dto.updated_at),
    createdAt: safeNumber(dto.created_at),
    participantCount: safeNumber(dto.participant_count),
    driverBotUuid: safeString(dto.driver_bot_uuid),
    ...(membership ? { membership } : {}),
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
  message_view_scope?: 'full' | 'participant';
}): ParticipantView {
  const actorId = safeString(dto.actor_id);
  const participant: ParticipantView = {
    actorId,
    kind: isParticipantKind(dto.actor_kind) ? dto.actor_kind : 'bot',
    name: safeString(dto.name, actorId),
    role: ROLE_NATIVE_TO_DOMAIN[isParticipantRole(dto.role) ? dto.role : ''] ?? 'member',
    mode: isParticipantMode(dto.mode) ? dto.mode : 'auto',
  };
  if (isMessageViewScope(dto.message_view_scope)) participant.messageViewScope = dto.message_view_scope;
  return participant;
}

export function mapSessionListItem(dto: GroupSessionDto): SessionView {
  const participants = Array.isArray(dto.participants) ? dto.participants : [];
  const status = dto.status === 'completed' ? 'completed' : 'running';
  return {
    sessionId: safeString(dto.session_id),
    groupId: safeString(dto.group_id),
    title: safeString(dto.title, '未命名会话'),
    kind: dto.kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status,
    participants: participants.map(mapParticipant),
    ...(typeof dto.participant_count === 'number' && Number.isFinite(dto.participant_count)
      ? { participantCount: dto.participant_count }
      : {}),
    lastMessageAt: safeNumber(dto.updated_at),
    createdAt: safeNumber(dto.created_at),
    favorite: dto.collected === true,
    ...(typeof dto.created_by === 'string' ? { createdBy: dto.created_by } : {}),
    ...(typeof dto.caller_principal === 'string' ? { callerPrincipal: dto.caller_principal } : {}),
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
  created_by?: string;
  caller_principal?: string;
}

/** BCS raw 会话项 → SessionView 兜底映射（execute 建群链路专用，不碰预发 mapSessionListItem）。
 *  BCS 用 session_id/id、session_title、session_kind、bot_uuid/bot_name，按实际结构兜底取值。 */
export function mapBcsSessionItem(raw: BcsSessionRaw, fallbackGroupId: string): SessionView {
  const participants = Array.isArray(raw.participants) ? raw.participants : [];
  const sessionId = safeString(raw.session_id, safeString(raw.id));
  const groupId = safeString(raw.group_id, fallbackGroupId);
  return {
    sessionId,
    groupId,
    title: safeString(raw.session_title, safeString(raw.title, '未命名会话')),
    kind: raw.session_kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status: raw.status === 'completed' ? 'completed' : 'running',
    participants: participants.map((p) =>
      mapParticipant({
        actor_id: safeString(p.bot_uuid, safeString(p.actor_id, safeString(p.bot_id))),
        actor_kind: isParticipantKind(p.actor_kind) ? p.actor_kind : 'bot',
        name: safeString(p.bot_name, safeString(p.name, safeString(p.bot_uuid, safeString(p.actor_id)))),
        role: safeString(p.role, 'observer'),
        mode: isParticipantMode(p.mode) ? p.mode : 'auto',
      }),
    ),
    ...(typeof raw.participant_count === 'number' && Number.isFinite(raw.participant_count)
      ? { participantCount: raw.participant_count }
      : {}),
    lastMessageAt: safeNumber(raw.updated_at),
    createdAt: safeNumber(raw.created_at),
    favorite: raw.collected === true,
    ...(typeof raw.created_by === 'string' ? { createdBy: raw.created_by } : {}),
    ...(typeof raw.caller_principal === 'string' ? { callerPrincipal: raw.caller_principal } : {}),
  };
}
