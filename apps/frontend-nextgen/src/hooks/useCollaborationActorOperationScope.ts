import { useCallback, useLayoutEffect, useRef } from 'react';

/** 捕获一次操作时的代次快照：仍返回 true 才允许继续应用结果。 */
export type CollaborationOperationGuard = () => boolean;

/**
 * 当前操作角色／资源范围的代次守卫（bcs-standalone-friend-discovery Task 4）。
 *
 * scopeKey 由调用方以 JSON.stringify([登录 userId, resource, actor.type, actor.id]) 编码，
 * 避免易冲突的字符串拼接。useLayoutEffect 在角色提交时更新 key 并递增 epoch，
 * cleanup 再递增 epoch 并置 inactive：覆盖角色切换（A→B）、A→B→A 与卸载／重新挂载，
 * 相同 key 或仅检测 AbortError 都不足以放行旧代次的结果。
 */
export function useCollaborationActorOperationScope(scopeKey: string) {
  const scope = useRef({ key: scopeKey, epoch: 0, active: false });
  useLayoutEffect(() => {
    scope.current = { key: scopeKey, epoch: scope.current.epoch + 1, active: true };
    return () => {
      scope.current = { ...scope.current, epoch: scope.current.epoch + 1, active: false };
    };
  }, [scopeKey]);
  return useCallback(() => {
    const started = scope.current;
    return () =>
      scope.current.active && scope.current.key === started.key && scope.current.epoch === started.epoch;
  }, []);
}
