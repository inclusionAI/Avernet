import type { HumanBotActionContext } from '@/domain/collaborationSquare/types';
import {
  listFriendConnectionRequests,
  listFriendConnections,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { CollaborationSquareError } from './collaborationSquareError';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isAceLoginResponse(value: unknown) {
  return isRecord(value) && value.actionType === 'LOGIN' && value.buserviceErrorCode === 'USER_NOT_LOGIN';
}

function getBotActorId(
  fromActor: { type: string; id: string },
  toActor: { type: string; id: string },
  humanId: string,
) {
  const actors = [fromActor, toActor];
  if (actors.some((actor) => !actor || typeof actor.id !== 'string' || !actor.id)) {
    throw new CollaborationSquareError('protocol_error', '好友关系接口返回了无法识别的参与方');
  }
  if (!actors.some((actor) => actor.type === 'human' && actor.id === humanId)) {
    throw new CollaborationSquareError('protocol_error', '好友关系接口返回了无法识别的 Human 参与方');
  }
  const botActors = actors.filter((actor) => actor.type === 'bot');
  if (botActors.length !== 1)
    throw new CollaborationSquareError('protocol_error', '好友关系接口返回了无法识别的 Bot 参与方');
  return botActors[0].id;
}

export async function listFriendBotRelationships(context: HumanBotActionContext, signal?: AbortSignal) {
  const friendIds = new Set<string>();
  const applyingIds = new Set<string>();
  const connectionsResponse = await listFriendConnections(
    {
      actor_type: 'human',
      actor_id: context.userId,
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
    if (!connection || (connection.status !== 'approved' && connection.status !== 'public_no_edge')) {
      throw new CollaborationSquareError('protocol_error', '好友连接接口返回了无法识别的状态');
    }
    if (connection.status === 'approved') {
      friendIds.add(getBotActorId(connection.from_actor, connection.to_actor, context.userId));
    }
  }

  const pageSize = 100;
  let page = 1;
  while (true) {
    const requestsResponse = await listFriendConnectionRequests(
      {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: context.userId,
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
      applyingIds.add(getBotActorId(request.from_actor, request.to_actor, context.userId));
    }
    const total = requestsResponse.data?.total;
    if (requests.length === 0 || (typeof total === 'number' ? page * pageSize >= total : requests.length < pageSize))
      break;
    page += 1;
  }
  return { friendIds, applyingIds };
}
