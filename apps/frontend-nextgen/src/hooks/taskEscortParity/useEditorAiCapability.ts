import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useState } from 'react';

/** Capability reads are identity-bound and cannot outlive the editor session. */
export function useEditorAiCapability(workflowId: string, userId: string | undefined) {
  const [capability, setCapability] = useState<{ available: boolean; reason?: string } | null>(null);
  useEffect(() => {
    const pending = new AbortController();
    setCapability(null);
    if (userId)
      void workflowAuthoringService
        .capabilities(workflowId, { user_id: userId }, pending.signal)
        .then((value) => {
          if (!pending.signal.aborted) setCapability(value);
        })
        .catch((cause: unknown) => {
          if (!pending.signal.aborted)
            setCapability({
              available: false,
              reason: cause instanceof Error ? cause.message : 'AI 能力读取失败，请重试',
            });
        });
    return () => pending.abort();
  }, [workflowId, userId]);
  return capability;
}
