import type {
  BotSearchMode,
  PublicBot,
  PublicBotProfile,
  PublicGroup,
  PublicGroupMember,
  SquareDeepLink,
  SquareResource,
} from './types';

export interface PublicBotTransport {
  bot_id: string;
  bot_uuid?: string;
  bot_name: string;
  owner_name: string;
  description?: string;
  capabilities?: string[];
  relationship_status?: string;
}
export interface BotProfileTransport {
  bot_id: string;
  bot_uuid?: string;
  bot_name: string;
  owner_name: string;
  description?: string;
  engine_name?: string;
  capabilities?: Array<{ capability_id: string; display_name: string }>;
  [key: string]: unknown;
}
export interface PublicGroupTransport {
  group_id: string;
  group_name: string;
  owner_bot_name?: string;
  owner_user_name?: string;
  group_type_label?: string;
  member_count?: number;
  goal?: string;
  member_list_visibility?: string;
  can_create_session?: boolean;
}
export interface PublicGroupMemberTransport {
  member_id: string;
  display_name: string;
  member_type: string;
  group_role: string;
  [key: string]: unknown;
}

/**
 * Gateway collaboration/catalog DTO 的最小交集。
 * 这里不依赖 backendApi controller，避免 domain 层反向依赖 transport 层。
 */
export interface CollaborationBotTransport {
  bot_id?: string;
  bot_uuid?: string;
  name?: string;
  bot_name?: string;
  summary?: string;
  description?: string;
  owner_name?: string;
  owner?: { name?: string };
  relationship_status?: string;
  descriptor?: {
    skills?: Array<{ name?: string; description?: string }>;
    summary?: string;
  };
}

export interface PublicBotCatalogTransport {
  bot_id?: string;
  bot_uuid?: string;
  entity_id?: string;
  description?: string;
  name?: string;
  owner_name?: string;
  is_friend?: boolean;
  /** 智能搜索 Discovery 返回的推荐理由；short_profile 用于卡片「Profile」行。 */
  recommendation?: { short_profile?: string };
}

export interface CollaborationGroupDetailTransport {
  group_id: string;
  status?: string;
  originator_actor_id?: string;
  driver_bot_uuid?: string;
  name?: string;
  group_name?: string;
  owner_bot_name?: string;
  owner_user_name?: string;
  group_type_label?: string;
  member_count?: number;
  participant_count?: number;
  participants?: Array<{
    actor_id?: string;
    actor_kind?: string;
    name?: string;
    role?: string;
    display_name?: string;
    member_id?: string;
    member_type?: string;
    group_role?: string;
  }>;
  goal?: string;
  context?: string;
  visibility?: string;
  group_kind?: string;
  kind?: string;
  member_list_visibility?: string;
  count_only?: boolean;
  can_create_session?: boolean;
  /** 协作策略：chat / manager_worker / state_machine。公开群目录响应为顶层字段，兼容历史嵌套形态。 */
  strategy?: string;
  collaboration?: { strategy?: string };
}

function normalized(value: string) {
  return value.trim().toLocaleLowerCase();
}

function nonEmpty(...values: Array<string | undefined>) {
  return values.find((value) => value?.trim())?.trim() ?? '';
}

function mapRelationshipStatus(value?: string): PublicBot['relationshipStatus'] {
  return value === 'applying' || value === 'pending'
    ? 'applying'
    : value === 'friend' || value === 'accepted'
    ? 'friend'
    : 'none';
}

/** Map the confirmed public/catalog Bot DTOs into the page domain model. */
export function mapCollaborationBotDto(value: CollaborationBotTransport): PublicBot {
  return {
    id: nonEmpty(value.bot_uuid, value.bot_id),
    name: nonEmpty(value.name, value.bot_name) || '未命名 Bot',
    ownerName: nonEmpty(value.owner_name, value.owner?.name) || '未公开',
    description: nonEmpty(value.description, value.summary),
    capabilities: value.descriptor?.skills?.flatMap((skill) => (skill.name?.trim() ? [skill.name.trim()] : [])) ?? [],
    relationshipStatus: mapRelationshipStatus(value.relationship_status),
  };
}

/** Resolve the Bot actor id expected by the friend-connection endpoint. */
export function resolveFriendRequestBotId(
  value: Pick<PublicBotCatalogTransport, 'bot_id' | 'bot_uuid' | 'entity_id'>,
): string {
  const explicitBotUuid = value.bot_uuid?.trim();
  if (explicitBotUuid) return explicitBotUuid;

  const botId = value.bot_id?.trim() ?? '';
  const entityId = value.entity_id?.trim() ?? '';
  return botId && entityId ? `${botId}:${entityId}` : botId;
}

/** Map only the fields confirmed by the Bot Catalog Search contract. */
export function mapPublicBotCatalogDto(value: PublicBotCatalogTransport, viewerUserId?: string): PublicBot | null {
  const id = value.bot_uuid?.trim() || value.bot_id?.trim() || '';
  if (!id) return null;
  const friendRequestBotId = resolveFriendRequestBotId(value);
  const shortProfile = value.recommendation?.short_profile?.trim() || undefined;
  const isOwnedByLoggedInUser = Boolean(viewerUserId?.trim() && value.entity_id?.trim() === viewerUserId.trim());
  return {
    id,
    name: value.name?.trim() || '未命名 Bot',
    ownerName: value.owner_name?.trim() || '未公开',
    description: value.description?.trim() ?? '',
    capabilities: [],
    relationshipStatus: value.is_friend === true ? 'friend' : 'none',
    ...(isOwnedByLoggedInUser ? { isOwnedByLoggedInUser: true } : {}),
    ...(friendRequestBotId && friendRequestBotId !== id ? { friendRequestBotId } : {}),
    ...(shortProfile ? { shortProfile } : {}),
  };
}

/** Map only public normal groups and derive card labels from confirmed public fields. */
export function mapPublicGroupCatalogDto(value: CollaborationGroupDetailTransport): PublicGroup | null {
  const id = value.group_id?.trim() ?? '';
  const kind = value.kind ?? value.group_kind;
  if (!id || value.visibility !== 'public' || kind !== 'normal') return null;
  const participants = Array.isArray(value.participants) ? value.participants : [];
  const ownerBot = participants.find((item) => item.actor_kind === 'bot' && item.actor_id === value.driver_bot_uuid);
  const ownerUser = participants.find(
    (item) => item.actor_kind === 'human' && item.actor_id === value.originator_actor_id,
  );
  const strategy = value.strategy ?? value.collaboration?.strategy;
  const typeLabel =
    strategy === 'manager_worker' ? '任务协作' : strategy === 'state_machine' ? '自定义协同' : '自由聊天';
  const memberCount = value.participant_count ?? value.member_count ?? participants.length;
  return {
    id,
    name: nonEmpty(value.name, value.group_name) || '未命名协作群',
    ownerBotName: nonEmpty(ownerBot?.name, value.owner_bot_name) || '未公开',
    ownerUserName: nonEmpty(ownerUser?.name, value.owner_user_name) || '未公开',
    driverBotUuid: value.driver_bot_uuid ?? '',
    typeLabel,
    memberCount: Number.isFinite(memberCount) ? Math.max(0, memberCount) : 0,
    goal: nonEmpty(value.goal, value.context),
    memberListVisibility: value.member_list_visibility === 'visible' ? 'visible' : 'count_only',
    canCreateSession: value.can_create_session !== false && value.status === 'active',
  };
}

/** Map a group detail/list DTO without exposing transport fields to the UI. */
export function mapGroupDetailTransport(value: CollaborationGroupDetailTransport): PublicGroup {
  const participants = Array.isArray(value.participants) ? value.participants : [];
  const memberCount = value.member_count ?? value.participant_count ?? participants.length;
  const explicitVisibility = value.member_list_visibility;
  const memberListVisibility =
    explicitVisibility === 'count_only' || value.count_only === true ? 'count_only' : 'visible';
  return {
    id: value.group_id,
    name: nonEmpty(value.name, value.group_name) || '未命名协作群',
    ownerBotName: value.owner_bot_name ?? '未公开',
    ownerUserName: value.owner_user_name ?? '未公开',
    typeLabel:
      value.group_type_label ?? (value.group_kind === 'normal' || value.kind === 'normal' ? '协作群' : '协作空间'),
    memberCount: Number.isFinite(memberCount) ? Math.max(0, memberCount) : 0,
    goal: nonEmpty(value.goal, value.context),
    memberListVisibility,
    canCreateSession: value.can_create_session !== false,
  };
}

/** Map participants returned by the group detail endpoint. Unknown actor kinds are ignored. */
export function mapGroupParticipants(
  value: CollaborationGroupDetailTransport | PublicGroupMemberTransport[],
): PublicGroupMember[] {
  const participants = Array.isArray(value) ? value : value.participants ?? [];
  return participants.flatMap((participant) => {
    const rawType = participant.member_type ?? participant.actor_kind;
    const type = rawType === 'human' || rawType === 'bot' ? rawType : undefined;
    if (!type) return [];
    const rawId = participant.member_id ?? participant.actor_id;
    const id = typeof rawId === 'string' ? rawId.trim() : '';
    if (!id) return [];
    const displayName =
      typeof participant.display_name === 'string'
        ? participant.display_name
        : typeof participant.name === 'string'
        ? participant.name
        : undefined;
    const role =
      typeof participant.group_role === 'string'
        ? participant.group_role
        : typeof participant.role === 'string'
        ? participant.role
        : undefined;
    return [
      {
        id,
        displayName: nonEmpty(displayName) || '未命名成员',
        type,
        role: nonEmpty(role) || '参与者',
      },
    ];
  });
}

export function mapBotTransport(value: PublicBotTransport): PublicBot {
  const status = value.relationship_status;
  return {
    id: value.bot_id,
    name: value.bot_name,
    ownerName: value.owner_name,
    description: value.description ?? '',
    capabilities: Array.isArray(value.capabilities) ? value.capabilities.filter(Boolean) : [],
    relationshipStatus: status === 'applying' || status === 'friend' ? status : 'none',
  };
}

export function mapBotProfileTransport(value: BotProfileTransport): PublicBotProfile {
  return {
    id: nonEmpty(value.bot_uuid, value.bot_id),
    name: value.bot_name,
    ownerName: value.owner_name,
    description: value.description ?? '',
    engine: value.engine_name,
    capabilities: Array.isArray(value.capabilities)
      ? value.capabilities
          .filter((item) => item?.capability_id && item?.display_name)
          .map((item) => ({ id: item.capability_id, name: item.display_name }))
      : [],
  };
}

/**
 * 分享深链只依赖公开 Catalog 摘要。Catalog 不提供能力 ID/引擎信息，不能伪造成完整画像。
 */
export function mapPublicBotCatalogSummaryToProfile(bot: PublicBot): PublicBotProfile {
  return {
    id: bot.id,
    name: bot.name,
    ownerName: bot.ownerName,
    description: bot.description,
    ...(bot.friendRequestBotId ? { friendRequestBotId: bot.friendRequestBotId } : {}),
    ...(bot.isOwnedByLoggedInUser ? { isOwnedByLoggedInUser: true } : {}),
    ...(bot.shortProfile ? { shortProfile: bot.shortProfile } : {}),
    capabilities: [],
  };
}

export function mapGroupTransport(value: PublicGroupTransport): PublicGroup {
  return {
    id: value.group_id,
    name: value.group_name,
    ownerBotName: value.owner_bot_name ?? '未公开',
    ownerUserName: value.owner_user_name ?? '未公开',
    typeLabel: value.group_type_label ?? '协作群',
    memberCount: Math.max(0, value.member_count ?? 0),
    goal: value.goal ?? '',
    memberListVisibility: value.member_list_visibility === 'count_only' ? 'count_only' : 'visible',
    canCreateSession: value.can_create_session !== false,
  };
}

export function mapGroupMembersTransport(values: PublicGroupMemberTransport[]): PublicGroupMember[] {
  return values.flatMap((value) =>
    value.member_type === 'human' || value.member_type === 'bot'
      ? [{ id: value.member_id, displayName: value.display_name, type: value.member_type, role: value.group_role }]
      : [],
  );
}

export function filterPublicBots(bots: PublicBot[], query: string, mode: BotSearchMode) {
  const keyword = normalized(query);
  if (!keyword) return bots;
  return bots.filter((bot) => {
    const fields = mode === 'name' ? [bot.name, bot.ownerName] : [bot.name, bot.description, ...bot.capabilities];
    return fields.some((field) => normalized(field).includes(keyword));
  });
}

export function filterPublicGroups<T extends Pick<PublicGroup, 'name'>>(groups: T[], query: string) {
  const keyword = normalized(query);
  return keyword ? groups.filter((group) => normalized(group.name).includes(keyword)) : groups;
}

export function parseSquareDeepLink(search: string, expected: SquareResource): SquareDeepLink | null {
  const params = new URLSearchParams(search);
  const resource = params.get('resource');
  const id = params.get('id')?.trim();
  const searchHint = params.get('name')?.trim();
  return resource === expected && id ? { resource, id, ...(searchHint ? { searchHint } : {}) } : null;
}
