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

function renderUserAvatar(name: string, avatarUrl?: string) {
  return <Avatar name={name} src={avatarUrl} size={32} />;
}

function renderAvatar(name: string, avatarUrl?: string, fallbackAvatar?: string) {
  if (avatarUrl) {
    return <img src={avatarUrl} alt={name} className="h-8 w-8 shrink-0 rounded-full object-cover" />;
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
      {fallbackAvatar || name.charAt(0)}
    </span>
  );
}

export function resolveSingleSender(
  message: ChatMessage,
  target: ConversationTarget,
  viewer?: IdentityView | null,
  userAvatarUrl?: string,
  authenticatedUserId?: string | null,
  authenticatedUserName?: string | null,
): { name: string; avatar: ReactNode } {
  if (message.role === 'assistant') {
    return { name: target.name || '未命名 Bot', avatar: renderAvatar(target.name || 'Bot', undefined, target.avatar) };
  }
  const senderId = typeof message.extra?.senderId === 'string' ? message.extra.senderId : undefined;
  const senderName = typeof message.extra?.senderName === 'string' ? message.extra.senderName.trim() : '';
  const isCurrentUser = !senderId || isSameHumanIdentity(senderId, authenticatedUserId, 'human');
  const name = isCurrentUser
    ? authenticatedUserName?.trim() || (viewer?.kind === 'user' ? viewer.displayName : '') || senderName || '未命名成员'
    : senderName || senderId || '未命名成员';
  return { name, avatar: renderUserAvatar(name, userAvatarUrl ?? viewer?.avatarUrl) };
}
