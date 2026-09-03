// 当前登录 human 身份收口 Hook。替代 AccountBadge 写死的「张三」。
//
// 设计依据 docs/specs/2026-08-21-account-badge-real-identity/plan.md：
// - Open Core 默认：identityService.loadIdentities() 拉 listMyBots human[0] 写 workspaceStore；
//   getHumanIdentity() 读 store me 映射 HumanIdentity。
// - 内部 overlay：extensions/internal.ts 覆盖 getHumanIdentity 读内部身份源。
// - 本 hook 纯读 capability + service 访问器，不直接 fetch；单飞复用 identityService。
// - oauth-provider（阿里云）：订阅 externalAuthStore（/auth/user 与 mine 并跑，auth 常更晚落位），
//   登录后晚到的正确身份即刷出，不等业务偶发 re-render。
// Hook ≤ 150 行（AGENTS.md store 约束）。
import type { HumanIdentity } from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import { identityService } from '@/services/workspace/identityService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useState } from 'react';

export type HumanIdentityStatus = 'loading' | 'ready' | 'error';

export interface UseHumanIdentityResult {
  identity: HumanIdentity | null;
  status: HumanIdentityStatus;
  error?: string;
}

/** 读取当前 capability + service 状态推导结果。 */
function computeCurrent(): UseHumanIdentityResult {
  const r = getCapabilities().getHumanIdentity();
  if (r.value) return { identity: r.value, status: 'ready' };
  // value null：正在加载 → loading；曾成功且非加载 → 空 me 兜底 ready（合成「我」）；
  // 从未成功且非加载 → error
  if (identityService.isIdentityLoading()) return { identity: null, status: 'loading' };
  if (identityService.isIdentityResolved()) {
    // 曾成功但当前 capability 返回 null（合成 me 被清等极端情况）→ 兜底 ready 占位
    return { identity: null, status: 'ready' };
  }
  return { identity: null, status: 'error', error: '身份未加载' };
}

function shallowEqual(a: UseHumanIdentityResult, b: UseHumanIdentityResult): boolean {
  return (
    a.status === b.status &&
    a.error === b.error &&
    !!a.identity === !!b.identity &&
    a.identity?.userId === b.identity?.userId &&
    a.identity?.displayName === b.identity?.displayName &&
    a.identity?.avatarUrl === b.identity?.avatarUrl &&
    a.identity?.online === b.identity?.online
  );
}

/**
 * 当前登录用户身份。挂载时若 capability 返回 null 且未在加载，触发 identityService 单飞；
 * 解析完成后写 workspaceStore，capability 重读即拿到。
 */
export function useHumanIdentity(): UseHumanIdentityResult {
  const [result, setResult] = useState<UseHumanIdentityResult>(() => computeCurrent());

  // identities 变化时重算（Open Core 路径 store 写入后触发）
  const identities = useWorkspaceStore((s) => s.identities);
  // oauth-provider 路径：/auth/user（checkAuth）与 mine 并跑且常更晚返回；capability 契约规定
  // externalAuthStore.user 优先。订阅该 userId 让「登录后晚到的正确身份」触发重算与守卫重入，
  // 而非依赖业务引发的偶发 re-render（此前需切 tab 才刷出正确头像/花名）。
  const oauthUserId = useExternalAuthStore((s) => s.user?.userId ?? null);

  useEffect(() => {
    // capability 已有值（含内部 overlay 直接命中）→ 无需触发加载
    if (getCapabilities().getHumanIdentity().value) return;
    // 已在加载 → 等单飞完成后 store 变化自然重算
    if (identityService.isIdentityLoading()) return;
    // 曾成功加载过但 store 空（被清空）→ 不重复触发，避免循环
    if (identityService.isIdentityResolved()) return;

    // 未就绪且未在加载 → 触发单飞
    let cancelled = false;
    setResult((prev) => (prev.status === 'loading' ? prev : { identity: null, status: 'loading' }));
    void identityService.loadIdentities().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        useWorkspaceStore.getState().setIdentities(res.data.identities, res.data.defaultActiveId);
      } else {
        setResult({ identity: null, status: 'error', error: res.error.friendlyMessage });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [identities.length, oauthUserId]);

  // identities 变化后重算（对齐 Open Core 路径 store 写入）
  const recomputed = computeCurrent();
  // 仅在 status/identity 实质变化时更新，避免 identities 引用变化导致抖动
  if (!shallowEqual(recomputed, result)) {
    setResult(recomputed);
  }

  return result;
}
