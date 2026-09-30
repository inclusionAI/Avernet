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

export function useBotRegistrationToken(open: boolean): UseBotRegistrationTokenResult {
  const [requestSeq, setRequestSeq] = useState(0);
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
    setIsLoading(true);
    setError(null);

    void botRegistrationService.getRegistrationToken(controller.signal).then((result) => {
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
  }, [open, requestSeq]);

  return { token, expiresAt, note, isLoading, error, bcsEndpoint, retry };
}
