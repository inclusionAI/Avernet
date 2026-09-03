import type { Block, ChatMessage, ImageBlock, TextBlock } from '@tc-chat/core';

/**
 * 消息展示层可识别的图片附件形态。
 * 同时兼容协作群 BCS snake_case 和 SDK/单聊可能出现的 camelCase，
 * 不向后端请求或领域模型写入新字段。
 */
export interface MessageImageAttachment {
  attachment_id?: unknown;
  attachmentId?: unknown;
  type?: unknown;
  url?: unknown;
  file_name?: unknown;
  fileName?: unknown;
  mime_type?: unknown;
  mimeType?: unknown;
  size?: unknown;
  expires_at?: unknown;
  content?: unknown;
}

export interface MessageBlockBuildOptions {
  resolveAttachmentUrl?: (attachment: MessageImageAttachment) => string | undefined;
}

/** 图片失效/已删时统一使用的展示降级资源。仅存在于前端展示层。 */
export const IMAGE_UNAVAILABLE_PLACEHOLDER =
  'data:image/svg+xml;charset=utf8,' +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='240' height='160'><rect width='240' height='160' fill='#f1f5f9'/><g fill='none' stroke='#cbd5e1' stroke-width='2'><rect x='90' y='45' width='60' height='50' rx='4'/><circle cx='106' cy='62' r='6' fill='#cbd5e1'/><path d='M90 95 L112 73 L132 90 L150 65 L150 95Z' fill='#cbd5e1'/></g><text x='120' y='125' text-anchor='middle' font-family='sans-serif' font-size='14' fill='#94a3b8'>图片不可用</text></svg>`,
  );

function asAttachmentList(value: unknown): MessageImageAttachment[] {
  return Array.isArray(value)
    ? (value.filter((item) => item && typeof item === 'object') as MessageImageAttachment[])
    : [];
}

function getMessageAttachments(message: ChatMessage): MessageImageAttachment[] {
  const attachments = [...asAttachmentList(message.attachments), ...asAttachmentList(message.extra?.attachments)];
  const seen = new Set<string>();
  return attachments.filter((attachment) => {
    const id = String(attachment.attachment_id ?? attachment.attachmentId ?? '');
    const url = String(attachment.url ?? '');
    const name = String(attachment.file_name ?? attachment.fileName ?? '');
    const key = id || `${url}|${name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function contentToDataUrl(attachment: MessageImageAttachment, mimeType: string): string | undefined {
  if (typeof attachment.content !== 'string' || !attachment.content) return undefined;
  if (attachment.content.startsWith('data:')) return attachment.content;
  return `data:${mimeType};base64,${attachment.content}`;
}

/** 将图片附件统一转换为 SDK ImageBlock；无可用地址时返回同一前端占位图。 */
export function imageAttachmentsToBlocks(
  attachments: MessageImageAttachment[] | undefined,
  options: MessageBlockBuildOptions = {},
): ImageBlock[] {
  return (attachments ?? [])
    .filter((attachment) => attachment.type === 'image')
    .map((attachment) => {
      const mimeType = String(attachment.mime_type ?? attachment.mimeType ?? 'image/png');
      const data =
        options.resolveAttachmentUrl?.(attachment) ||
        (typeof attachment.url === 'string' ? attachment.url : undefined) ||
        contentToDataUrl(attachment, mimeType) ||
        IMAGE_UNAVAILABLE_PLACEHOLDER;
      return {
        type: 'image' as const,
        data,
        name: String(attachment.file_name ?? attachment.fileName ?? 'image'),
        mimeType,
      };
    });
}

/** 优先尊重已解析 blocks；没有 blocks 时统一补齐图片附件与文本回退。 */
export function buildMessageBlocks(message: ChatMessage, options: MessageBlockBuildOptions = {}): Block[] {
  if (message.blocks?.length) return message.blocks;
  const attachments = getMessageAttachments(message);
  const blocks: Block[] = [...imageAttachmentsToBlocks(attachments, options)];
  if (message.content) blocks.push({ type: 'text', content: message.content } as TextBlock);
  return blocks;
}
