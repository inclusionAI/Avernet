// useGroupWorkspace 的成员/详情动作下沉层：群行头像堆 participants 缓存与补拉、
// 选中群详情按需重载、解散群。与 useGroupWorkspace 内联实现语义一一对应，无行为变化
// （工程红线：Hook 单文件 ≤250 行）。
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { groupService } from '@/services/workspace/groupService';
import { toast } from 'sonner';
import { useCallback, useRef } from 'react';
import { useWorkspaceStore } from '@/stores/workspaceStore';

// 群行头像堆成员补拉并发预算：列表缺 participants 的群按详情逐个拉取（N+1），
// 4 并发对线上网关友好且失败不阻塞其余群；命中缓存的群零请求（防抖搜索不重复拉）。
const PARTICIPANTS_ENRICH_CONCURRENCY = 4;

interface UseGroupWorkspaceMutationsParams {
  /** 主 Hook 的群列表 state（读取当前值：解散校验用）。 */
  sessionGroups: GroupView[];
  setSessionGroups: React.Dispatch<React.SetStateAction<GroupView[]>>;
  activeIdentityId: string | null;
  selectedGroupId: string | null;
}

export function useGroupWorkspaceMutations({
  sessionGroups,
  setSessionGroups,
  activeIdentityId,
  selectedGroupId,
}: UseGroupWorkspaceMutationsParams) {
  // 群行头像堆成员缓存（groupId → participants，跨身份/搜索的列次复用）：列表接口只有
  // participant_count，群行真实头像（2026-10-12 去文件夹图标）按群详情补拉一次后在此常驻。
  const participantsCacheRef = useRef<Map<string, ParticipantView[]>>(new Map());

  /** 新列表播种：先用缓存过的成员名单补齐列表接口不回传的 participants。 */
  const seedFromCache = useCallback(
    (list: GroupView[]) => {
      const cache = participantsCacheRef.current;
      return list.map((g) => {
        const cached = cache.get(g.groupId);
        return cached && cached.length > 0 && g.participants.length === 0 ? { ...g, participants: cached } : g;
      });
    },
    [],
  );

  // 群行头像堆成员补拉（fire-and-forget）：对列表未回传 participants 的群按详情逐个拉取，
  // 逐群合并进 sessionGroups（不整替整行，避免覆盖列表的 membership 等视图字段）。
  // 失败也写入缓存占位（空名单），防止逐字防抖搜索对同一失败群反复请求；页面重挂载即重置。
  const enrichParticipants = useCallback(
    async (list: GroupView[], viewBotId?: string) => {
      const cache = participantsCacheRef.current;
      const queue = list.filter(
        (g) => g.participants.length === 0 && g.participantCount > 0 && !cache.has(g.groupId),
      );
      if (queue.length === 0) return;
      const worker = async () => {
        for (;;) {
          const group = queue.shift();
          if (!group) return;
          let participants: ParticipantView[] | null = null;
          try {
            participants = await groupService.loadGroupParticipants(group.groupId, viewBotId);
          } catch {
            participants = null;
          }
          cache.set(group.groupId, participants ?? []);
          if (participants && participants.length > 0) {
            setSessionGroups((current) =>
              current.map((g) => (g.groupId === group.groupId ? { ...g, participants } : g)),
            );
          }
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(PARTICIPANTS_ENRICH_CONCURRENCY, queue.length) }, () => worker()),
      );
    },
    [setSessionGroups],
  );

  const reloadSelectedGroup = useCallback(
    async (groupId?: string) => {
      const gid = groupId ?? selectedGroupId;
      if (!gid) return;
      const res = await groupService.loadGroupDetailOrBcs(gid, activeIdentityId ?? undefined);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return;
      }
      // 管理面板改动成员后的新鲜名单同步进头像堆缓存，避免后续列表播种用回旧名单。
      if (res.data.participants.length > 0) {
        participantsCacheRef.current.set(gid, res.data.participants);
      }
      setSessionGroups((current) =>
        current.some((g) => g.groupId === gid)
          ? current.map((g) => (g.groupId === gid ? res.data : g))
          : [res.data, ...current],
      );
    },
    [activeIdentityId, selectedGroupId, setSessionGroups],
  );

  const dissolveGroup = useCallback(
    async (groupId: string) => {
      const group = sessionGroups.find((g) => g.groupId === groupId) ?? null;
      const permission = groupService.canDissolveGroup(group, useWorkspaceStore.getState().activeIdentityId);
      if (!permission.allowed) {
        toast.info(permission.disabledReason ?? '当前无权解散该协作群。');
        return;
      }
      const res = await groupService.dissolveGroup(groupId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return;
      }
      toast.success('协作群已解散');
      participantsCacheRef.current.delete(groupId);
      setSessionGroups((current) => current.filter((g) => g.groupId !== groupId));
      if (useWorkspaceStore.getState().selectedGroupId === groupId) {
        useWorkspaceStore.getState().selectGroup(null);
      }
    },
    [sessionGroups, setSessionGroups],
  );

  return { seedFromCache, enrichParticipants, reloadSelectedGroup, dissolveGroup };
}