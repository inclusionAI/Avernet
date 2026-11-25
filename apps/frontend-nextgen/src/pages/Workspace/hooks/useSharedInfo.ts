import { listSharedInfo } from '@/services/workspace/groupContextService';
import type { GroupContextView, SharedInfoItem } from '@/services/workspace/groupContextService';
import { useEffect, useState } from 'react';

export function useSharedInfo(view: GroupContextView) {
  const { groupId, sessionId, view_bot_id: botId } = view;
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<SharedInfoItem[] | null>();

  useEffect(() => {
    const controller = new AbortController();
    listSharedInfo({ groupId, sessionId, view_bot_id: botId }, controller.signal).then(
      ({ items }) => {
        if (!controller.signal.aborted) setResult(Array.isArray(items) ? items : null);
      },
      () => {
        if (!controller.signal.aborted) setResult(null);
      },
    );
    return () => controller.abort();
  }, [groupId, sessionId, botId, attempt]);

  function reload() {
    setResult(undefined);
    setAttempt((previous) => previous + 1);
  }

  return { result, reload };
}
