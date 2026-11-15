import type { IdentityView } from '@/domain/collaboration';
import type { CollaborationDraft, CollaborationDraftReference } from '@/domain/collaborationDraft';
import { loadCollaborationDraft } from '@/services/workspace/collaborationDraftService';
import { identityService } from '@/services/workspace/identityService';
import { applyIdentityLoadResult } from '@/services/workspace/identityStore';
import { useEffect, useState } from 'react';

export function useCollaborationDraftLaunch(reference: CollaborationDraftReference) {
  const [data, setData] = useState<{ draft: CollaborationDraft; bots: IdentityView[] } | null>(null);
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      // 身份加载是共享请求；超时后仅使本次加载失效，忽略迟到结果。
      cancelled = true;
      setError('读取协作版本和自有 Bot 超时，请重试');
      controller.abort();
    }, 15_000);
    setData(null);
    setError(undefined);
    void Promise.all([loadCollaborationDraft(reference, controller.signal), identityService.loadIdentities()])
      .then(([draft, identities]) => {
        if (cancelled) return;
        if (!draft.ok || !identities.ok) {
          setError(
            !draft.ok ? draft.error.friendlyMessage : !identities.ok ? identities.error.friendlyMessage : '加载失败',
          );
          return;
        }
        applyIdentityLoadResult(identities.data);
        setData({ draft: draft.data, bots: identities.data.identities.filter((identity) => identity.kind === 'bot') });
      })
      .catch(() => {
        if (!cancelled) setError('读取协作版本和自有 Bot 失败，请重试');
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [reference.draftId, reference.revision, attempt]);
  return { data, error, retry: () => setAttempt((value) => value + 1) };
}
