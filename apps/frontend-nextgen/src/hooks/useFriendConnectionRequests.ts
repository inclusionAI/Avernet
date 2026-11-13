import { notifySuccess } from '@/components/ui/notify';
import type { FriendRequestDecision, FriendRequestDirection, FriendRequestPages, FriendRequestSnapshot } from '@/domain/collaborationSquare/friendRequests';
import { sameFriendActor } from '@/domain/collaborationSquare/friendRequests';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import { friendConnectionRequestService } from '@/services/collaborationSquare/friendConnectionRequestService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useCollaborationActorOperationScope } from './useCollaborationActorOperationScope';

function inlineError(error: unknown): string {
  const failure = error as { message?: string; toastKey?: string };
  if (failure.toastKey) useErrorNotifyStore.getState().cancel(failure.toastKey);
  return failure.message || '请求失败，请重试';
}

/** 仅弹窗挂载时活跃；身份/卸载代次守卫阻止旧读写结果跨身份污染。 */
export function useFriendConnectionRequests(actor: FriendRequestActor, onChanged: () => void) {
  const captureScope = useCollaborationActorOperationScope(JSON.stringify([actor.type, actor.id]));
  const [data, setData] = useState<FriendRequestSnapshot | null>(null);
  const dataRef = useRef<FriendRequestSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const loadingRef = useRef(true);
  const [error, setError] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const pages = useRef<FriendRequestPages>({ sent: 1, received: 1 });
  const readSequence = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const mutations = useRef(new Map<string, AbortController>());

  const refresh = useCallback(async () => {
    if (mutations.current.size) return;
    const isCurrentScope = captureScope();
    const sequence = ++readSequence.current;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const current = () => isCurrentScope() && sequence === readSequence.current && !controller.signal.aborted;
    loadingRef.current = true;
    dataRef.current = null;
    setLoading(true);
    setData(null);
    setError(null);
    setActionErrors({});
    try {
      const next = await friendConnectionRequestService.load({ type: actor.type, id: actor.id }, { ...pages.current }, controller.signal);
      if (!current()) return;
      dataRef.current = next;
      setData(next);
    } catch (failure) {
      if (current()) setError(inlineError(failure));
    } finally {
      if (current()) { loadingRef.current = false; setLoading(false); }
    }
  }, [actor.id, actor.type, captureScope]);

  useEffect(() => {
    pages.current = { sent: 1, received: 1 };
    setBusyIds([]);
    void refresh();
    const inFlight = mutations.current;
    return () => {
      readController.current?.abort();
      readSequence.current += 1;
      inFlight.forEach((controller) => controller.abort());
      inFlight.clear();
    };
  }, [refresh]);

  const goToPage = useCallback((direction: FriendRequestDirection, page: number) => {
    if (mutations.current.size || loadingRef.current || !Number.isInteger(page) || page < 1) return;
    pages.current = { ...pages.current, [direction]: page };
    void refresh();
  }, [refresh]);

  const decide = useCallback(async (requestId: string, decision: FriendRequestDecision) => {
    const item = dataRef.current?.received.items.find((entry) => entry.requestId === requestId);
    if (loadingRef.current || !item || item.status !== 'pending' || !sameFriendActor(item.toActor, actor) || mutations.current.has(requestId)) return;
    const isCurrent = captureScope();
    const controller = new AbortController();
    mutations.current.set(requestId, controller);
    setBusyIds([...mutations.current.keys()]);
    setActionErrors((previous) => { const next = { ...previous }; delete next[requestId]; return next; });
    try {
      const confirmed = await friendConnectionRequestService.decide(requestId, decision, controller.signal);
      if (!isCurrent() || controller.signal.aborted) return;
      if (!sameFriendActor(confirmed.toActor, item.toActor) || !sameFriendActor(confirmed.fromActor, item.fromActor)) {
        throw new Error('好友申请确认结果与当前申请不匹配，请刷新重试');
      }
      const current = dataRef.current;
      if (!current) return;
      // 保留已补齐的头像/名称，只应用服务端确认的状态字段。
      const next = { ...current, received: { ...current.received, items: current.received.items.map((entry) =>
        entry.requestId === requestId ? { ...entry, status: confirmed.status,
          decisionReason: confirmed.decisionReason, decidedAt: confirmed.decidedAt } : entry) } };
      dataRef.current = next;
      setData(next);
      notifySuccess(decision === 'accept' ? '好友申请已通过' : '好友申请已拒绝');
      onChanged();
    } catch (failure) {
      if (isCurrent() && !controller.signal.aborted) setActionErrors((previous) => ({ ...previous, [requestId]: inlineError(failure) }));
    } finally {
      if (mutations.current.get(requestId) === controller) {
        mutations.current.delete(requestId);
        if (isCurrent()) setBusyIds([...mutations.current.keys()]);
      }
    }
  }, [actor.id, actor.type, captureScope, onChanged]);

  return { data, loading, error, actionErrors, busyIds, refresh, goToPage, decide };
}
