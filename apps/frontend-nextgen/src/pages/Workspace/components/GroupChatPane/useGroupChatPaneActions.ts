// GroupChatPane 的消息交互动作装配：空态点名写入 / 副屏面板动作 / 引用 / 解释 / 发送
// 五个动作共用「消息查找 + resolveSender 群聊命名」口径，从主组件抽离下沉至同目录 hook，
// 使主文件保持在工程红线（Component 300 行）之内。动作语义与原实现一一对应，无行为变化。
import { useCallback } from 'react';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { buildExplainPrompt, useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { PanelAction } from '@tc-chat/core';
import type { GroupChatPaneProps } from './GroupChatPane.types';
import { resolveSender } from './messageHelpers';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

interface UseGroupChatPaneActionsParams {
  group: GroupChatPaneProps['group'];
  session: GroupChatPaneProps['session'];
  messages: ReturnType<typeof filterVisibleGroupMessages>;
  interactions: ReturnType<typeof useMessageInteractions>;
  onDraftChange: (content: string) => void;
  onSend: GroupChatPaneProps['send'];
  /** 最近一次发送携带的 @ 成员（isWaitingForBot 派生用），hook 内负责写入。 */
  onLastSentMentionsChange: (mentions: string[]) => void;
  finishEdit: () => void;
  inputRef: GroupChatPaneProps['inputRef'];
  userAvatarUrl: GroupChatPaneProps['userAvatarUrl'];
  userIdentityId: GroupChatPaneProps['userIdentityId'];
  userIdentityName: GroupChatPaneProps['userIdentityName'];
}

export function useGroupChatPaneActions({
  group,
  session,
  messages,
  interactions,
  onDraftChange,
  onSend,
  onLastSentMentionsChange,
  finishEdit,
  inputRef,
  userAvatarUrl,
  userIdentityId,
  userIdentityName,
}: UseGroupChatPaneActionsParams) {
  // 空态点名卡落地（对齐单聊 ChatEmptyState 的 insertDraft）：写入 '@' 并聚焦——Sender 的
  // insert 走 commitValue→mentionHandlers.onValueChange，成员面板随之自然唤起。
  const insertDraft = useCallback(
    (text: string) => {
      inputRef?.current?.insert(text);
      inputRef?.current?.focus();
    },
    [inputRef],
  );

  const handlePanelAction = (action: PanelAction) => {
    if (action.type === 'fill_input') {
      onDraftChange(action.content);
      return;
    }
    onSend(action.content);
  };

  const findSelectedMessage = () => messages.find((message) => message.id === interactions.selection?.messageId);

  const quoteSelectedMessage = (text: string) => {
    const selectedMessage = findSelectedMessage();
    if (!selectedMessage) return;
    const sender = resolveSender(selectedMessage, group, session?.participants, userAvatarUrl, userIdentityId, userIdentityName);
    interactions.quoteMessage(selectedMessage.id, sender?.name ?? '未命名成员', text);
  };

  const explainSelectedMessage = (text: string) => {
    const selectedMessage = findSelectedMessage();
    if (!selectedMessage) return;
    const sender = resolveSender(selectedMessage, group, session?.participants, userAvatarUrl, userIdentityId, userIdentityName);
    onDraftChange(buildExplainPrompt(sender?.name ?? '未命名成员', text));
    interactions.clearQuote();
    interactions.setSelection(null);
    finishEdit();
    inputRef?.current?.focus();
  };

  const handleGroupSend = (content: string, mentions?: string[], attachments?: Parameters<GroupChatPaneProps['send']>[2]) => {
    Tracert.click(Points.groupFocus.sendMessage, { group_id: group?.groupId });
    interactions.markRead();
    interactions.clearQuote();
    finishEdit();
    onLastSentMentionsChange(mentions ?? []);
    onSend(content, mentions, attachments);
  };

  return { insertDraft, handlePanelAction, quoteSelectedMessage, explainSelectedMessage, handleGroupSend };
}