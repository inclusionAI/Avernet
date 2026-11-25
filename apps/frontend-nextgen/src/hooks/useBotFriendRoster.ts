// 好友名单数据 Hook(公开设置 · 好友名单 section + 管理抽屉共用）。
//
// `botUuid` 必须是 BCS 协作 Bot UUID（dialog 里的 `targetBot.id`，带 `:port`
// 后缀），绝不能传后端行 `bot.id` —— 后者在 friend-connections 接口上是
// 另一个标识，会 404/403。
//
// 数据流：`loadDirectory` 一次装入两个类型段（fetch-all 全量），计数与抽屉
// 列表共生；`reload(type)` 只重拉一个类型段；`removeFriend` 走 Owner 移除
// 参数（actor=被管理 Bot)，成功后原地重拉该类型，deleted:false 视为"已被
// 移除"的中性路径（无错误态）。加载/移除的错误分别留在各类型段
// (DomainError，可重试）和 `removeError` 里供 UI 渲染。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  rosterCounts,
  type BotFriendRosterCounts,
} from '@/domain/collaborationPrivacy/botFriends';
import {
  botFriendConversationService,
  type BotFriendActorType,
  type BotFriendDirectoryResult,
  type BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import type { DomainError } from '@/services/workspace/identityService';

export interface BotFriendRosterRemoval {
  ok: boolean;
  /** 服务端回报 deleted:false —— 好友关系早已被移除（重复点击/并发）,走中性提示。 */
  alreadyRemoved?: boolean;
  /** 同一好友的移除尚在途，本次触发被去重丢弃。 */
  duplicate?: boolean;
}

export interface BotFriendRoster {
  sections: BotFriendDirectoryResult | null;
  counts: BotFriendRosterCounts;
  loading: boolean;
  reload: (type?: BotFriendActorType) => Promise<void>;
  removeFriend: (view: BotIdentityFriendView) => Promise<BotFriendRosterRemoval>;
  removingIds: string[];
  removeError: DomainError | null;
}

export function useBotFriendRoster(botUuid: string): BotFriendRoster {
  const [sections, setSections] = useState<BotFriendDirectoryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [removingIds, setRemovingIds] = useState<string[]>([]);
  const [removeError, setRemoveError] = useState<DomainError | null>(null);
  // 单调序号：botUuid 切换后丢弃过期响应；移除在途集合用 ref 保持 removeFriend 引用稳定。
  const requestSeq = useRef(0);
  const removingRef = useRef<Set<string>>(new Set());

  const reload = useCallback(
    async (type?: BotFriendActorType) => {
      const seq = ++requestSeq.current;
      if (!type) {
        setLoading(true);
        const result = await botFriendConversationService.loadDirectory(botUuid);
        if (seq === requestSeq.current) {
          setSections(result);
          setLoading(false);
        }
        return;
      }
      const section = await botFriendConversationService.loadSection(botUuid, type);
      if (seq !== requestSeq.current) return;
      setSections((prev) =>
        prev
          ? {
              human: type === 'human' ? section : prev.human,
              bot: type === 'bot' ? section : prev.bot,
            }
          : prev,
      );
    },
    [botUuid],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  const counts = useMemo(
    () => (sections ? rosterCounts(sections.human, sections.bot) : { users: null, bots: null }),
    [sections],
  );

  const removeFriend = useCallback(
    async (view: BotIdentityFriendView): Promise<BotFriendRosterRemoval> => {
      if (removingRef.current.has(view.actorId)) {
        return { ok: false, duplicate: true };
      }
      removingRef.current.add(view.actorId);
      setRemovingIds(Array.from(removingRef.current));
      setRemoveError(null);
      try {
        // 分层：Hook 只调 Service，由 Service 再落到 API Controller。
        const result = await botFriendConversationService.removeFriend(botUuid, view);
        if (!result.ok) {
          setRemoveError(result.error);
          return { ok: false };
        }
        await reload(view.actorType);
        return result.data.deleted ? { ok: true } : { ok: true, alreadyRemoved: true };
      } catch {
        setRemoveError({
          code: 'BOT_FRIEND_REMOVE_FAILED',
          friendlyMessage: '移除好友失败，请稍后重试。',
          canRetry: true,
        });
        return { ok: false };
      } finally {
        removingRef.current.delete(view.actorId);
        setRemovingIds(Array.from(removingRef.current));
      }
    },
    [botUuid, reload],
  );

  return { sections, counts, loading, reload, removeFriend, removingIds, removeError };
}
