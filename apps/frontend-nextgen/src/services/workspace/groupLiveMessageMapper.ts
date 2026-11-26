import type { ChatMessage } from '@tc-chat/core';

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

/**
 * SDK 2.1 实时消息保留 BCS payload.message.from，却未提供 UI 使用的 senderId，
 * 头像也叫 senderAvatar 而非 senderAvatarUrl。在接收边界补齐，与历史/本地回显一致。
 * 只使用明确的发送者字段；botUuid 可能是路由目标，不能据此猜测 Human 作者。
 */
export function normalizeGroupLiveMessage(message: ChatMessage): ChatMessage {
  const extra = message.extra;
  if (!extra) return message;
  const sender = record(record(extra.payload)?.message);
  const senderId = text(extra.senderId) ?? text(sender?.from);
  const senderName = text(extra.senderName) ?? text(sender?.from_name);
  const senderAvatarUrl = text(extra.senderAvatarUrl) ?? text(extra.senderAvatar) ?? text(sender?.from_avatar);
  if (!senderId && !senderName && !senderAvatarUrl) return message;
  return {
    ...message,
    extra: {
      ...extra,
      ...(senderId ? { senderId } : {}),
      ...(senderName ? { senderName } : {}),
      ...(senderAvatarUrl ? { senderAvatarUrl } : {}),
    },
  };
}
