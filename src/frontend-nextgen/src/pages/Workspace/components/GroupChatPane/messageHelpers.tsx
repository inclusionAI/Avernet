import type { GroupView, ParticipantView } from '@/domain/collaboration';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { formatChatTime } from '@/utils/format';
import type { Block, ChatMessage, ImageBlock, TextBlock } from '@tc-chat/core';
import type { ReactNode } from 'react';

/** 图片失效/已删（无 url）时展示的占位图（SVG data URL）。 */
const IMAGE_UNAVAILABLE_PLACEHOLDER =
  'data:image/svg+xml;charset=utf8,' +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='240' height='160'><rect width='240' height='160' fill='#f1f5f9'/><g fill='none' stroke='#cbd5e1' stroke-width='2'><rect x='90' y='45' width='60' height='50' rx='4'/><circle cx='106' cy='62' r='6' fill='#cbd5e1'/><path d='M90 95 L112 73 L132 90 L150 65 L150 95Z' fill='#cbd5e1'/></g><text x='120' y='125' text-anchor='middle' font-family='sans-serif' font-size='14' fill='#94a3b8'>图片不可用</text></svg>`,
  );

/** 取消息展示时间：优先 extra.displayTime，回退 createdAt（当天 HH:mm，非当天 MM-dd HH:mm）。 */
export function getMessageTime(message: ChatMessage): string | undefined {
  const displayTime = message.extra?.displayTime;
  if (displayTime) return formatChatTime(displayTime);
  return formatChatTime(message.createdAt);
}

/** 取消息渲染块：优先自带 blocks，否则把 content 包成单文本块。 */
export function getMessageBlocks(message: ChatMessage): Block[] {
  if (message.blocks?.length) return message.blocks;
  const bcsAttachments = (message.extra?.attachments ?? []) as SessionMessageAttachment[];
  const imageBlocks: ImageBlock[] = bcsAttachments
    .filter((attachment) => attachment.type === 'image')
    .map((attachment) => ({
      type: 'image' as const,
      data: attachment.url || IMAGE_UNAVAILABLE_PLACEHOLDER,
      name: attachment.file_name ?? 'image',
      mimeType: attachment.mime_type ?? 'image/png',
    }));
  const blocks: Block[] = [...imageBlocks];
  if (message.content) blocks.push({ type: 'text', content: message.content } as TextBlock);
  return blocks;
}

/**
 * 解析 assistant 消息的发送者展示信息（头像 + 名称）。优先级：
 * 1. ws/历史 DTO 里明确的 message.extra.botName（非 bot_id 退化值）
 * 2. botUuid / senderId → 会话成员、群成员匹配真实 bot 名称
 *    （SDK ws 解析在拿不到名称时会把 botName 兜底成 botUuid，故 botName===botUuid 时不可信）
 * 3. group.name 兜底
 * 占位中的 assistant 消息（bot 还在回复，extra 无任何发送者标识）返回 undefined，
 * 不展示名称——避免误显示为群聊名称。
 * - user / system 消息：返回 undefined（user 右侧「我」，system 无头像）
 */
export function resolveSender(
  message: ChatMessage,
  group: GroupView | null,
  sessionParticipants?: ParticipantView[],
): { name: string; avatar: ReactNode } | undefined {
  if (message.role !== 'assistant') return undefined;

  const botUuid = message.extra?.botUuid;
  const senderId = message.extra?.senderId;
  const botName = message.extra?.botName;

  // bot 回复途中的 pending 占位消息没有任何发送者标识 → 不展示名称（避免误显示群名）；
  // 历史消息等其它形态继续走下方兜底。
  const isPendingPlaceholder =
    message.status === 'pending' &&
    !(typeof botName === 'string' && botName.length > 0) &&
    !(typeof botUuid === 'string' && botUuid.length > 0) &&
    !(typeof senderId === 'string' && senderId.length > 0);
  if (isPendingPlaceholder) return undefined;

  // botName 可信（非 botUuid 退化值）时直接使用
  if (typeof botName === 'string' && botName.length > 0 && botName !== botUuid && botName !== senderId) {
    const initial = botName.charAt(0);
    return {
      name: botName,
      avatar: (
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--color-primary)] text-xs font-semibold text-white">
          {initial}
        </span>
      ),
    };
  }

  // 按 botUuid / senderId 在会话成员、群成员中匹配真实名称
  const lookupIds = [botUuid, senderId].filter((v): v is string => typeof v === 'string' && v.length > 0);
  const candidatePools: ParticipantView[][] = [sessionParticipants ?? [], group?.participants ?? []];
  for (const id of lookupIds) {
    for (const pool of candidatePools) {
      const participant: ParticipantView | undefined = pool.find((p) => p.actorId === id);
      if (!participant) continue;
      const initial = participant.name?.charAt(0) ?? 'G';
      return {
        name: participant.name,
        avatar: participant.avatarUrl ? (
          <img src={participant.avatarUrl} alt={participant.name} className="h-8 w-8 rounded-lg object-cover" />
        ) : (
          <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--color-primary)] text-xs font-semibold text-white">
            {initial}
          </span>
        ),
      };
    }
  }

  // 兜底到群名（找不到 bot_name 与参与人时仍展示一定语义）
  const fallbackName = group?.name ?? 'Bot';
  return {
    name: fallbackName,
    avatar: (
      <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--color-primary)] text-xs font-semibold text-white">
        {fallbackName.charAt(0) ?? 'G'}
      </span>
    ),
  };
}
