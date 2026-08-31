import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type CollaborationFriendRequestDto = BackendUnknownRecord;
export type CreateFriendRequestRequest = BackendUnknownRecord;

export const COLLABORATION_FRIEND_REQUEST_ENDPOINTS = {
  friendRequests: (bot_uuid: string) => `/openapi/v1/collaboration/bots/${bot_uuid}/friend-requests`,
  friendships: (bot_uuid: string) => `/openapi/v1/collaboration/bots/${bot_uuid}/friendships`,
  friendship: (bot_uuid: string, friend_bot_uuid: string) =>
    `/openapi/v1/collaboration/bots/${bot_uuid}/friendships/${friend_bot_uuid}`,
  accept: (request_id: string) => `/openapi/v1/collaboration/friend-requests/${request_id}/accept`,
  reject: (request_id: string) => `/openapi/v1/collaboration/friend-requests/${request_id}/reject`,
};

// 查询好友申请列表。
export function listFriendRequests(bot_uuid: string, params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationFriendRequestDto>>>(
    COLLABORATION_FRIEND_REQUEST_ENDPOINTS.friendRequests(bot_uuid),
    { method: 'GET', params },
  );
}

// 创建好友申请。
export function createFriendRequest(bot_uuid: string, body: CreateFriendRequestRequest) {
  return backendRequest<BackendApiEnvelope<CollaborationFriendRequestDto>>(
    COLLABORATION_FRIEND_REQUEST_ENDPOINTS.friendRequests(bot_uuid),
    { method: 'POST', data: body },
  );
}

// 接受好友申请。
export function acceptFriendRequest(request_id: string) {
  return backendRequest<BackendApiEnvelope<CollaborationFriendRequestDto>>(
    COLLABORATION_FRIEND_REQUEST_ENDPOINTS.accept(request_id),
    { method: 'POST' },
  );
}

// 拒绝好友申请。
export function rejectFriendRequest(request_id: string) {
  return backendRequest<BackendApiEnvelope<CollaborationFriendRequestDto>>(
    COLLABORATION_FRIEND_REQUEST_ENDPOINTS.reject(request_id),
    { method: 'POST' },
  );
}

// 删除好友关系。
export function deleteFriendship(bot_uuid: string, friend_bot_uuid: string) {
  return backendRequest<BackendApiEnvelope<void>>(
    COLLABORATION_FRIEND_REQUEST_ENDPOINTS.friendship(bot_uuid, friend_bot_uuid),
    { method: 'DELETE' },
  );
}
