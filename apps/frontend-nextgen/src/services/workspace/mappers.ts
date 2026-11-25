import type {
  GroupKind,
  GroupView,
  HumanMentionNotifyMode,
  ParticipantMode,
  ParticipantRole,
  ParticipantSummaryView,
  ParticipantView,
  SessionKind,
  SessionView,
} from '@/domain/collaboration';
import { getParticipantDisplayCount } from '@/domain/collaboration/participantPresentation';
import type { GroupSummaryData } from '@/services/backendApi/collaboration/collaborationGroupController';
import type { ParticipantSummaryReadDto } from '@/services/backendApi/collaboration/participantTypes';
import type { SessionDetailData, SessionParticipantDto } from '@/services/backendApi/collaboration/sessionController';
import { normalizeAvatarUrl } from '@/utils/avatarSource';

type GroupStrategy = 'chat' | 'manager_worker' | 'state_machine';
export type GroupListItemDto = GroupSummaryData;
export interface GroupSessionDto {
  session_id: string;
  group_id: string;
  title?: string;
  kind?: SessionKind;
  status?: 'running' | 'completed';
  /** 会话成员总数（列表接口可能直接返回，替代 participants 列表）。 */
  participant_count?: number;
  /** 会话成员：列表接口下发轻量摘要，详情接口下发完整成员；缺省视为未知。 */
  participants?: Array<ParticipantSummaryReadDto | SessionParticipantDto>;
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

/** @Human 通知来源枚举守卫：旧版后端缺省或枚举漂移时整字段丢弃，不伪造默认值。 */
export function isHumanMentionNotifyMode(value: unknown): value is HumanMentionNotifyMode {
  return value === 'all' || value === 'driver_bot_only' || value === 'none';
}
const ROLE_NATIVE_TO_DOMAIN: Record<string, ParticipantRole> = {
  owner: 'owner',
  driver: 'driver',
  manager: 'manager',
  consultant: 'member',
  worker: 'worker',
  observer: 'member',
};

export function mapParticipantSummary(dto: {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name?: string | null;
  avatar_url?: string | null;
}): ParticipantSummaryView {
  return {
    actorId: dto.actor_id,
    kind: dto.actor_kind,
    name: dto.name?.trim() || dto.actor_id,
    avatarUrl: normalizeAvatarUrl(dto.avatar_url),
  };
}

/** 完整成员 → 轻量摘要（详情映射后派生，不额外请求）。 */
export function toParticipantSummary(p: ParticipantView): ParticipantSummaryView {
  return { actorId: p.actorId, kind: p.kind, name: p.name, avatarUrl: p.avatarUrl };
}

export function mapGroupListItem(dto: GroupListItemDto): GroupView {
  const strategy = isGroupStrategy(dto.strategy) ? dto.strategy : 'chat';
  const membership = dto.membership === 'direct' || dto.membership === 'session_only' ? dto.membership : undefined;
  return {
    groupId: safeString(dto.group_id),
    name: safeString(dto.name, '未命名群'),
    kind: STRATEGY_TO_KIND[strategy],
    status: dto.status === 'dissolved' ? 'dissolved' : 'active',
    participants: [],
    participantDetailsLoaded: false,
    ...(Array.isArray(dto.participants) ? { participantSummaries: dto.participants.map(mapParticipantSummary) } : {}),
    sessions: [],
    lastMessageAt: safeNumber(dto.updated_at),
    createdAt: safeNumber(dto.created_at),
    participantCount: getParticipantDisplayCount(
      dto.participant_count,
      Array.isArray(dto.participants) ? dto.participants.map(mapParticipantSummary) : [],
    ),
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
  avatar_url?: string | null;
}): ParticipantView {
  const actorId = safeString(dto.actor_id);
  const participant: ParticipantView = {
    actorId,
    kind: isParticipantKind(dto.actor_kind) ? dto.actor_kind : 'bot',
    name: typeof dto.name === 'string' && dto.name.trim() ? dto.name.trim() : actorId,
    avatarUrl: normalizeAvatarUrl(dto.avatar_url),
    role: ROLE_NATIVE_TO_DOMAIN[isParticipantRole(dto.role) ? dto.role : ''] ?? 'member',
    mode: isParticipantMode(dto.mode) ? dto.mode : 'auto',
  };
  if (isMessageViewScope(dto.message_view_scope)) participant.messageViewScope = dto.message_view_scope;
  return participant;
}

/** 会话列表映射：只保留轻量摘要，不写入完整成员/控制字段（详情由 mapSessionDetail 补齐）。 */
export function mapSessionListItem(dto: GroupSessionDto): SessionView {
  const status = dto.status === 'completed' ? 'completed' : 'running';
  return {
    sessionId: safeString(dto.session_id),
    groupId: safeString(dto.group_id),
    title: safeString(dto.title, '未命名会话'),
    kind: dto.kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status,
    participants: [],
    participantDetailsLoaded: false,
    ...(Array.isArray(dto.participants) ? { participantSummaries: dto.participants.map(mapParticipantSummary) } : {}),
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

/** 会话详情映射：完整成员 + 由成员派生的摘要，并显式标记详情已加载（含空详情）。 */
export function mapSessionDetail(dto: SessionDetailData): SessionView {
  const participants = dto.participants.map(mapParticipant);
  return {
    ...mapSessionListItem(dto),
    participants,
    participantSummaries: participants.map(toParticipantSummary),
    participantDetailsLoaded: true,
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
  /** 成员头像；历史 BCS raw 可能缺省。 */
  avatar_url?: string | null;
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
  const rawRows = Array.isArray(raw.participants) ? raw.participants : [];
  const identityRows = rawRows.map((p) => ({
    actor_id: safeString(p.bot_uuid, safeString(p.actor_id, safeString(p.bot_id))),
    actor_kind: isParticipantKind(p.actor_kind) ? p.actor_kind : ('bot' as const),
    name: safeString(p.bot_name, safeString(p.name, safeString(p.bot_uuid, safeString(p.actor_id)))),
    avatar_url: p.avatar_url,
  }));
  // 只有每一行都携带合法 role/mode 时才视为完整控制信息；V1 轻量行不伪造 observer/auto。
  const detailRows = rawRows.filter(
    (p): p is BcsParticipantRaw & { role: string; mode: ParticipantMode } =>
      typeof p.role === 'string' &&
      Object.prototype.hasOwnProperty.call(ROLE_NATIVE_TO_DOMAIN, p.role) &&
      isParticipantMode(p.mode),
  );
  const hasDetail = rawRows.length > 0 && detailRows.length === rawRows.length;
  const sessionId = safeString(raw.session_id, safeString(raw.id));
  const groupId = safeString(raw.group_id, fallbackGroupId);
  return {
    sessionId,
    groupId,
    title: safeString(raw.session_title, safeString(raw.title, '未命名会话')),
    kind: raw.session_kind === 'service_invocation' ? 'service_invocation' : 'chat',
    status: raw.status === 'completed' ? 'completed' : 'running',
    participants: hasDetail
      ? detailRows.map((p, index) =>
          mapParticipant({
            ...identityRows[index],
            role: p.role,
            mode: p.mode,
          }),
        )
      : [],
    participantDetailsLoaded: hasDetail,
    ...(Array.isArray(raw.participants)
      ? { participantSummaries: identityRows.map(mapParticipantSummary) }
      : {}),
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
