import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { GroupChatRequest } from '@/services/workspace/groupChatProvider';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import type { AixContext } from '@tc-chat/core';

/**
 * 群聊请求参数构造——供直接路径（useGroupChat.send）与桥路径（useChatBridge.buildRequestParams）共用，
 * 避免两路本地回显（displayTime / attachments 内容地址）不一致。
 *
 * 从 useGroupChat 抽出为纯函数：控 useGroupChat ≤250 行门禁（design D1 / O3）。
 */

export interface GroupMessageActorEcho {
  senderId?: string;
  senderName?: string;
  senderAvatarUrl?: string;
}

/** 群聊用户消息 extra（仅本地回显）：displayTime、发送者展示信息与附件内容地址。 */
export function buildGroupUserMessageExtra(
  echoAttachments?: SessionMessageAttachment[],
  actor?: GroupMessageActorEcho,
) {
  return {
    displayTime: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    ...(actor?.senderId ? { senderId: actor.senderId } : {}),
    ...(actor?.senderName ? { senderName: actor.senderName } : {}),
    ...(actor?.senderAvatarUrl ? { senderAvatarUrl: actor.senderAvatarUrl } : {}),
    ...(echoAttachments && echoAttachments.length > 0 ? { attachments: echoAttachments } : {}),
  };
}

/** 把会话附件转成本地回显形态（share_url → 会话内容地址，避免分享域名/CORS 加载失败）。 */
export function buildEchoAttachments(sessionId: string, attachments?: SessionMessageAttachment[]) {
  return attachments?.map((attachment) => ({
    ...attachment,
    url: sessionFileService.buildContentUrl(sessionId, attachment.attachment_id),
  }));
}

/**
 * 群聊桥路径 buildRequestParams：把 aixcore 卡片 `bridge.sendMessage(content, extra)` 转成
 * `chat.onRequest` 期望的 GroupChatRequest（顶层 content + 字段保真）。
 *
 * - 根因 A：顶层 `content` 必须存在（provider 读 params.content → query → ws 帧 message）。
 *   SDK 默认 buildRequestParams 仅产 `{...extra, userMessage:{content}}`（无顶层 content）→ ws 帧 message 丢。
 * - 根因 C：botUuid / mentionAll / replyToMessageId / panelContext / isInject 从桥 extra 透传
 *   （provider 据此组装 ws 帧 bot_uuid / mentionAll / reply_to / panelContext / isInject 字段）。
 *   panelContext 亦可由 SDK useChatBridge.withPanelContext 从 panelRef.flushContext() 注入 extra，
 *   此处展开透传两路兼容。
 *
 * `extra` 形参：SDK useChatBridge 在调 build(content, withPanelContext(extra)) 时已做 panelContext 注入，
 * 故 extra 可能含 panelContext / isInject / mentions / attachments / botUuid 等卡片透传字段。
 */
export function buildGroupChatBridgeRequest(
  sessionId: string,
  content: string,
  extra?: Record<string, unknown>,
  actor?: GroupMessageActorEcho,
): GroupChatRequest & { userMessage: { content: string; extra: ReturnType<typeof buildGroupUserMessageExtra> } } {
  const trimmed = (content ?? '').trim();
  const attachments = extra?.attachments as SessionMessageAttachment[] | undefined;
  const echoAttachments =
    attachments && attachments.length > 0 ? buildEchoAttachments(sessionId, attachments) : undefined;
  return {
    content: trimmed,
    sessionId,
    ...(extra?.mentions ? { mentions: extra.mentions as string[] } : {}),
    ...(extra?.botUuid ? { botUuid: extra.botUuid as string } : {}),
    ...(extra?.mentionAll ? { mentionAll: extra.mentionAll as boolean } : {}),
    ...(extra?.replyToMessageId ? { replyToMessageId: extra.replyToMessageId as string } : {}),
    ...(attachments && attachments.length > 0 ? { attachments } : {}),
    ...(extra?.panelContext !== undefined ? { panelContext: extra.panelContext as AixContext } : {}),
    ...(extra && Object.prototype.hasOwnProperty.call(extra, 'isInject')
      ? { isInject: extra.isInject as boolean }
      : {}),
    userMessage: {
      content: trimmed,
      extra: buildGroupUserMessageExtra(echoAttachments, actor),
    },
  };
}
