import type {
  CodingBotSessionArchiveItem,
  CodingBotSessionArchiveRestoreStatus,
} from '@/domain/conversation/types';
import { formatRelativeTime } from '@/utils/format';

export const ARCHIVE_RESTORE_LIMIT = 100;

const STATUS_TEXT: Record<CodingBotSessionArchiveRestoreStatus, string> = {
  archived: '可恢复',
  queued: '等待恢复',
  restoring: '恢复中',
  succeeded: '已恢复',
  failed: '恢复失败',
};

export function archiveTitle(item: CodingBotSessionArchiveItem): string {
  return (
    item.title?.trim() ||
    `未命名会话 · ${item.sessionKey.match(/:session:([^:]+)/)?.[1]?.slice(0, 8) || item.archiveId.slice(-8)}`
  );
}

export function activeTime(item: CodingBotSessionArchiveItem): string {
  const value = [item.lastActiveAt, item.updatedAt, item.createdAt].find(Boolean) || null;
  if (!value) return '时间未知';
  return formatRelativeTime(value);
}

export function archiveMessageCountLabel(item: CodingBotSessionArchiveItem): string {
  const count = item.messageCount;
  return typeof count === 'number' && Number.isSafeInteger(count) && count >= 0
    ? `消息 ${count.toLocaleString('zh-CN')} 条`
    : '消息条数未知';
}

export function rowMeta(item: CodingBotSessionArchiveItem): string {
  if (item.restoreStatus === 'queued') return STATUS_TEXT.queued;
  if (item.restoreStatus === 'restoring') return STATUS_TEXT.restoring;
  if (item.restoreStatus === 'failed') return STATUS_TEXT.failed;
  return activeTime(item);
}

export function formatMessageTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
