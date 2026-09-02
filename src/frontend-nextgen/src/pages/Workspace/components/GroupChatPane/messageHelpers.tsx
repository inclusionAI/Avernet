import { Avatar } from '@/components/ui';
import { normalizeMessageHumanId } from '@/components/Workspace/messagePresentation';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { buildMessageBlocks } from '@/services/workspace/messageBlockBuilder';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { formatChatTime } from '@/utils/format';
import type { Block, ChatMessage } from '@tc-chat/core';
import type { ReactNode } from 'react';

/** 取消息渲染块：与单聊共用图片附件、失效图片和文本回退规则。 */
export function getMessageBlocks(message: ChatMessage, sessionId?: string): Block[] {
  return buildMessageBlocks(message, {
    resolveAttachmentUrl: (attachment) => {
      const attachmentId = attachment.attachment_id ?? attachment.attachmentId;
      return sessionId && typeof attachmentId === 'string' && attachmentId
        ? sessionFileService.buildContentUrl(sessionId, attachmentId)
        : undefined;
    },
  });
}

/** 取消息展示时间：优先 extra.displayTime，回退 createdAt（当天 HH:mm，非当天 MM-dd HH:mm）。 */
export function getMessageTime(message: ChatMessage): string | undefined {
  const displayTime = message.extra?.displayTime;
  if (displayTime) return formatChatTime(displayTime);
  return formatChatTime(message.createdAt);
}

/** 根据发送者身份生成统一的头像节点。 */
function renderCurrentUserAvatar(name: string, avatarUrl?: string): ReactNode {
  return <Avatar name={name} src={avatarUrl} size={32} />;
}

function renderHumanAvatar(name: string, avatarUrl?: string): ReactNode {
  return <Avatar name={name} src={avatarUrl} size={32} />;
}

function renderBotAvatar(name: string, avatarUrl?: string, fallbackAvatar?: string): ReactNode {
  if (avatarUrl) {
    return <img src={avatarUrl} alt={name} className="h-8 w-8 shrink-0 rounded-full object-cover" />;
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-foreground text-xs font-semibold text-background">
      {fallbackAvatar || name.charAt(0)}
    </span>
  );
}

function participantSender(
  participant: ParticipantView,
  displayName?: string,
  fallbackAvatar?: string,
): { name: string; avatar: ReactNode } | undefined {
  const name = displayName || participant.name || (participant.kind === 'bot' ? '未命名 Bot' : '未命名成员');
  return {
    name,
    avatar:
      participant.kind === 'bot'
        ? renderBotAvatar(name, participant.avatarUrl, fallbackAvatar)
        : renderHumanAvatar(name, participant.avatarUrl ?? fallbackAvatar),
  };
}

/**
 * 解析消息发送者展示信息（头像 + 名称）。优先使用消息本地回显信息，其次按 senderId
 * 匹配会话成员/群成员；assistant 保留 botName 与群名兜底，user 使用稳定的「未命名成员」兜底。
 * 该解析只消费现有消息 extra 与会话参与者，不改变后端消息或请求合同。
 */
export function resolveSender(
  message: ChatMessage,
  group: GroupView | null,
  sessionParticipants?: ParticipantView[],
  userAvatarUrl?: string,
  userIdentityId?: string | null,
): { name: string; avatar: ReactNode } | undefined {
  const senderId = typeof message.extra?.senderId === 'string' ? message.extra.senderId : undefined;
  const senderName = typeof message.extra?.senderName === 'string' ? message.extra.senderName : undefined;
  const senderAvatarUrl =
    typeof message.extra?.senderAvatarUrl === 'string' ? message.extra.senderAvatarUrl : undefined;
  const botUuid = typeof message.extra?.botUuid === 'string' ? message.extra.botUuid : undefined;
  const botName = typeof message.extra?.botName === 'string' ? message.extra.botName : undefined;
  const candidatePools: ParticipantView[][] = [sessionParticipants ?? [], group?.participants ?? []];

  if (message.role === 'user') {
    const humanParticipant = senderId
      ? candidatePools
          .flatMap((pool) => pool)
          .find(
            (item) =>
              item.kind === 'human' && normalizeMessageHumanId(item.actorId) === normalizeMessageHumanId(senderId),
          )
      : undefined;
    const normalizedSenderId = normalizeMessageHumanId(senderId);
    const normalizedUserIdentityId = normalizeMessageHumanId(userIdentityId);
    const isCurrentUserMessage =
      !senderId ||
      (Boolean(normalizedSenderId && normalizedUserIdentityId) && normalizedSenderId === normalizedUserIdentityId);
    if (isCurrentUserMessage) {
      const name = senderName || humanParticipant?.name || '未命名成员';
      return { name, avatar: renderCurrentUserAvatar(name, userAvatarUrl ?? senderAvatarUrl) };
    }
    if (humanParticipant) {
      return participantSender(humanParticipant, senderName, senderAvatarUrl);
    }
    if (senderName) return { name: senderName, avatar: renderHumanAvatar(senderName, senderAvatarUrl) };
    const fallbackName = '未命名成员';
    return { name: fallbackName, avatar: renderHumanAvatar(fallbackName, senderAvatarUrl) };
  }

  if (message.role !== 'assistant') {
    const fallbackName = '系统';
    return { name: fallbackName, avatar: renderBotAvatar(fallbackName) };
  }

  const isPendingPlaceholder = message.status === 'pending' && !botName && !botUuid && !senderId;
  if (isPendingPlaceholder) return undefined;

  if (botName && botName !== botUuid && botName !== senderId) {
    return { name: botName, avatar: renderBotAvatar(botName, senderAvatarUrl) };
  }

  const lookupIds = [botUuid, senderId].filter((value): value is string => Boolean(value));
  for (const id of lookupIds) {
    for (const pool of candidatePools) {
      const participant = pool.find((item) => item.actorId === id && item.kind === 'bot');
      if (participant) return participantSender(participant);
    }
  }

  const fallbackName = group?.name ?? 'Bot';
  return { name: fallbackName, avatar: renderBotAvatar(fallbackName) };
}
