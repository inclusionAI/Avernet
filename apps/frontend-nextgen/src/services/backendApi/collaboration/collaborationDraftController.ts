import type { CollaborationDraftReference } from '@/domain/collaborationDraft';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface StoredCollaborationDraft {
  draft_id: string;
  revision: number;
  definition_yaml: string;
  title?: string | null;
  content_hash: string;
  size_bytes: number;
}

export function readCollaborationDraft(reference: CollaborationDraftReference, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<StoredCollaborationDraft>>(
    `/api/v1/collaboration/drafts/${reference.draftId}/revisions/${reference.revision}`,
    { method: 'GET', injectUserId: false, signal },
  );
}
