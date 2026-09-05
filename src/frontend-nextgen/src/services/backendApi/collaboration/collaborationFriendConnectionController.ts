import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';

export type FriendConnectionActorType = 'human' | 'bot';
export type FriendConnectionRequestStatus = 'pending' | 'approved' | 'rejected' | 'cancelled';
export type FriendConnectionStatus = FriendConnectionRequestStatus | 'public_no_edge';

export interface FriendConnectionActor {
  type: FriendConnectionActorType;
  id: string;
}

export interface FriendConnectionRequestDto {
  request_id: string;
  from_actor: FriendConnectionActor;
  to_actor: FriendConnectionActor;
  status: FriendConnectionRequestStatus;
  message?: string;
  created_at?: number | string;
  updated_at?: number | string;
}

/** 已建立好友连接列表项。接口返回连接另一端 actor，不返回 status/from_actor/to_actor。 */
export interface FriendConnectionDto {
  actor: FriendConnectionActor;
  is_online?: boolean;
}

export interface FriendConnectionSummaryDto {
  actor: FriendConnectionActor;
  name?: string;
}

export interface CreateFriendConnectionRequestBody {
  to_actor: FriendConnectionActor;
  from_actor?: FriendConnectionActor;
  message?: string;
}

export interface CreateFriendConnectionRequestData {
  request_ids: string[];
  edge_ids: string[];
  status: FriendConnectionStatus;
  auto_accepted: boolean;
}

export interface ListFriendConnectionRequestsParams {
  direction?: 'received' | 'sent' | 'all';
  status?: FriendConnectionRequestStatus;
  page?: number;
  page_size?: number;
  actor_type?: FriendConnectionActorType;
  actor_id?: string;
}

export interface ListFriendConnectionsParams {
  actor_type: FriendConnectionActorType;
  actor_id: string;
}

export interface DeleteFriendConnectionBody {
  from_actor: FriendConnectionActor;
  to_actor: FriendConnectionActor;
}

export const COLLABORATION_FRIEND_CONNECTION_ENDPOINTS = {
  requests: '/openapi/v1/collaboration/friend-connections/requests',
  request: (requestId: string) => `/openapi/v1/collaboration/friend-connections/requests/${requestId}`,
  accept: (requestId: string) => `/openapi/v1/collaboration/friend-connections/requests/${requestId}/accept`,
  reject: (requestId: string) => `/openapi/v1/collaboration/friend-connections/requests/${requestId}/reject`,
  cancel: (requestId: string) => `/openapi/v1/collaboration/friend-connections/requests/${requestId}/cancel`,
  connections: '/openapi/v1/collaboration/friend-connections',
};

export function createFriendConnectionRequest(body: CreateFriendConnectionRequestBody, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<CreateFriendConnectionRequestData>>(
    COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.requests,
    { method: 'POST', data: body, injectUserId: false, signal },
  );
}

export function listFriendConnectionRequests(params: ListFriendConnectionRequestsParams = {}, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<FriendConnectionRequestDto>>>(
    COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.requests,
    { method: 'GET', params: { ...params }, injectUserId: false, signal },
  );
}

export function acceptFriendConnectionRequest(requestId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<FriendConnectionRequestDto>>(
    COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.accept(requestId),
    { method: 'POST', injectUserId: false, signal },
  );
}

export function rejectFriendConnectionRequest(requestId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<FriendConnectionRequestDto>>(
    COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.reject(requestId),
    { method: 'POST', injectUserId: false, signal },
  );
}

export function cancelFriendConnectionRequest(requestId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<FriendConnectionRequestDto>>(
    COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.cancel(requestId),
    { method: 'POST', injectUserId: false, signal },
  );
}

export function listFriendConnections<T = FriendConnectionDto>(
  params: ListFriendConnectionsParams,
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<T>>>(COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.connections, {
    method: 'GET',
    params: { ...params },
    injectUserId: false,
    signal,
  });
}

export function deleteFriendConnection(body: DeleteFriendConnectionBody, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<void>>(COLLABORATION_FRIEND_CONNECTION_ENDPOINTS.connections, {
    method: 'DELETE',
    data: body,
    injectUserId: false,
    signal,
  });
}
