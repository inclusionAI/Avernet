/**
 * The stored agent-definition library.
 *
 * Unlike the run list this does not poll: a definition only changes when the
 * user changes it, so each mutation refreshes explicitly. Keeping it unpolled
 * also means a slow gateway cannot silently overwrite the library while the
 * user is editing a row.
 */
import { useCallback, useEffect, useState } from 'react';
import { listDefinitions } from '@/services/agentswarm/service';
import type { DefinitionDTO } from '@/services/agentswarm/types';

export interface UseDefinitionsResult {
  definitions: DefinitionDTO[];
  total: number;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useDefinitions(tenant: string | undefined, keyword = ''): UseDefinitionsResult {
  const [definitions, setDefinitions] = useState<DefinitionDTO[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((current) => current + 1), []);

  useEffect(() => {
    if (!tenant) {
      setDefinitions([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    const load = async () => {
      try {
        const result = await listDefinitions(tenant, keyword ? { name: keyword } : {});
        if (cancelled) return;
        setDefinitions(result.items ?? []);
        setTotal(result.total ?? 0);
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : '定义库加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [tenant, keyword, nonce]);

  return { definitions, total, loading, error, refresh };
}