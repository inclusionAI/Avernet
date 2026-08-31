import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import { userScopedParams } from './botController';

export interface BotEditorDto {
  id: number;
  user_id: string;
  user_name?: string | null;
  role: 'admin' | 'member';
}

const path = (botId: string) => `/openapi/v1/bots/${encodeURIComponent(botId)}/editors`;

export const botCollaborationController = {
  list: (botId: string) =>
    backendRequest<BackendApiEnvelope<{ total: number; items: BotEditorDto[] }>>(path(botId), {
      params: userScopedParams(),
    }),
  add: (botId: string, editorUserId: string, role: BotEditorDto['role']) =>
    backendRequest<BackendApiEnvelope<BotEditorDto>>(path(botId), {
      method: 'POST',
      params: userScopedParams(),
      data: { editor_user_id: editorUserId, role },
    }),
  update: (botId: string, editorId: number, role: BotEditorDto['role']) =>
    backendRequest<BackendApiEnvelope<BotEditorDto>>(`${path(botId)}/${editorId}`, {
      method: 'PATCH',
      params: userScopedParams(),
      data: { role },
    }),
  remove: (botId: string, editorId: number) =>
    backendRequest(`${path(botId)}/${editorId}`, { method: 'DELETE', params: userScopedParams() }),
  requestAccess: (botId: string, ownerId: string, reason: string) =>
    backendRequest<BackendApiEnvelope<{ work_order_id: number; work_order_no: string; status: string }>>(
      `/openapi/v1/bots/${encodeURIComponent(botId)}/editor-requests`,
      { method: 'POST', params: userScopedParams({ owner_id: ownerId }), data: { reason } },
    ),
};
