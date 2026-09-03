import type { CreateGroupSessionFormValues } from '@/components/CollaborationSquare/CreateGroupSessionModal';
import { notifySuccess } from '@/components/ui/notify';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { collaborationSquareGroupService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { history } from '@umijs/max';
import { useCallback, useState } from 'react';

/**
 * 公开协作群「创建新会话」流程：弹窗状态 + 提交（调 service 创建并跳转 Workspace）。
 *
 * 拆自 useCollaborationSquare 以控 Hook 体积。Component → Hook → Service 分层：
 * 弹窗 UI 由 SquarePageShell 渲染，提交编排由本 Hook 拥有。
 */
export function useCreateGroupSessionFlow(
  humanBotContext: { actorId: string; userId: string } | null,
  runBusy: (key: string, task: () => Promise<void>, invalidTargetId?: string) => Promise<void>,
) {
  const store = useCollaborationSquareStore();
  const [target, setTarget] = useState<PublicGroup | null>(null);
  const isCreating = store.busyKeys.includes(`group:${target?.id ?? ''}`);

  const open = useCallback((group: PublicGroup) => {
    setTarget(group);
  }, []);

  const close = useCallback(() => {
    setTarget(null);
  }, []);

  const submit = useCallback(
    (values: CreateGroupSessionFormValues) => {
      const group = target;
      if (!group) return;
      void runBusy(`group:${group.id}`, async () => {
        const result = await collaborationSquareGroupService.createGroupSession(
          group.id,
          humanBotContext ?? undefined,
          { title: values.title, query: values.query },
        );
        notifySuccess('会话创建成功');
        setTarget(null);
        const params = new URLSearchParams({ tab: 'group', group: group.id, session: result.sessionId });
        if (result.memberSource) params.set('membership', 'session_only');
        if (result.defaultRole) params.set('defaultRole', result.defaultRole);
        history.push(`/workspace?${params.toString()}`);
      });
    },
    [humanBotContext, runBusy, target],
  );

  return { target, isCreating, open, close, submit };
}
