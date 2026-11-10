import { isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import {
  acceptFriendConnectionRequest,
  cancelFriendConnectionRequest,
  listFriendConnectionRequests,
  rejectFriendConnectionRequest,
  type FriendConnectionActor,
  type FriendConnectionRequestDto,
  type FriendConnectionRequestStatus,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { CollaborationSquareError } from './collaborationSquareError';

/** 发现页「好友申请」每向列表的页大小（BCS page_size）。 */
export const FRIEND_CONNECTION_REQUESTS_PAGE_SIZE = 20;

export type FriendConnectionDirection = 'sent' | 'received';

/** 所选角色视角下的好友申请展示记录（BCS friend-connections 申请 DTO 的安全映射）。 */
export interface FriendConnectionRequestRecord {
  requestId: string;
  fromActorId: string;
  toActorId: string;
  status: FriendConnectionRequestStatus;
  message?: string;
}

export type FriendConnectionOperation = 'accept' | 'reject' | 'cancel';

const isFriendConnectionActor = (actor: unknown): actor is FriendConnectionActor => {
  if (typeof actor !== 'object' || actor === null) return false;
  const { type, id } = actor as { type?: unknown; id?: unknown };
  return (type === 'human' || type === 'bot') && typeof id === 'string';
};

/** 校验并映射 BCS friend-connections 申请 DTO；畸形记录按协议错误上抛，不伪装为空列表。 */
function mapFriendConnectionRequestDto(dto: FriendConnectionRequestDto): FriendConnectionRequestRecord {
  const statuses: FriendConnectionRequestStatus[] = ['pending', 'approved', 'rejected', 'cancelled'];
  if (
    !dto ||
    typeof dto.request_id !== 'string' ||
    !dto.request_id.trim() ||
    !isFriendConnectionActor(dto.from_actor) ||
    !isFriendConnectionActor(dto.to_actor) ||
    !statuses.includes(dto.status)
  ) {
    throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的记录');
  }
  return {
    requestId: dto.request_id.trim(),
    fromActorId: dto.from_actor.id,
    toActorId: dto.to_actor.id,
    status: dto.status,
    ...(dto.message?.trim() ? { message: dto.message.trim() } : {}),
  };
}

/** 好友连接接口简化信封校验：登录重定向与业务码失败都按原语义抛 CollaborationSquareError。 */
function assertSuccessEnvelope(response: unknown, failureMessage: string): void {
  if (isAceLoginResponse(response))
    throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
  const code = (response as { code?: unknown } | null | undefined)?.code;
  if (code !== 20000) throw new CollaborationSquareError('protocol_error', failureMessage);
}

/**
 * 所选角色的 pending 好友申请分页读取（direction=sent|received，status=pending）。
 * 复用现有 BCS friend-connections Controller；页游标由调用方持有。
 */
export async function listPendingFriendConnectionRequests(
  direction: FriendConnectionDirection,
  actor: { type: 'human' | 'bot'; id: string },
  page: number,
  signal?: AbortSignal,
): Promise<{ items: FriendConnectionRequestRecord[]; total: number }> {
  const response = await listFriendConnectionRequests(
    {
      actor_type: actor.type,
      actor_id: actor.id,
      direction,
      status: 'pending',
      page,
      page_size: FRIEND_CONNECTION_REQUESTS_PAGE_SIZE,
    },
    signal,
  );
  assertSuccessEnvelope(response, '好友申请接口返回了无法识别的业务码');
  const items = (response as { data?: { items?: unknown } }).data?.items;
  if (!Array.isArray(items)) throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的数据');
  const mapped = (items as FriendConnectionRequestDto[]).map(mapFriendConnectionRequestDto);
  const total = (response as { data?: { total?: unknown } }).data?.total;
  return { items: mapped, total: typeof total === 'number' ? total : mapped.length };
}

/** 好友申请写操作（accept/reject/cancel）：返回处理后的申请记录；撤销等失败按错误上抛。 */
export async function resolveFriendConnectionRequest(
  operation: FriendConnectionOperation,
  requestId: string,
  signal?: AbortSignal,
): Promise<FriendConnectionRequestRecord> {
  const response =
    operation === 'accept'
      ? await acceptFriendConnectionRequest(requestId, signal)
      : operation === 'reject'
      ? await rejectFriendConnectionRequest(requestId, signal)
      : await cancelFriendConnectionRequest(requestId, signal);
  assertSuccessEnvelope(response, '好友申请处理接口返回了无法识别的业务码');
  const data = (response as { data?: FriendConnectionRequestDto }).data;
  if (!data) throw new CollaborationSquareError('protocol_error', '好友申请处理接口返回了无法识别的数据');
  return mapFriendConnectionRequestDto(data);
}
