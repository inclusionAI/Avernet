import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { createRoutineClient, type RoutineLock } from '@/services/botScheduledTasks/service';
import type { ManagedRoutine, RoutineContext, RoutineForm } from '@/services/botScheduledTasks/types';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export function useScheduledTasks(botId: string, ownerId?: string) {
  const identity = useBotWorkshopRequestIdentity();
  const [context, setContext] = useState<RoutineContext>();
  const [tasks, setTasks] = useState<ManagedRoutine[]>([]);
  const [failures, setFailures] = useState<string[]>([]);
  const [lock, setLock] = useState<RoutineLock>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const requestId = useRef(0);
  const client = useMemo(
    () => createRoutineClient(botId, ownerId, identity.userId || ''),
    [botId, ownerId, identity.userId],
  );
  const load = useCallback(async () => {
    if (!identity.ready || !botId) return;
    const id = ++requestId.current;
    setLoading(true);
    setError('');
    try {
      const next = await client.context();
      if (id !== requestId.current) return;
      setContext(next);
      const result = await client.list();
      if (id !== requestId.current) return;
      setTasks(result.items);
      setFailures(result.failures.map((item) => item.message || '部分环境任务加载失败'));
    } catch (reason) {
      if (id === requestId.current) setError(reason instanceof Error ? reason.message : '加载失败');
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [botId, client, identity.ready]);
  const refreshLock = useCallback(async () => {
    try {
      const value = await client.getLock();
      setLock(value);
      return value;
    } catch (reason) {
      setLock(undefined);
      throw reason;
    }
  }, [client]);
  useEffect(() => {
    setContext(undefined);
    setTasks([]);
    setLock(undefined);
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);
  useEffect(() => {
    if (!identity.ready || !botId || !context?.can_manage) return;
    let active = true;
    const check = () => {
      if (active)
        void refreshLock().catch(() => {
          if (active) setLock(undefined);
        });
    };
    check();
    const timer = setInterval(check, 30000);
    window.addEventListener('focus', check);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener('focus', check);
    };
  }, [botId, identity.ready, context?.can_manage, refreshLock]);
  const ownsLock = useCallback(
    (value?: RoutineLock) =>
      Boolean(
        value &&
          ((value.need_lock === false && value.locked === false) ||
            (value.locked && value.holder_user_id === identity.userId)),
      ),
    [identity.userId],
  );
  const editable = Boolean(context?.can_manage && ownsLock(lock));
  const lockAction = async (action: 'acquire' | 'steal' | 'release') => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await client.lockAction(action);
      await refreshLock();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '编辑锁操作失败');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  async function mutate(work: () => Promise<unknown>) {
    if (busyRef.current) throw new Error('操作处理中，请稍后');
    busyRef.current = true;
    setBusy(true);
    try {
      const [nextContext, currentLock] = await Promise.all([client.context(), refreshLock()]);
      setContext(nextContext);
      if (!nextContext.can_manage) throw new Error('当前账号没有定时任务管理权限');
      if (!ownsLock(currentLock)) throw new Error('编辑锁已失效，请重新获取后重试。输入已保留');
      await work();
      await load();
    } catch (reason) {
      void refreshLock().catch(() => setLock(undefined));
      throw reason;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const loadModels = useCallback(() => botEditorService.listRoutineModels(botId, ownerId), [botId, ownerId]);
  return {
    identity,
    context,
    tasks,
    failures,
    lock,
    editable,
    error,
    loading: identity.loading || (identity.ready && loading),
    busy,
    client,
    load,
    lockAction,
    loadModels,
    save: (form: RoutineForm, original?: ManagedRoutine) => mutate(() => client.save(context!, form, original)),
    toggle: (task: ManagedRoutine) => mutate(() => client.toggle(context!, task)),
    remove: (task: ManagedRoutine) => mutate(() => client.remove(context!, task)),
    run: (task: ManagedRoutine) => mutate(() => client.run(context!, task)),
  };
}
