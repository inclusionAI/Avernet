import type {
  Block,
  ChatMessage,
  ImageBlock,
  MessageAttachment,
} from '@aix-chat/core';

/**
 * 将附件转换为 blocks（后端协议格式）
 * { type: 'file', fileName, content: base64, mimeType }
 */
function attachmentsToBlocks(attachments?: MessageAttachment[]): ImageBlock[] {
  if (!attachments || attachments.length === 0) return [];

  return attachments
    .filter((att) => att.type === 'file')
    .map((att) => ({
      type: 'image' as const,
      data: `data:${att.mimeType};base64,${att.content}`,
      name: att.fileName,
      mimeType: att.mimeType,
    }));
}

/**
 * 获取消息的 blocks
 * 合并消息的 blocks、attachments 和 content 为统一的 Block 列表
 */
export function getMessageBlocks(msg: ChatMessage): Block[] {
  const blocks: Block[] = [];

  // 先添加已有 blocks（如果有）
  if (msg.blocks && msg.blocks.length > 0) {
    blocks.push(...msg.blocks);
  }

  // 添加图片 blocks（附件）- 始终处理 attachments
  const imageBlocks = attachmentsToBlocks(msg.attachments);
  blocks.push(...imageBlocks);

  // 添加文本 block（如果有内容且还没有 text block）
  if (msg.content?.trim()) {
    const hasTextBlock = blocks.some((b) => b.type === 'text');
    if (!hasTextBlock) {
      blocks.push({ type: 'text', content: msg.content });
    }
  }

  // 如果没有 blocks，返回空文本 block
  if (blocks.length === 0) {
    blocks.push({ type: 'text', content: '' });
  }

  return blocks;
}
