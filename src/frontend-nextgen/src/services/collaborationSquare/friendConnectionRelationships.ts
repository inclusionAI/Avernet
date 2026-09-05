import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import { isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import {
  listFriendConnectionRequests,
  listFriendConnections,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { CollaborationSquareError } from './collaborationSquareError';

/**
 * 取好友关系/申请中「另一方」的 actor id（即非发起方 selfId 的那个参与方），
 * 用于回填 Bot 卡片的 friend/applying 状态。selfId 即当前查询的 actor id。
 */
function getOtherActorId(
  fromActor: { type: string; id: string },
  toActor: { type: string; id: string },
  selfId: string,
) {
  if (
    !fromActor ||
    !toActor ||
    typeof fromActor.id !== 'string' ||
    typeof toActor.id !== 'string' ||
    !fromActor.id ||
    !toActor.id
  ) {
    throw new CollaborationSquareError('protocol_error', '好友关系接口返回了无法识别的参与方');
  }
  if (fromActor.id === selfId) return toActor.id;
  if (toActor.id === selfId) return fromActor.id;
  throw new CollaborationSquareError('protocol_error', '好友关系接口返回了无法识别的参与方');
}

/** Search 已提供 approved 状态时，仅补读 pending 申请，避免重复查询已确认的好友连接。 */
export async function listPendingFriendBotRelationships(actor: FriendRequestActor, signal?: AbortSignal) {
  const applyingIds = new Set<string>();
  const pageSize = 100;
  let page = 1;
  while (true) {
    const requestsResponse = await listFriendConnectionRequests(
      {
        direction: 'sent',
        status: 'pending',
        actor_type: actor.type,
        actor_id: actor.id,
        page,
        page_size: pageSize,
      },
      signal,
    );
    if (isAceLoginResponse(requestsResponse))
      throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
    if (requestsResponse.code !== 20000)
      throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的业务码');
    const requests = requestsResponse.data?.items;
    if (!Array.isArray(requests))
      throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的数据');
    for (const request of requests) {
      if (!request || request.status !== 'pending') {
        throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的状态');
      }
      applyingIds.add(getOtherActorId(request.from_actor, request.to_actor, actor.id));
    }
    const total = requestsResponse.data?.total;
    if (requests.length === 0 || (typeof total === 'number' ? page * pageSize >= total : requests.length < pageSize))
      break;
    page += 1;
  }
  return applyingIds;
}

export async function listFriendBotRelationships(actor: FriendRequestActor, signal?: AbortSignal) {
  const friendIds = new Set<string>();
  const connectionsResponse = await listFriendConnections(
    {
      actor_type: actor.type,
      actor_id: actor.id,
    },
    signal,
  );
  if (isAceLoginResponse(connectionsResponse))
    throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
  if (connectionsResponse.code !== 20000)
    throw new CollaborationSquareError('protocol_error', '好友连接接口返回了无法识别的业务码');
  const connections = connectionsResponse.data?.items;
  if (!Array.isArray(connections))
    throw new CollaborationSquareError('protocol_error', '好友连接接口返回了无法识别的数据');
  for (const connection of connections) {
    if (!connection?.actor || typeof connection.actor.id !== 'string' || !connection.actor.id.trim()) {
      throw new CollaborationSquareError('protocol_error', '好友连接接口返回了无法识别的参与方');
    }
    // 已建立连接列表直接返回连接另一端 actor；仅回填 Bot，Human 关系不影响 Bot 卡片。
    if (connection.actor.type === 'bot') friendIds.add(connection.actor.id.trim());
  }

  const applyingIds = await listPendingFriendBotRelationships(actor, signal);
  return { friendIds, applyingIds };
}
