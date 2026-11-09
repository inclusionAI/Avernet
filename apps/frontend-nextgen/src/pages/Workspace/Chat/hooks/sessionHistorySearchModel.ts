/**
 * 会话历史搜索模型 —— 历史消息面板的搜索/筛选/结果状态（本地源即时过滤）。
 *
 * 自 useChatHeaderPanels 下沉（工程红线：Hook 单文件 ≤250 行，同目录 sidePaneContent.ts
 * 同款先例）；单聊 useChatHeaderPanels 与群聊 useGroupChatHeaderPanels 语义同构，
 * 类型自此导出（历史导入面 useChatHeaderPanels 再导出保持兼容）。
 */
import type {
  SessionMessageSearchQuery,
  UseSessionHistorySearchModel,
} from '@/services/workspace/conversationSearchService';
import { searchConversationMessages } from '@/services/workspace/conversationSearchService';
import type { ChatMessage } from '@tc-chat/core';
import { useMemo, useRef, useState } from 'react';

export type { UseSessionHistorySearchModel } from '@/services/workspace/conversationSearchService';

export function useSessionHistorySearchModel(messages: ChatMessage[], sessionEpoch: number): UseSessionHistorySearchModel {
  const [keyword, setKeyword] = useState('');
  const [sender, setSender] = useState<SessionMessageSearchQuery['sender']>(null);
  const [todayOnly, setTodayOnly] = useState(false);

  // 会话切换复位筛选（sessionEpoch 变化即重置）。
  const lastEpochRef = useRef(sessionEpoch);
  if (lastEpochRef.current !== sessionEpoch) {
    lastEpochRef.current = sessionEpoch;
    setKeyword('');
    setSender(null);
    setTodayOnly(false);
  }

  const outcome = useMemo(
    () => searchConversationMessages(messages, { keyword, sender, todayOnly }),
    [messages, keyword, sender, todayOnly],
  );
  return {
    query: { keyword, sender, todayOnly },
    setKeyword,
    setSender,
    setTodayOnly,
    source: outcome.source,
    results: outcome.items,
  };
}
