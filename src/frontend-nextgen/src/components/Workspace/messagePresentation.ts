import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import type { ChatMessage } from '@tc-chat/core';

/**
 * 仅用于消息区展示的发送者标识归一化：human_123 与 user_id=123 视为同一位用户。
 * 不修改消息 DTO 或领域模型。
 */
export function normalizeMessageHumanId(value?: string | null): string {
  return normalizeOpenApiUserId(value).trim();
}

/** 生成消息区分组键，供间距计算使用，不参与后端请求或持久化。 */
export function getMessageSenderKey(message: ChatMessage): string {
  const senderId = typeof message.extra?.senderId === 'string' ? message.extra.senderId : undefined;
  const senderName = typeof message.extra?.senderName === 'string' ? message.extra.senderName : undefined;
  const botUuid = typeof message.extra?.botUuid === 'string' ? message.extra.botUuid : undefined;
  const botName = typeof message.extra?.botName === 'string' ? message.extra.botName : undefined;

  if (message.role === 'user') {
    return `human:${normalizeMessageHumanId(senderId) || senderName || 'unknown'}`;
  }
  if (message.role === 'assistant') {
    return `bot:${botUuid || senderId || botName || 'unknown'}`;
  }
  return message.role;
}

/**
 * 间距落在当前消息之后：同一发送者 16px，发送者切换 24px，列表末尾不额外留白。
 * 这样可以覆盖 SDK 操作栏的悬浮区域，同时保持消息分组关系清晰。
 */
export function getMessageSpacingClass(messages: ChatMessage[], index: number): string {
  const nextMessage = messages[index + 1];
  if (!nextMessage) return 'mb-0';
  return getMessageSenderKey(messages[index]) === getMessageSenderKey(nextMessage) ? 'mb-4' : 'mb-6';
}
