import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import type { FriendRequestDirection, FriendRequestItem, FriendRequestParty } from '@/domain/collaborationSquare/friendRequests';
import { sameFriendActor } from '@/domain/collaborationSquare/friendRequests';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import type { CollaborationBotDto } from '@/services/backendApi/collaboration/collaborationBotController';
import type { FriendConnectionRequestDto } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { CollaborationSquareError } from './collaborationSquareError';

const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object';
const nonEmpty = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
function isActor(value: unknown): value is FriendRequestActor {
  return record(value) && (value.type === 'human' || value.type === 'bot') && nonEmpty(value.id);
}
export function invalidRequests(): never {
  throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的数据，请刷新重试');
}
export function requestData(response: unknown): unknown {
  if (isAceLoginResponse(response)) throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录');
  if (record(response) && [40100, 401000].includes(Number(response.code))) {
    throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录');
  }
  if (record(response) && [40300, 403000].includes(Number(response.code))) {
    throw new CollaborationSquareError('forbidden', '无权访问当前身份的好友申请或资料');
  }
  if (!record(response) || response.code !== 20000 || response.success === false) return invalidRequests();
  return response.data;
}
export function parseFriendRequest(value: unknown): FriendConnectionRequestDto {
  if (!record(value) || !nonEmpty(value.request_id) || !isActor(value.from_actor) || !isActor(value.to_actor) ||
      !isActor(value.created_by) || !['pending', 'approved', 'rejected', 'cancelled'].includes(String(value.status)) ||
      (value.message !== undefined && typeof value.message !== 'string') ||
      (value.decision_reason !== undefined && typeof value.decision_reason !== 'string') ||
      (value.decided_at !== undefined && (!Number.isSafeInteger(value.decided_at) || Number(value.decided_at) < 0))) {
    return invalidRequests();
  }
  return value as unknown as FriendConnectionRequestDto;
}
export function parseRequestPage(response: unknown, actor: FriendRequestActor, direction: FriendRequestDirection, page: number) {
  const data = requestData(response);
  if (!record(data) || !Array.isArray(data.items) || !Number.isSafeInteger(data.total) || Number(data.total) < 0 ||
      data.page !== page || data.page_size !== 20 || data.items.length > 20 || Number(data.total) < data.items.length) return invalidRequests();
  const items = data.items.map(parseFriendRequest);
  if (new Set(items.map((item) => item.request_id)).size !== items.length || items.some((item) =>
    item.status !== 'pending' || !sameFriendActor(direction === 'sent' ? item.from_actor : item.to_actor, actor),
  )) return invalidRequests();
  return { items, total: Number(data.total), page, pageSize: 20 };
}
function mapParty(actor: FriendRequestActor, bots: Map<string, CollaborationBotDto>): FriendRequestParty {
  const bot = actor.type === 'bot' ? bots.get(actor.id) : undefined;
  return {
    ...actor, name: bot?.name?.trim() || actor.id,
    ...(actor.type === 'bot' ? { avatarUrl: resolveBotAvatarUrl(bot?.avatar_url, actor.id) } : {}),
    ...(bot?.status ? { online: bot.status === 'online' && bot.reachability !== 'unreachable' } : {}),
  };
}
export function mapFriendRequest(dto: FriendConnectionRequestDto, bots = new Map<string, CollaborationBotDto>()): FriendRequestItem {
  return { requestId: dto.request_id, fromActor: mapParty(dto.from_actor, bots), toActor: mapParty(dto.to_actor, bots),
    status: dto.status, message: dto.message, decisionReason: dto.decision_reason, decidedAt: dto.decided_at };
}
