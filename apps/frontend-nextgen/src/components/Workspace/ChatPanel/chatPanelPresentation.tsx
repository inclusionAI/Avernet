import { Avatar } from '@/components/ui';
import type { IdentityView } from '@/domain/collaboration';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { buildMessageBlocks } from '@/services/workspace/messageBlockBuilder';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { formatChatTime } from '@/utils/format';
import type { ChatMessage } from '@tc-chat/core';
import type { ReactNode } from 'react';

export function getMessageTime(message: ChatMessage) {
  const displayTime = message.extra?.displayTime;
  if (displayTime) return formatChatTime(displayTime);
  return formatChatTime(message.createdAt);
}

export function getMessageBlocks(message: ChatMessage) {
  return buildMessageBlocks(message);
}

// dmore 交付包复测（index.html + artboard-005/006/009/013 一致，2026-10-10）：消息区
// 用户与 Bot 头像均为 24px 圆、距内容列 12px（x=36）；旧 32px/无头像系早期误读。
function renderUserAvatar(name: string, avatarUrl?: string) {
  return <Avatar name={name} src={avatarUrl} size={24} />;
}

function renderBotAvatar(name: string, avatar?: string) {
  return <Avatar name={name} src={avatar} size={24} />;
}

export function resolveSingleSender(
  message: ChatMessage,
  target: ConversationTarget,
  viewer?: IdentityView | null,
  userAvatarUrl?: string,
  authenticatedUserId?: string | null,
  authenticatedUserName?: string | null,
): { name: string; avatar: ReactNode | null } {
  if (message.role === 'assistant') {
    const name = target.name || '未命名 Bot';
    return { name, avatar: renderBotAvatar(name, target.avatar) };
  }
  const senderId = typeof message.extra?.senderId === 'string' ? message.extra.senderId : undefined;
  const senderName = typeof message.extra?.senderName === 'string' ? message.extra.senderName.trim() : '';
  const isCurrentUser = !senderId || isSameHumanIdentity(senderId, authenticatedUserId, 'human');
  const name = isCurrentUser
    ? authenticatedUserName?.trim() || (viewer?.kind === 'user' ? viewer.displayName : '') || senderName || '未命名成员'
    : senderName || senderId || '未命名成员';
  return { name, avatar: renderUserAvatar(name, userAvatarUrl ?? viewer?.avatarUrl) };
}
