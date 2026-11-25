/**
 * 公开设置「好友名单」数据 Hook：按 Bot 维度拉取已建好友连接（Human 好友用户 / Bot 好友 Bot），
 * 经纯领域映射成 roster；分计与过滤在领域层，视图在组件层。
 *
 * 读取口径：listFriendConnections 以 actor_type='bot' + actor_id=Bot UUID 查询该 Bot 的好友连接，
 * target_type 分别取 human / bot 得到两类名单（与 managedBotConversationService / botFriendConversationService 同源）。
 *
 * 移除口径：PRD §6 明确「Owner 移除的正式接口合同（关系主体 actor 语义）」为后续接入项，本期为
 * Demo 级前端行为（PRD §8）；故 removeFriend 目前仅本地即时生效并提示，待正式接口落地后在此替换为
 * 真实删除调用并触发 reload，不在本期臆造后端写接口（避免误删登录人自身好友边）。
 */
import { notifySuccess } from '@/components/ui/notify';
import {
  buildRosterFriends,
  type RosterFriend,
  type RosterSeed,
} from '@/domain/collaborationPrivacy/friendRoster';
import {
  listFriendConnections,
  type FriendConnectionSummaryDto,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { useCallback, useEffect, useState } from 'react';

const FRIEND_PAGE_SIZE = 100;

async function loadFriendActors(
  botId: string,
  type: 'human' | 'bot',
  signal: AbortSignal,
): Promise<RosterSeed[]> {
  const seeds: RosterSeed[] = [];
  let page = 1;
  let total = 0;
  // 分页拉全量：首页必拉；后续按 total 或「本页为空」收敛（与既有好友目录拉取同法）。
  while (page === 1 || seeds.length < total) {
    const response = await listFriendConnections<FriendConnectionSummaryDto>(
      {
        actor_type: 'bot',
        actor_id: botId,
        target_type: type,
        page,
        page_size: FRIEND_PAGE_SIZE,
      },
      signal,
    );
    const items = response?.data?.items;
    if (!Array.isArray(items)) {
      throw new Error('好友名单接口返回了无法识别的数据');
    }
    total = response?.data?.total ?? seeds.length + items.length;
    for (const item of items) {
      const actor = item?.actor;
      if (!actor || actor.type !== type || typeof actor.id !== 'string' || !actor.id.trim()) continue;
      seeds.push({ id: actor.id.trim(), name: item.name });
    }
    if (items.length === 0) break;
    page += 1;
  }
  return seeds;
}

export interface BotFriendRosterState {
  friends: RosterFriend[];
  loading: boolean;
  error: string | null;
  /** 重新拉取名单（抽屉内重试 / 移除后刷新）。 */
  reload: () => void;
  /** 移除好友（Demo 口径，见文件头；正式接口按 PRD §6 后续接入）。 */
  removeFriend: (friend: RosterFriend) => void;
}

export function useBotFriendRoster(botId: string, enabled: boolean): BotFriendRosterState {
  const [friends, setFriends] = useState<RosterFriend[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!enabled || !botId) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([
      loadFriendActors(botId, 'human', controller.signal),
      loadFriendActors(botId, 'bot', controller.signal),
    ])
      .then(([humans, bots]) => {
        if (controller.signal.aborted) return;
        setFriends(buildRosterFriends({ humans, bots }));
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        console.error('[useBotFriendRoster] 加载好友名单失败:', err);
        setError(err instanceof Error ? err.message : '好友名单加载失败，请稍后重试');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [botId, enabled, reloadToken]);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  const removeFriend = useCallback((friend: RosterFriend) => {
    // Demo 口径（PRD §8）；正式 owner 移除接口按 PRD §6 后续接入后替换此处为真实删除 + reload。
    setFriends((current) => current.filter((item) => !(item.type === friend.type && item.id === friend.id)));
    notifySuccess('已移除好友');
  }, []);

  return { friends, loading, error, reload, removeFriend };
}