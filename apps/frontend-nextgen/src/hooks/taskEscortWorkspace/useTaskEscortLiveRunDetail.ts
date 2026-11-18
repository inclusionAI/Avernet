import type { DomainRunDetail } from '@/domain/taskEscortWorkspace';
import { liveRunPresentation } from '@/services/taskEscortWorkspace/livePresentation';
import { useEffect, useState } from 'react';

interface DetailState {
  id: string | null;
  detail: DomainRunDetail | null;
  loading: boolean;
  error: boolean;
}
export function useTaskEscortLiveRunDetail(id: string | null, load: (id: string) => Promise<DomainRunDetail>) {
  const [state, setState] = useState<DetailState>({ id: null, detail: null, loading: false, error: false });
  const [attempt, retry] = useState(0);
  useEffect(() => {
    let active = true;
    setState({ id, detail: null, loading: !!id, error: false });
    if (id)
      void load(id).then(
        (detail) => {
          if (active) setState({ id, detail, loading: false, error: false });
        },
        () => {
          if (active) setState({ id, detail: null, loading: false, error: true });
        },
      );
    return () => {
      active = false;
    };
  }, [id, load, attempt]);
  const current = state.id === id ? state : { id, detail: null, loading: !!id, error: false };
  return {
    ...current,
    run: current.detail ? { ...current.detail.run, ...liveRunPresentation(current.detail.run) } : null,
    nodes:
      current.detail?.nodes.map((node) => ({ ...node, ...liveRunPresentation({ ...node, startedAt: null }) })) ?? [],
    retry: () => retry((value) => value + 1),
  };
}
