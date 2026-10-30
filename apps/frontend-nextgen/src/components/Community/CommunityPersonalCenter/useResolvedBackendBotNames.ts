import { resolveBackendBotNames } from '@/services/community/botDisplayNames';
import { useEffect, useMemo, useRef, useState } from 'react';

type BotNameById = Record<string, string>;

/**
 * Hold bot-name resolution until the current page is resolved so the UI never
 * paints a raw bot ID first and then replaces it with the real name.
 */
export function useResolvedBackendBotNames(botIds: string[]) {
  const key = useMemo(() => botIds.join('\u0000'), [botIds]);
  const [resolved, setResolved] = useState<{ key: string; names: BotNameById } | null>(null);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!botIds.length) return;

    const requestId = ++requestRef.current;
    void resolveBackendBotNames(botIds).then((names) => {
      if (requestId !== requestRef.current) return;
      setResolved({ key, names });
    });
  }, [botIds, key, requestRef]);

  const names = resolved?.key === key ? resolved.names : {};
  return {
    names,
    loading: botIds.length > 0 && resolved?.key !== key,
  };
}
