import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface CollaborationInvitationData {
  token: string;
  target_type: 'group' | 'session';
  target_id: string;
  state: 'pending' | 'accepted' | 'expired';
  created_at: number;
  expires_at?: number;
}
export interface CreateInvitationBody {
  expires_in_seconds?: number;
}
export interface AcceptInvitationData {
  target_type: 'group' | 'session';
  target_id: string;
  joined: boolean;
  already_joined?: boolean;
}

// 创建群组邀请。
export async function createGroupInvitation(group_id: string, body: CreateInvitationBody) {
  return backendRequest<BackendApiEnvelope<CollaborationInvitationData>>(
    `/openapi/v1/collaboration/groups/${group_id}/invitations`,
    { method: 'POST', data: body, injectUserId: false },
  );
}

// 创建会话邀请。
export async function createSessionInvitation(session_id: string, body: CreateInvitationBody) {
  return backendRequest<BackendApiEnvelope<CollaborationInvitationData>>(
    `/openapi/v1/collaboration/sessions/${session_id}/invitations`,
    { method: 'POST', data: body, injectUserId: false },
  );
}

// 接受邀请。
export async function acceptInvitation(token: string) {
  return backendRequest<BackendApiEnvelope<AcceptInvitationData>>(
    `/openapi/v1/collaboration/invitations/${token}/accept`,
    { method: 'POST', data: {}, injectUserId: false },
  );
}
