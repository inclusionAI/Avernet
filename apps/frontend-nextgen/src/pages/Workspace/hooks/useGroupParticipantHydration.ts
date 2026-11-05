import { useCallback, useRef, type Dispatch, type SetStateAction } from 'react';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { toParticipantSummary } from '@/services/workspace/mappers';
import { useWorkspaceStore } from '@/stores/workspaceStore';

/** 一次列表/详情请求的代次令牌：身份 + 列次 epoch，用于丢弃迟到的旧请求结果。 */
export interface GroupParticipantRequest { identityId: string | null; epoch: number }

/**
 * 群行头像堆成员补拉：新列表自带 participantSummaries 时零请求；仅旧响应（缺摘要字段）
 * 按群详情补拉，4 并发、同身份缓存可复用。请求令牌隔离身份/列次，迟到结果不能覆盖当前列表。
 * 缓存属于 Hook 实例（页面重挂载即重置），不进入全局 Store。
 */
export function useGroupParticipantHydration(setGroups: Dispatch<SetStateAction<GroupView[]>>) {
  const current = useRef<GroupParticipantRequest>({
    identityId: useWorkspaceStore.getState().activeIdentityId, epoch: 0,
  });
  const cache = useRef(new Map<string, ParticipantView[]>());
  const revisions = useRef(new Map<string, number>());
  // 固定群入口不加载目录；身份初始化/切换不能依赖 beginListLoad。
  const ensureIdentity = useCallback((identityId: string | null) => {
    if (current.current.identityId !== identityId) {
      cache.current.clear();
      revisions.current.clear();
      current.current = { identityId, epoch: current.current.epoch + 1 };
    }
    return current.current;
  }, []);
  const beginListLoad = useCallback((identityId: string | null) => {
    ensureIdentity(identityId);
    current.current = { identityId, epoch: current.current.epoch + 1 };
    return current.current;
  }, [ensureIdentity]);
  const captureRequest = useCallback(() =>
    ensureIdentity(useWorkspaceStore.getState().activeIdentityId), [ensureIdentity]);
  const isCurrentRequest = useCallback((request: GroupParticipantRequest) =>
    request === current.current && useWorkspaceStore.getState().activeIdentityId === request.identityId, []);
  const rememberParticipants = useCallback((groupId: string, members: ParticipantView[]) => {
    revisions.current.set(groupId, (revisions.current.get(groupId) ?? 0) + 1);
    cache.current.set(groupId, members);
  }, []);
  const forgetParticipants = useCallback((groupId: string) => {
    revisions.current.set(groupId, (revisions.current.get(groupId) ?? 0) + 1);
    cache.current.delete(groupId);
  }, []);
  const seedFromCache = useCallback((list: GroupView[], request: GroupParticipantRequest) => {
    if (!isCurrentRequest(request)) return list;
    return list.map((g) => {
      // 新列表带摘要（含显式空摘要）时旧缓存失效，绝不用旧头像覆盖新列表。
      if (g.participantSummaries !== undefined) {
        forgetParticipants(g.groupId);
        return g;
      }
      const members = cache.current.get(g.groupId);
      return g.participants.length === 0 && members && members.length > 0
        ? { ...g, participants: members, participantSummaries: members.map(toParticipantSummary), participantDetailsLoaded: false } : g;
    });
  }, [forgetParticipants, isCurrentRequest]);
  const enrichParticipants = useCallback(async (
    list: GroupView[], request: GroupParticipantRequest, viewBotId?: string,
  ) => {
    const queue = list.filter((g) => g.participantSummaries === undefined &&
      g.participants.length === 0 && g.participantCount > 0 && !cache.current.has(g.groupId));
    const worker = async () => {
      while (isCurrentRequest(request)) {
        const group = queue.shift();
        if (!group) return;
        const revision = revisions.current.get(group.groupId) ?? 0;
        let members: ParticipantView[] | null = null;
        try { members = await groupService.loadGroupParticipants(group.groupId, viewBotId); }
        catch { members = null; }
        const stillCurrent = () => isCurrentRequest(request) &&
          (revisions.current.get(group.groupId) ?? 0) === revision;
        if (!stillCurrent()) continue;
        // 失败也写空占位，避免本次挂载对同一群反复请求（不弹额外 Toast）。
        cache.current.set(group.groupId, members ?? []);
        if (!members?.length) continue;
        const loaded = members;
        setGroups((groups) => !stillCurrent() ? groups : groups.map((g) =>
          g.groupId === group.groupId && g.participantSummaries === undefined && g.participants.length === 0
            ? { ...g, participants: loaded, participantSummaries: loaded.map(toParticipantSummary), participantDetailsLoaded: false } : g));
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, queue.length) }, () => worker()));
  }, [isCurrentRequest, setGroups]);
  return { beginListLoad, captureRequest, isCurrentRequest, seedFromCache,
    enrichParticipants, rememberParticipants, forgetParticipants };
}
