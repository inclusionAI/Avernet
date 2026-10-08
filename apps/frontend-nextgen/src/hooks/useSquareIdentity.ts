import type { IdentityView } from '@/domain/collaboration';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { identityService } from '@/services/workspace/identityService';
import { applyIdentityLoadResult } from '@/services/workspace/identityStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useState } from 'react';

/**
 * 发现菜单（协作广场）模块级工作身份 Hook。
 *
 * 合同：docs/specs/2026-09-23-discovery-tab-identity/spec.md
 * - 身份列表复用 identityService（经 useHumanIdentity 单飞触发，只读，不写全局激活身份）；
 * - selectedIdentityId = null → 登录用户身份（默认）；
 * - 持久化身份失效（不在列表中，如 Bot 被移除）→ 自动回退登录用户身份（AC-5）；
 * - listStatus 覆盖 loading / ready / error；失败态提供页内重试（AC-6，PR #428 评审 P2）。
 */
export type SquareIdentityListStatus = 'loading' | 'ready' | 'error';

export interface SquareIdentity {
  /** 身份列表（登录用户 + 我的 Bot），来自 identityService 加载结果。 */
  identities: IdentityView[];
  listStatus: SquareIdentityListStatus;
  /** null = 登录用户身份（默认）。 */
  selectedIdentityId: string | null;
  /** 选中身份视图：null 时映射列表中用户身份项；列表未就绪时为 null。 */
  selectedIdentity: IdentityView | null;
  /** 列表中用户身份项：公开协作群/任务广场固定登录用户身份时使用，不受模块级选择影响。 */
  userIdentity: IdentityView | null;
  selectIdentity: (id: string | null) => void;
  /** 页内重试：重新拉取身份列表并写回 store；重试期间 listStatus 为 loading。 */
  retryLoadIdentities: () => Promise<void>;
}

export function useSquareIdentity(): SquareIdentity {
  const identities = useWorkspaceStore((state) => state.identities);
  const selectedIdentityId = useSquareIdentityStore((state) => state.selectedIdentityId);
  const selectIdentity = useSquareIdentityStore((state) => state.selectIdentity);
  const { status: humanStatus } = useHumanIdentity();
  const [retryLoading, setRetryLoading] = useState(false);

  // 失效校验：列表就绪后，持久化的身份 id 不在列表中 → 回退默认（登录用户身份）。
  // 列表为空（未加载）时不校验，避免加载完成前误清。
  useEffect(() => {
    if (selectedIdentityId === null) return;
    if (identities.length === 0) return;
    if (!identities.some((identity) => identity.id === selectedIdentityId)) {
      selectIdentity(null);
    }
  }, [identities, selectedIdentityId, selectIdentity]);

  const retryLoadIdentities = useCallback(async () => {
    setRetryLoading(true);
    try {
      // identityService 单飞：重试与首次加载共用去重机制；成功经 applyIdentityLoadResult
      // 写回 store（与 useHumanIdentity 相同链路），失败保持 error 态可再次重试。
      const result = await identityService.loadIdentities();
      if (result.ok) applyIdentityLoadResult(result.data);
    } finally {
      setRetryLoading(false);
    }
  }, []);

  const listStatus: SquareIdentityListStatus = retryLoading
    ? 'loading'
    : identities.length > 0 || humanStatus === 'ready'
    ? 'ready'
    : humanStatus === 'error'
    ? 'error'
    : 'loading';

  const userIdentity = identities.find((identity) => identity.kind === 'user') ?? null;
  const selectedIdentity =
    selectedIdentityId === null
      ? userIdentity
      : identities.find((identity) => identity.id === selectedIdentityId) ?? null;

  return {
    identities,
    listStatus,
    selectedIdentityId,
    selectedIdentity,
    userIdentity,
    selectIdentity,
    retryLoadIdentities,
  };
}
