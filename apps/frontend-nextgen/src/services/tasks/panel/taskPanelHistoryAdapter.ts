import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import type { SessionMessageData } from '@/services/backendApi/collaboration/sessionController';
import { isEnvelopeSuccessAnyDialect, type BackendApiEnvelope } from '@/services/backendApi/types';
import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { HistoryReader } from '@avernet-assets/task-panel';
/** Own cursor: never advances the chat provider's visible-message paginator. */
export function createBotTaskHistoryReader(bot: ChatBotView, userId: string): HistoryReader {
  return { async readPage(conversation, cursor, signal) {
    signal.throwIfAborted();
    const page = cursor === null ? 1 : Number(cursor);
    if (!Number.isInteger(page) || page < 1) throw new Error('无效的会话历史游标。');
    const response = await botSessionService.listMessagesPage(bot, userId, conversation.sessionId, page, 50);
    signal.throwIfAborted();
    return { messages: response.messages.map(m => ({ id: m.id, content: typeof m.content === 'string' ? m.content : '', timestamp: Number(m.createdAt) || 0 })),
      nextCursor: page * 50 < response.total && response.rawCount > 0 ? String(page + 1) : null };
  } };
}
export function createGroupTaskHistoryReader(viewIdentity: string): HistoryReader {
  return { async readPage(conversation, cursor, signal) {
    signal.throwIfAborted();
    const response = await backendRequest<BackendApiEnvelope<SessionMessageData[]>>(
      `/openapi/v1/collaboration/sessions/${encodeURIComponent(conversation.sessionId)}/messages`, {
        method: 'GET', injectUserId: false, signal, operation: 'task-panel-history-recovery',
        params: { limit: 50, ...(cursor ? { before: cursor } : {}), view_bot_id: viewIdentity, include_pending: false },
      },
    ).catch(error => {
      // Supplemental recovery owns its error UI; leave authentication handling intact.
      if (error instanceof BackendRequestError && error.status !== 401 && error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
      throw error;
    });
    signal.throwIfAborted();
    if (!isEnvelopeSuccessAnyDialect(response)) throw new Error(response?.message || '任务历史读取失败。');
    const rows = response.data ?? [];
    const oldest = rows.length ? Math.min(...rows.map(m => m.timestamp).filter(t => Number.isFinite(t) && t > 0)) : NaN;
    if (rows.length >= 50 && !Number.isFinite(oldest)) throw new Error('历史消息缺少分页时间戳，无法继续查找。');
    return { messages: rows.map(m => ({ id: m.id, content: m.content, timestamp: m.timestamp })),
      nextCursor: rows.length >= 50 ? String(oldest) : null };
  } };
}
