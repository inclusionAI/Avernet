import type { IdentityView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useMemo } from 'react';

export interface WorkIdentityAccess {
  activeIdentity: IdentityView | null;
  activeIdentityKind: 'user' | 'bot' | null;
}

/**
 * 全局工作身份访问（供尚未迁移到模块级身份的菜单读取，如我的任务）。
 *
 * 2026-09-23 发现菜单（协作广场）已迁移至模块级身份（useSquareIdentity），
 * canViewPublicGroups 判定与 /collaboration-square/groups 的 Bot 重定向随之退场；
 * 左上角全局选择器废弃后本 hook 将随最后消费方一并移除。
 */
export function getWorkIdentityAccess(activeIdentity: IdentityView | null): WorkIdentityAccess {
  return {
    activeIdentity,
    activeIdentityKind: activeIdentity?.kind ?? null,
  };
}

export function useWorkIdentityAccess(): WorkIdentityAccess {
  const activeIdentity = useWorkspaceStore((state) =>
    state.activeIdentityId ? state.identities.find((identity) => identity.id === state.activeIdentityId) ?? null : null,
  );

  return useMemo(() => getWorkIdentityAccess(activeIdentity), [activeIdentity]);
}
