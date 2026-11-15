import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
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
export function createGroupTaskHistoryReader(viewIdentity: string | null): HistoryReader {
  return { async readPage(conversation, cursor, signal) {
    signal.throwIfAborted();
    const response = await listSessionMessages(conversation.sessionId, {
      limit: 50, ...(cursor ? { before: cursor } : {}), ...(viewIdentity ? { view_bot_id: viewIdentity } : {}), include_pending: false,
    });
    signal.throwIfAborted();
    const rows = response.data ?? [];
    const oldest = rows.length ? Math.min(...rows.map(m => m.timestamp).filter(t => Number.isFinite(t) && t > 0)) : NaN;
    if (rows.length >= 50 && !Number.isFinite(oldest)) throw new Error('历史消息缺少分页时间戳，无法继续查找。');
    return { messages: rows.map(m => ({ id: m.id, content: m.content, timestamp: m.timestamp })),
      nextCursor: rows.length >= 50 ? String(oldest) : null };
  } };
}
