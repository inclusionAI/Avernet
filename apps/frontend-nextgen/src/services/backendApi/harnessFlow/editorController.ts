/** Verified wire contracts only. Domain mapping and authorization interpretation live in Service. */
import { backendRequest } from '../httpClient';

export interface EditorIdentityParams {
  user_id?: string;
}
export interface EditorSpecDto extends Record<string, unknown> {
  id: string;
  title: string;
  version?: string | number;
  nodes: Array<Record<string, unknown> & { id: string; executor: Record<string, unknown> & { type: string } }>;
}
export interface EditorAccessDto {
  workflowId: string;
  canView: boolean;
  canEdit: boolean;
}
export interface EditorHistoryDto {
  workflowId: string;
  history: unknown[];
  active: unknown | null;
}
export interface EditorSnapshotDto extends Record<string, unknown> {
  deployNumber: number;
  version: number;
  action: string;
  specJson: string;
}
export interface EditorDiffDto {
  workflowId: string;
  from: EditorSnapshotDto;
  to: EditorSnapshotDto;
}
export interface EditorPackDto {
  workflowId: string;
  packId: string;
  baseRef: string;
  baseCommit: string;
  files: Array<{ path: string; content: string; mode: '100644' | '100755' }>;
}
const path = (id: string) => `/openapi/v1/harnessflow/api/workflows/${encodeURIComponent(id)}`;
function get<T>(url: string, params: object, signal?: AbortSignal, noStore = false): Promise<T> {
  return backendRequest<T>(url, {
    method: 'GET',
    params: { ...params },
    signal,
    injectUserId: false,
    ...(noStore ? { headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } } : {}),
  });
}
export const editorController = {
  workflow: (id: string, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorSpecDto>(path(id), params, signal),
  access: (id: string, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorAccessDto>(`${path(id)}/access`, params, signal),
  history: (id: string, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorHistoryDto>(`${path(id)}/history`, { ...params, limit: 20, releaseOnly: true }, signal),
  snapshot: (id: string, deploy: number, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorSnapshotDto>(`${path(id)}/history/by-deploy/${deploy}`, params, signal),
  diff: (id: string, from: number, to: number, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorDiffDto>(`${path(id)}/history/diff`, { ...params, fromDeploy: from, toDeploy: to }, signal),
  scripts: (id: string, params: EditorIdentityParams, signal?: AbortSignal) =>
    get<EditorPackDto>(`${path(id)}/pack-scripts`, params, signal, true),
};
export type EditorController = typeof editorController;
