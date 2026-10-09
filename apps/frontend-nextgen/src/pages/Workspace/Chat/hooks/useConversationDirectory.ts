// 会话目录 Hook:管理 Bot / 好友 Bot 两组目录的加载、错误与重试。
// 设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md(对话 Hook 固定
// 消费 Human identity,不读取全局 active identity)。
// 降级规则(遵循 conversationService.listDirectory 契约):好友 Bot 列表失败在 Service 内
// 降级为 `friendBots: []` 且整体 ok:true——本 Hook 原样透出 Service 上报的结果,不重试、
// 不伪造错误;`friendError` 仅在 Service 将来显式上报好友失败时非空。`retryFriend` 会重新
// 拉取整个目录(当前 Service 只有组合目录一个入口)。
import type { ConversationBotView } from '@/domain/conversation/types';
import { conversationService } from '@/services/workspace/conversationService';
import { useEffect, useRef, useState } from 'react';

export interface ConversationDirectoryModel {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  managedLoading: boolean;
  friendLoading: boolean;
  managedError: string | null;
  friendError: string | null;
  /** 目录结果透传:是否存在独立入口消费的 AgentCoding Bot(入口降级展示用)。 */
  hasAgentCodingBots: boolean;
  retryManaged(): void;
  retryFriend(): void;
}

interface DirectoryState {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  managedLoading: boolean;
  friendLoading: boolean;
  managedError: string | null;
  friendError: string | null;
  hasAgentCodingBots: boolean;
}

const EMPTY_STATE: DirectoryState = {
  managedBots: [],
  friendBots: [],
  managedLoading: false,
  friendLoading: false,
  managedError: null,
  friendError: null,
  hasAgentCodingBots: false,
};

/** 目录 Hook:user id 为固定 Human id;用户切换时丢弃旧请求的写入(generation 保护)。 */
export function useConversationDirectory(userId: string | null): ConversationDirectoryModel {
  const [state, setState] = useState<DirectoryState>(EMPTY_STATE);
  const [managedNonce, setManagedNonce] = useState(0);
  const [friendNonce, setFriendNonce] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    // 用户变化或重试即一轮新的 generation;在途旧响应不再写入。
    generationRef.current += 1;
    const generation = generationRef.current;
    if (!userId) {
      setState(EMPTY_STATE);
      return;
    }
    setState({
      managedBots: [],
      friendBots: [],
      managedLoading: true,
      friendLoading: true,
      managedError: null,
      friendError: null,
      hasAgentCodingBots: false,
    });
    let active = true;
    void conversationService.listDirectory(userId).then((result) => {
      // 卸载或用户切换/重试后丢弃,而不是 abort(generation 兜底;Service 无 signal 参数)。
      if (!active || generation !== generationRef.current) return;
      if (!result.ok) {
        setState({
          managedBots: [],
          friendBots: [],
          managedLoading: false,
          friendLoading: false,
          managedError: result.error.friendlyMessage,
          // 整体失败不含好友失败信息;按 Service 契约保持 null。
          friendError: null,
          hasAgentCodingBots: false,
        });
        return;
      }
      setState({
        managedBots: result.data.managedBots.map((bot) => ({ bot, section: 'managed' as const })),
        friendBots: result.data.friendBots.map((bot) => ({ bot, section: 'friend' as const })),
        managedLoading: false,
        friendLoading: false,
        managedError: null,
        friendError: null,
        hasAgentCodingBots: result.data.hasAgentCodingBots,
      });
    });
    return () => {
      active = false;
    };
  }, [userId, managedNonce, friendNonce]);

  const retryManaged = useRef(() => setManagedNonce((nonce) => nonce + 1)).current;
  const retryFriend = useRef(() => setFriendNonce((nonce) => nonce + 1)).current;

  return {
    managedBots: state.managedBots,
    friendBots: state.friendBots,
    managedLoading: state.managedLoading,
    friendLoading: state.friendLoading,
    managedError: state.managedError,
    friendError: state.friendError,
    hasAgentCodingBots: state.hasAgentCodingBots,
    retryManaged,
    retryFriend,
  };
}
