/** 按 TaskPanel 中的协作群节点拉取群详情，提取成员名列表；失败静默降级为既有展示。 */
import { fetchGroupDetail, sortGroupMembers } from '../GroupDrillDown';
import type { GroupMemberInfo, GroupMemberInfoByGroupId } from '../groupDisplay';
import { useEffect, useMemo, useState } from 'react';

function toMemberInfo(detail: Awaited<ReturnType<typeof fetchGroupDetail>>): GroupMemberInfo | null {
  const participants = sortGroupMembers(detail?.participants ?? []);
  const nameByActorId: Record<string, string> = {};
  for (const participant of participants) {
    if (participant.actor_id && participant.name) nameByActorId[participant.actor_id] = participant.name;
  }
  const names = [...new Set(participants.map((participant) => participant.name).filter(Boolean))];
  return Object.keys(nameByActorId).length || names.length ? { names, nameByActorId } : null;
}

export function useGroupMemberNames(groupIds: string[], bcsBaseUrl = ''): GroupMemberInfoByGroupId {
  const [state, setState] = useState<{ groupIdsKey: string; groups: GroupMemberInfoByGroupId }>({
    groupIdsKey: '',
    groups: {},
  });
  const groupIdsKey = useMemo(() => groupIds.join('\n'), [groupIds]);

  useEffect(() => {
    if (!groupIdsKey) {
      setState({ groupIdsKey, groups: {} });
      return undefined;
    }
    const ids = groupIdsKey.split('\n');
    let cancelled = false;
    void (async () => {
      const entries = await Promise.all(
        ids.map(async (id) => {
          try {
            const info = toMemberInfo(await fetchGroupDetail(id, bcsBaseUrl));
            return info ? ([id, info] as const) : null;
          } catch {
            return null;
          }
        }),
      );
      if (!cancelled) {
        setState({ groupIdsKey, groups: Object.fromEntries(entries.flatMap((entry) => (entry ? [entry] : []))) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupIdsKey, bcsBaseUrl]);

  return state.groupIdsKey === groupIdsKey ? state.groups : {};
}
