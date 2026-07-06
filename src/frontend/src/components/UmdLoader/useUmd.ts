import type { ComponentType } from 'react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { loadUmd } from './loadUmd';
import type { UseUmdOptions, UseUmdResult } from './types';

export function useUmd(options: UseUmdOptions): UseUmdResult {
  const { cdn, entry, dependencies, timeout, retryTimes, onUmdLoad, onReady } =
    options;
  const [state, setState] = useState<{ error: Error | null; loading: boolean }>(
    {
      error: null,
      loading: true,
    },
  );
  const componentRef = useRef<ComponentType<any> | null>(null);

  useLayoutEffect(() => {
    if (!cdn) {
      setState({ error: new Error('NO_CDN_ERROR'), loading: false });
      return;
    }
    componentRef.current = null;
    setState({ error: null, loading: true });

    let aborted = false;
    loadUmd({ cdn, entry, dependencies, timeout, retryTimes })
      .then((fn) => {
        if (aborted) return;
        componentRef.current = fn;
        setState({ error: null, loading: false });
      })
      .catch((err: Error) => {
        if (aborted) return;
        setState({ error: err, loading: false });
      });

    return () => {
      aborted = true;
    };
    // 仅在 cdn/entry 变化时重载，与底层缓存维度一致
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cdn, entry]);

  useEffect(() => {
    if (state.error === null && state.loading === false) {
      (onUmdLoad || onReady)?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return {
    Component: componentRef.current,
    error: state.error,
    loading: state.loading,
  };
}
