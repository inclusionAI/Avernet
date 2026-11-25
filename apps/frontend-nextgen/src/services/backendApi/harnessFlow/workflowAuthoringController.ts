/** Same-origin clawweb authoring contracts (fresh dev); no legacy upsert fallback. */
import { backendRequest } from '../httpClient';
export interface WorkflowAuthoringIdentityParams { user_id: string }
export interface WorkflowAuthoringModificationDto {
  requestId: string; mode: 'create' | 'modify'; prompt: string; currentYaml: string;
}
export interface WorkflowAuthoringUnifiedSaveDto {
  requestId: string; sourceVersion: 'draft' | 'current'; sourceCommit: string;
  expectedSavedHead: string | null; expectedDraftToken: string; specJson: string; changes: never[];
}
const BASE = '/openapi/v1/harnessflow/api';
const workflow = (id: string) => `/workflows/${encodeURIComponent(id)}`;
const drafts = (id: string) => `${workflow(id)}/ai-drafts`;
function send(path: string, method: 'GET' | 'POST' | 'PUT', identity: WorkflowAuthoringIdentityParams,
  signal?: AbortSignal, data?: unknown): Promise<unknown> {
  if (!identity.user_id?.trim()) throw new Error('缺少明确的用户身份');
  signal?.throwIfAborted();
  // Protected hosts forbid query keys other than their own documented selector.
  // Explicit user_id is bound to X-User-Id, never read from httpClient's store.
  return backendRequest(`${BASE}${path}`, { method, injectUserId: false, retryOnTransient: false, signal,
    headers: { 'X-User-Id': identity.user_id, 'Cache-Control': 'no-store', 'X-Pack-Version-CSRF': '1' },
    ...(data === undefined ? {} : { data }),
  });
}
export const workflowAuthoringController = {
  workflow: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send(workflow(id), 'GET', identity, signal),
  access: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send(`${workflow(id)}/access`, 'GET', identity, signal),
  draft: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send(`${workflow(id)}/editor-draft`, 'GET', identity, signal),
  packVersion: (id: string, version: 'draft' | 'current', identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`${workflow(id)}/pack-version?version=${version}`, 'GET', identity, signal),
  unifiedCapability: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send(`${workflow(id)}/unified-save`, 'GET', identity, signal),
  unifiedSave: (id: string, input: WorkflowAuthoringUnifiedSaveDto, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`${workflow(id)}/unified-save`, 'POST', identity, signal, input),
  create: (input: { workflowId: string; packId: string; spec: Record<string, unknown>; createOnly: true; expectedSavedHead: null; requestId: string },
    identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send('/auto-heal/save', 'POST', identity, signal, input),
  deploymentState: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${encodeURIComponent(id)}`, 'GET', identity, signal),
  publish: (input: { workflowId: string; packId: string; requestId: string; candidateCommit: string; expectedDraftToken: string;
    expectedActiveCommit: string; snapshotProtocol: 'saved-tree-v2'; confirmedFileDiff: true; autoRun: false; note?: string },
    identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send('/auto-heal/editor-deploy/snapshot-v2', 'POST', identity, signal, input),
  validate: (input: { spec: Record<string, unknown> }, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send('/workflows/validate', 'POST', identity, signal, input),
  cloneCandidates: (identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send('/workflows/clone-candidates', 'GET', identity, signal),
  clonePreview: (sourceWorkflowId: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send('/workflows/clone/preview', 'POST', identity, signal, { sourceWorkflowId }),
  clone: (input: { sourceWorkflowId: string; targetWorkflowId: string; targetTitle: string; facade?: { command?: string; remark?: string } },
    identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send('/workflows/clone', 'POST', identity, signal, input),
  capabilities: (id: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) => send(`${drafts(id)}/capabilities`, 'GET', identity, signal),
  prepareModification: (id: string, input: WorkflowAuthoringModificationDto, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`${drafts(id)}/prepare`, 'POST', identity, signal, input),
  startModification: (id: string, input: WorkflowAuthoringModificationDto & { receipt: string }, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(drafts(id), 'POST', identity, signal, input),
  pollModification: (id: string, draftId: string, context: { receipt: string; currentYaml: string }, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`${drafts(id)}/${encodeURIComponent(draftId)}/poll`, 'POST', identity, signal, context),
  cancelModification: (id: string, draftId: string, receipt: string, identity: WorkflowAuthoringIdentityParams, signal?: AbortSignal) =>
    send(`${drafts(id)}/${encodeURIComponent(draftId)}/cancel`, 'POST', identity, signal, { receipt }),
};
export type WorkflowAuthoringController = typeof workflowAuthoringController;
