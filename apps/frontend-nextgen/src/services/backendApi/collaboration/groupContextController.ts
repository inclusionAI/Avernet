import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type GroupContextScope = 'group' | 'session' | 'round';

export interface SharedInfoItem {
  name: string;
  scope_level: GroupContextScope;
  scope: string;
  content: string;
  creator: string;
  gmt_modified: string;
}

export interface GroupContextPreset {
  name: string;
  description: string;
}

export interface GroupContextView {
  groupId: string;
  sessionId?: string;
  view_bot_id?: string;
}

// Browser-facing routes use Gateway session authentication. Gateway must
// forward the authenticated Human identity. BCS checks view_bot_id against
// that Human’s Bot authority and the requested Group or Session membership.
const path = '/openapi/v1/collaboration/groupcontext';

async function request<T>(operation: string, view: GroupContextView, data: object, signal?: AbortSignal) {
  const response = await backendRequest<BackendApiEnvelope<T>>(`${path}/${operation}`, {
    method: 'POST',
    params: { ...view },
    data,
    signal,
    injectUserId: false,
  });
  if (response.data == null) {
    throw new Error(response.message ?? '接口返回为空');
  }
  return response.data;
}

export function listSharedInfo(view: GroupContextView, signal: AbortSignal) {
  return request<{ items: SharedInfoItem[] }>('shared-info', view, {}, signal);
}

export function listPresets(view: GroupContextView, signal: AbortSignal) {
  if (view.view_bot_id) {
    return request<{ presets: GroupContextPreset[]; can_manage?: never }>('list-preset', view, {}, signal);
  }
  return request<{ presets: GroupContextPreset[]; can_manage: boolean }>('manage/list-preset', view, {}, signal);
}

export function addPreset(groupId: string, preset: GroupContextPreset) {
  return request('manage/add-preset', { groupId }, preset);
}

export function updatePreset(groupId: string, name: string, preset: GroupContextPreset) {
  return request('manage/update-preset', { groupId }, { name, new_name: preset.name, description: preset.description });
}

export function deletePreset(groupId: string, name: string) {
  return request('manage/delete-preset', { groupId }, { name });
}
