import type { FriendRequestDecision, FriendRequestPages, FriendRequestSnapshot } from '@/domain/collaborationSquare/friendRequests';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import { queryCollaborationBots, type CollaborationBotDto } from '@/services/backendApi/collaboration/collaborationBotController';
import { acceptFriendConnectionRequest, listFriendConnectionRequests, rejectFriendConnectionRequest } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { isAceLoginRedirectError } from './collaborationSquareApiHelpers';
import { invalidRequests, mapFriendRequest, parseFriendRequest, parseRequestPage, requestData } from './friendConnectionRequestMapper';

function assertNotAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError');
}
async function loadBotDetails(ids: string[], signal?: AbortSignal) {
  const bots = new Map<string, CollaborationBotDto>();
  if (ids.length === 0) return { bots };
  try {
    const data = requestData(await queryCollaborationBots({ bot_ids: ids }, signal)) as { items?: CollaborationBotDto[] } | undefined;
    assertNotAborted(signal);
    if (!Array.isArray(data?.items)) return invalidRequests();
    for (const bot of data.items) {
      if (!bot || typeof bot.bot_id !== 'string' || (bot.name !== undefined && typeof bot.name !== 'string') ||
          (bot.avatar_url !== undefined && bot.avatar_url !== null && typeof bot.avatar_url !== 'string')) return invalidRequests();
      if (bot.kind === 'bot' && ids.includes(bot.bot_id)) bots.set(bot.bot_id, bot);
    }
  } catch (error) {
    assertNotAborted(signal);
    const failure = error as { name?: string; status?: number; code?: string; toastKey?: string };
    if (failure.name === 'AbortError' || isAceLoginRedirectError(error) || failure.code === 'unauthenticated' || failure.code === 'forbidden' ||
        failure.status === 401 || failure.status === 403) throw error;
    // 可选展示资料失败不阻断审批；弹窗展示警告，取消 HTTP 层重复 toast。
    if (failure.toastKey) useErrorNotifyStore.getState().cancel(failure.toastKey);
  }
  return { bots, ...(bots.size < ids.length ? { detailsWarning: '部分 Bot 资料暂不可用，已显示 ID，可点击刷新重试。' } : {}) };
}

export const friendConnectionRequestService = {
  async load(actor: FriendRequestActor, pages: FriendRequestPages, signal?: AbortSignal): Promise<FriendRequestSnapshot> {
    assertNotAborted(signal);
    if (!actor.id.trim() || !['human', 'bot'].includes(actor.type) ||
        Object.values(pages).some((page) => !Number.isSafeInteger(page) || page < 1)) return invalidRequests();
    const [sent, received] = await Promise.all((['sent', 'received'] as const).map(async (direction) => {
      const response = await listFriendConnectionRequests({ direction, status: 'pending', actor_type: actor.type,
        actor_id: actor.id, page: pages[direction], page_size: 20 }, signal);
      return parseRequestPage(response, actor, direction, pages[direction]);
    }));
    assertNotAborted(signal);
    const ids = [...new Set([...sent.items, ...received.items].flatMap((item) => [item.from_actor, item.to_actor])
      .filter((item) => item.type === 'bot').map((item) => item.id))];
    const details = await loadBotDetails(ids, signal);
    assertNotAborted(signal);
    return { sent: { ...sent, items: sent.items.map((item) => mapFriendRequest(item, details.bots)) },
      received: { ...received, items: received.items.map((item) => mapFriendRequest(item, details.bots)) },
      detailsWarning: details.detailsWarning };
  },
  async decide(requestId: string, decision: FriendRequestDecision, signal?: AbortSignal) {
    assertNotAborted(signal);
    if (!requestId.trim() || !['accept', 'reject'].includes(decision)) return invalidRequests();
    const execute = decision === 'accept' ? acceptFriendConnectionRequest : rejectFriendConnectionRequest;
    const confirmed = parseFriendRequest(requestData(await execute(requestId, signal)));
    assertNotAborted(signal);
    if (confirmed.request_id !== requestId || confirmed.status !== (decision === 'accept' ? 'approved' : 'rejected')) return invalidRequests();
    return mapFriendRequest(confirmed);
  },
};
