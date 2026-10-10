// 会话目录 Hook:管理 / 团队 / 好友目录。团队单独加载、错误与重试。
// 设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md(对话 Hook 固定
// 消费 Human identity,不读取全局 active identity)。
// 降级规则(遵循 conversationService.listDirectory 契约):好友 Bot 列表失败在 Service 内
// 降级为 `friendBots: []` 且整体 ok:true——本 Hook 原样透出 Service 上报的结果,不重试、
// 不伪造错误;`friendError` 仅在 Service 将来显式上报好友失败时非空。`retryFriend` 会重新
// 拉取整个目录(当前 Service 只有组合目录一个入口)。
import type { ConversationBotView } from '@/domain/conversation/types';
import { conversationService } from '@/services/workspace/conversationService';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useConversationTeamDirectory } from './useConversationTeamDirectory';

export interface ConversationDirectoryModel {
  teamBots: ConversationBotView[];
  teamLoading: boolean;
  teamError: string | null;
  retryTeam(): void;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  managedLoading: boolean;
  friendLoading: boolean;
  managedError: string | null;
  friendError: string | null;
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
}

const EMPTY_STATE: DirectoryState = {
  managedBots: [],
  friendBots: [],
  managedLoading: false,
  friendLoading: false,
  managedError: null,
  friendError: null,
};

/** 目录 Hook:user id 为固定 Human id;用户切换时丢弃旧请求的写入(generation 保护)。 */
export function useConversationDirectory(userId: string | null): ConversationDirectoryModel {
  const team = useConversationTeamDirectory(userId);
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
      });
    });
    return () => {
      active = false;
    };
  }, [userId, managedNonce, friendNonce]);

  const retryManaged = useRef(() => setManagedNonce((nonce) => nonce + 1)).current;
  const retryFriend = useRef(() => setFriendNonce((nonce) => nonce + 1)).current;

  const teamBots = useMemo(() => {
    const managedIds = new Set(state.managedBots.map((view) => view.bot.botId));
    return team.teamBots.filter((view) => !managedIds.has(view.bot.botId));
  }, [state.managedBots, team.teamBots]);
  const friendBots = useMemo(() => {
    const primaryIds = new Set([...state.managedBots, ...teamBots].map((view) => view.bot.botId));
    return state.friendBots.filter((view) => !primaryIds.has(view.bot.botId));
  }, [state.managedBots, state.friendBots, teamBots]);

  return {
    ...team,
    teamBots,
    managedBots: state.managedBots,
    friendBots,
    managedLoading: state.managedLoading,
    friendLoading: state.friendLoading,
    managedError: state.managedError,
    friendError: state.friendError,
    retryManaged,
    retryFriend,
  };
}
