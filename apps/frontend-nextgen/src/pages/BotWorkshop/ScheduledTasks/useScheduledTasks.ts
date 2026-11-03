import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { createRoutineClient, type RoutineLock } from '@/services/botScheduledTasks/service';
import { addReportedTargets } from '@/services/botScheduledTasks/metadata';
import type {
  FailedTarget,
  ManagedRoutine,
  OperationResult,
  RoutineContext,
  RoutineForm,
  RoutineTarget,
} from '@/services/botScheduledTasks/types';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
export function useScheduledTasks(
  botId: string,
  ownerId: string | undefined,
  target: RoutineTarget,
  page: number,
  pageSize: number,
) {
  const identity = useBotWorkshopRequestIdentity();
  const client = useMemo(
    () => createRoutineClient(botId, ownerId, identity.userId || ''),
    [botId, ownerId, identity.userId],
  );
  const [context, setContext] = useState<RoutineContext>();
  const [tasks, setTasks] = useState<ManagedRoutine[]>([]);
  const [total, setTotal] = useState(0);
  const [failures, setFailures] = useState<FailedTarget[]>([]);
  const [lock, setLock] = useState<RoutineLock>();
  const [error, setError] = useState('');
  const [contextError, setContextError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<OperationResult>();
  const busyRef = useRef(false);
  const requestId = useRef(0);
  const contextRequestId = useRef(0);
  const currentClient = useRef(client);
  currentClient.current = client;
  const rowsRef = useRef<ManagedRoutine[]>([]);
  const stage = target.runtime_stage;
  const device = target.device_uuid;
  const operator = target.operator_id;
  const load = useCallback(async () => {
    if (!identity.ready || !botId) return;
    const id = ++requestId.current;
    setLoading(true);
    setError('');
    try {
      const result = await client.list(
        { runtime_stage: stage, device_uuid: device, operator_id: operator },
        page,
        pageSize,
      );
      if (id !== requestId.current) return;
      rowsRef.current = result.items;
      setTasks(result.items);
      setTotal(result.total);
      setFailures(result.failed_targets);
      setContext((current) => (current ? addReportedTargets(current, result.items, identity.userId) : current));
    } catch (reason) {
      if (id === requestId.current) setError(reason instanceof Error ? reason.message : '刷新失败');
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [botId, client, identity.ready, stage, device, operator, page, pageSize]);
  const refreshContext = useCallback(async () => {
    if (!identity.ready || !botId) return;
    const id = ++contextRequestId.current;
    setContextError('');
    try {
      const next = await client.context();
      if (id === contextRequestId.current) setContext(addReportedTargets(next, rowsRef.current, identity.userId));
    } catch (reason) {
      if (id === contextRequestId.current) {
        setContext(undefined);
        setContextError(reason instanceof Error ? reason.message : 'Bot 信息加载失败');
      }
    }
  }, [client, identity.ready, botId, identity.userId]);
  useEffect(() => {
    setContext(undefined);
    setLock(undefined);
    setLastResult(undefined);
    void refreshContext();
    return () => {
      contextRequestId.current += 1;
    };
  }, [refreshContext]);
  useEffect(() => {
    rowsRef.current = [];
    setTasks([]);
    setFailures([]);
    setTotal(0);
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);
  const refreshLock = useCallback(async () => {
    try {
      const value = await client.getLock();
      if (currentClient.current === client) setLock(value);
      return value;
    } catch (reason) {
      if (currentClient.current === client) setLock(undefined);
      throw reason;
    }
  }, [client]);
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
  async function mutate(work: (current: RoutineContext) => Promise<OperationResult>) {
    if (busyRef.current) throw new Error('操作处理中，请稍后');
    busyRef.current = true;
    setBusy(true);
    setLastResult(undefined);
    try {
      const [next, currentLock] = await Promise.all([client.context(), refreshLock()]);
      if (currentClient.current !== client) throw new Error('Bot 或登录身份已切换，请重新操作');
      setContext(addReportedTargets(next, rowsRef.current, identity.userId));
      if (!next.can_manage) throw new Error('当前账号没有定时任务管理权限');
      if (!ownsLock(currentLock)) throw new Error('编辑锁已失效，请重新获取后重试。输入已保留');
      const result = await work(next);
      setLastResult(result);
      await load();
      return result;
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
    total,
    failures,
    lock,
    editable,
    error: contextError || error,
    lastResult,
    loading: identity.loading || (identity.ready && loading),
    busy,
    client,
    load: async () => {
      await Promise.all([load(), refreshContext()]);
    },
    lockAction,
    loadModels,
    save: (form: RoutineForm, original?: ManagedRoutine) => mutate((current) => client.save(current, form, original)),
    toggle: (task: ManagedRoutine) => mutate((current) => client.toggle(current, task)),
    remove: (task: ManagedRoutine) => mutate((current) => client.remove(current, task)),
    run: (task: ManagedRoutine) => mutate((current) => client.run(current, task)),
  };
}
