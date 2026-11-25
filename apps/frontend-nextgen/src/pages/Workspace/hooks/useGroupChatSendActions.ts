import type { IdentityView } from '@/domain/collaboration';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { GroupChatProvider, GroupChatRequest } from '@/services/workspace/groupChatProvider';
import type { UserMessageConfig } from '@tc-chat/adapters';
import { useCallback } from 'react';
import {
  buildActorEcho,
  buildEchoAttachments,
  buildGroupChatBridgeRequest,
  buildGroupUserMessageExtra,
} from './groupChatRequestBuilder';

/**
 * 群聊发送/副屏提交动作 —— 从 useGroupChat 抽出（沿 groupChatRequestBuilder 先例，
 * 控 useGroupChat hook ≤250 行门禁）。
 *
 * 三路径共用 buildActorEcho / buildEchoAttachments 回显映射（O3 回显一致）：
 * - send：主输入框直接路径（文本 + mentions + 附件本地回显）。
 * - submitPanelMessage / submitTaskExecutionMessage：副屏标准桥请求（isInject 注入语义与
 *   执行审批 botUuid 指定），与 useChatBridge.buildRequestParams 走同一构造器。
 */

/** 仅声明本 hook 消费的最小 chat 面（onRequest 注入对话流，与 SDK `TInput & {userMessage?}` 同构）。 */
interface GroupChatSendChatLike {
  onRequest: (request: GroupChatRequest & { userMessage?: UserMessageConfig }) => void;
}

export function useGroupChatSendActions({
  chat,
  provider,
  sessionId,
  activeIdentity,
}: {
  chat: GroupChatSendChatLike;
  provider: GroupChatProvider | null;
  sessionId: string | null;
  activeIdentity: IdentityView | null;
}) {
  const send = (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => {
    const hasText = text.trim().length > 0;
    const hasAttachments = !!attachments && attachments.length > 0;
    // 输出中不阻塞发送：SDK 经 supportsConcurrentRequests 直发 provider.request，
    // 消息进入后端投递队列依次消费（拥塞控制归属后端）。
    if (!provider?.liveEnabled || !sessionId || (!hasText && !hasAttachments)) return;
    const trimmed = text.trim();
    // 本地回显附件：share_url 用于跨端/免鉴权分发，`<img>` 本地回显改走会话内容地址避免分享域名/CORS 加载失败。
    // 与桥路径 buildGroupChatBridgeRequest 共用 buildEchoAttachments/buildGroupUserMessageExtra（O3 回显一致）。
    const echoAttachments = buildEchoAttachments(sessionId, attachments);
    chat.onRequest({
      content: trimmed,
      sessionId,
      ...(mentions && mentions.length > 0 ? { mentions } : {}),
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      userMessage: {
        content: trimmed,
        extra: buildGroupUserMessageExtra(echoAttachments, buildActorEcho(activeIdentity)),
      },
    });
  };

  const submitPanelMessage = useCallback(
    (content: string) => {
      if (sessionId && provider?.liveEnabled)
        chat.onRequest(
          buildGroupChatBridgeRequest(sessionId, content, { isInject: true }, buildActorEcho(activeIdentity)),
        );
    },
    [activeIdentity, chat, sessionId, provider],
  );
  const submitTaskExecutionMessage = useCallback(
    (content: string, holderId: string) => {
      if (sessionId && provider?.liveEnabled)
        chat.onRequest(
          buildGroupChatBridgeRequest(
            sessionId,
            content,
            { isInject: false, botUuid: holderId },
            buildActorEcho(activeIdentity),
          ),
        );
    },
    [activeIdentity, chat, sessionId, provider],
  );
  return { send, submitPanelMessage, submitTaskExecutionMessage };
}
