import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import { useCallback, useEffect, useMemo, useState } from 'react';

interface UseBotRegistrationTokenResult {
  token: string | null;
  expiresAt: number | null;
  note: string | null;
  isLoading: boolean;
  error: string | null;
  bcsEndpoint: string | null;
  retry: () => void;
}

export function useBotRegistrationToken(open: boolean, providerSlug?: string): UseBotRegistrationTokenResult {
  const [requestSeq, setRequestSeq] = useState(0);
  const [tokenScope, setTokenScope] = useState<string | undefined>(undefined);
  const [token, setToken] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bcsEndpoint = useMemo(() => resolveBcsEndpoint(), []);

  const retry = useCallback(() => setRequestSeq((seq) => seq + 1), []);

  useEffect(() => {
    if (!open) {
      setToken(null);
      setExpiresAt(null);
      setNote(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setToken(null);
    setExpiresAt(null);
    setNote(null);
    setTokenScope(providerSlug);
    setIsLoading(true);
    setError(null);

    void botRegistrationService.getRegistrationToken(controller.signal, providerSlug).then((result) => {
      if (cancelled) return;
      setIsLoading(false);
      if (result.ok) {
        setToken(result.data.token);
        setExpiresAt(result.data.expiresAt);
        setNote(result.data.note ?? null);
      } else {
        setToken(null);
        setExpiresAt(null);
        setNote(null);
        setError(result.error.friendlyMessage);
      }
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, requestSeq, providerSlug]);

  const matchesScope = tokenScope === providerSlug;
  return {
    token: matchesScope ? token : null,
    expiresAt: matchesScope ? expiresAt : null,
    note: matchesScope ? note : null,
    isLoading: open && (!matchesScope || isLoading),
    error: matchesScope ? error : null,
    bcsEndpoint,
    retry,
  };
}
