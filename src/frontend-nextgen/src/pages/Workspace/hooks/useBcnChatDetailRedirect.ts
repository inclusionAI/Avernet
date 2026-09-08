import { isSameHumanIdentity } from '@/domain/userIdentity';
import { groupService } from '@/services/workspace/groupService';
import { history, useSearchParams } from '@umijs/max';
import { useEffect, useState } from 'react';

export type BcnChatDetailStatus = 'redirecting' | 'invalid';

/**
 * 参与方式判定：bot_uuid 命中群固定成员名册（loadGroupDetail participants）→ direct，
 * 否则 session_only。以下情形返回 null（降级为不带 membership 参数，
 * 由 workspace useSelectedGroupDetail 在列表加载完成后自动纠正视角）：
 * - bot_uuid 缺失；
 * - `bcs_grp_` 前缀的 BCS 群（交由 workspace BCS 前缀路由承接）；
 * - 群详情接口失败。
 */
async function resolveMembership(groupId: string, botUuid: string | null): Promise<'direct' | 'session_only' | null> {
  if (!botUuid || groupId.startsWith('bcs_grp_')) return null;
  try {
    const res = await groupService.loadGroupDetail(groupId);
    if (!res.ok) return null;
    const inRoster = (res.data.participants ?? []).some(
      (p) => p.actorId === botUuid || isSameHumanIdentity(p.actorId, botUuid, p.kind),
    );
    return inRoster ? 'direct' : 'session_only';
  } catch {
    return null;
  }
}

/**
 * useBcnChatDetailRedirect —— BCN 外链落地（/workspace/bcn/chat/detail?id=&bot_uuid=&session=）：
 * 判定参与方式后 history.replace 到 workspace 协作群深链
 * （?tab=group&group=&session=[&bot=][&membership=]）。bot_uuid 透传为 bot= 参数，
 * 由 workspace 首次外链同步按其定位视角身份（Bot/用户均可，未命中退回用户身份）。
 * 落地页不进历史栈（replace）。
 */
export function useBcnChatDetailRedirect(): { status: BcnChatDetailStatus } {
  const [searchParams] = useSearchParams();
  const groupId = searchParams.get('id')?.trim() || null;
  const botUuid = searchParams.get('bot_uuid')?.trim() || null;
  const sessionId = searchParams.get('session')?.trim() || null;
  const [status] = useState<BcnChatDetailStatus>(groupId && sessionId ? 'redirecting' : 'invalid');

  useEffect(() => {
    if (!groupId || !sessionId) return;
    let cancelled = false;
    void (async () => {
      const params = new URLSearchParams({ tab: 'group', group: groupId, session: sessionId });
      // bot_uuid 透传为 bot=：workspace 群深链首次同步按它定位视角身份（命中 store 身份则
      // 以该身份打开，含 Bot 角色；未命中退回用户身份）。缺失时不带 bot=，走原用户身份路径。
      if (botUuid) params.set('bot', botUuid);
      const membership = await resolveMembership(groupId, botUuid);
      if (membership) params.set('membership', membership);
      if (cancelled) return;
      history.replace(`/workspace?${params.toString()}`);
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, botUuid, sessionId]);

  return { status };
}
