import type { ConversationBotView } from '@/domain/conversation';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
import { useCallback, useEffect, useState } from 'react';

interface TeamDirectoryState {
  teamBots: ConversationBotView[];
  teamLoading: boolean;
  teamError: string | null;
}
const EMPTY: TeamDirectoryState = { teamBots: [], teamLoading: false, teamError: null };

/** 团队目录独立加载/失败/重试，不阻塞管理及好友目录；登录用户变化取消旧请求。 */
export function useConversationTeamDirectory(userId: string | null) {
  const [state, setState] = useState<TeamDirectoryState & { userId: string | null }>({ ...EMPTY, userId: null });
  const [nonce, setNonce] = useState(0);
  const retryTeam = useCallback(() => setNonce((value) => value + 1), []);
  useEffect(() => {
    if (!userId) {
      setState({ ...EMPTY, userId });
      return;
    }
    const controller = new AbortController();
    setState({ ...EMPTY, userId, teamLoading: true });
    void teamBotConversationService.listBots(userId, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setState(
        result.ok
          ? {
              userId,
              teamBots: result.data.map((bot) => ({ bot, section: 'team' })),
              teamLoading: false,
              teamError: null,
            }
          : { ...EMPTY, userId, teamError: result.error.friendlyMessage },
      );
    });
    return () => controller.abort();
  }, [userId, nonce]);
  const visible = state.userId === userId ? state : { ...EMPTY, teamLoading: Boolean(userId) };
  return { teamBots: visible.teamBots, teamLoading: visible.teamLoading, teamError: visible.teamError, retryTeam };
}
