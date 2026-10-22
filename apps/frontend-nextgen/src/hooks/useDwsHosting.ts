import type { HostingConfig, HostingConversations, HostingSettings, HostingStatus } from '@/domain/dwsHosting';
import { dwsHostingService as api } from '@/services/botWorkshop/dwsHostingService';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useDwsHosting(botId: string) {
  const [config, setConfig] = useState<HostingConfig>();
  const [draft, setDraft] = useState<HostingSettings>();
  const [status, setStatus] = useState<HostingStatus>();
  const [conversations, setConversations] = useState<HostingConversations>();
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const acting = useRef(false);
  const dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(config?.settings);
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const next = await api.status(botId, signal);
      if (alive.current && !signal?.aborted) setStatus(next);
    },
    [botId],
  );
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      try {
        const saved = await api.settings(botId, signal);
        if (!alive.current || signal?.aborted) return;
        setConfig(saved);
        setDraft(saved.settings);
        await refresh(signal);
      } catch (e) {
        if (alive.current && !signal?.aborted) setError(e instanceof Error ? e.message : '加载失败');
      } finally {
        if (alive.current && !signal?.aborted) setLoading(false);
      }
    },
    [botId, refresh],
  );
  useEffect(() => {
    alive.current = true;
    const abort = new AbortController();
    void load(abort.signal);
    return () => {
      alive.current = false;
      abort.abort();
    };
  }, [load]);
  useEffect(() => {
    if (!status || (!status.enabled && !['starting', 'pending'].includes(status.authorization))) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh(abort.signal);
      } catch {
        if (!abort.signal.aborted && alive.current) setError('状态更新失败，请刷新确认');
      }
      if (!abort.signal.aborted) timer = setTimeout(poll, 3000);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [refresh, status?.enabled, status?.authorization]);
  const act = async (work: () => Promise<unknown>) => {
    if (acting.current) return;
    acting.current = true;
    setBusy(true);
    setError('');
    try {
      await work();
      await refresh();
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      acting.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const list = async (next: number) => {
    const result = await api.conversations(botId, next);
    if (alive.current) {
      setConversations(result);
      setOffset(next);
    }
  };
  return {
    config,
    draft,
    setDraft,
    status,
    loading,
    busy,
    error,
    dirty,
    conversations,
    offset,
    reload: () => void load(),
    save: () =>
      act(async () => {
        if (!draft || !config) return;
        const saved = await api.save(botId, config.version, draft);
        if (alive.current) {
          setConfig(saved);
          setDraft(saved.settings);
        }
      }),
    enable: (value: boolean) => act(() => api.enable(botId, config?.version ?? 0, value)),
    authorize: () => act(() => api.authorize(botId)),
    check: () => act(() => api.check(botId)),
    list: (next: number) => act(() => list(next)),
    takeover: (id: string, value: boolean) =>
      act(async () => {
        await api.takeover(botId, id, value);
        await list(offset);
      }),
  };
}
