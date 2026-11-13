import type { FriendRequestActor } from './types';

export type FriendRequestDirection = 'sent' | 'received';
export type FriendRequestStatus = 'pending' | 'approved' | 'rejected' | 'cancelled';
export type FriendRequestDecision = 'accept' | 'reject';
export type FriendRequestPages = Record<FriendRequestDirection, number>;
export interface FriendRequestParty extends FriendRequestActor {
  name: string;
  avatarUrl?: string;
  /** 资料接口未给出在线状态时保持未知，不伪造离线。 */
  online?: boolean;
}
export interface FriendRequestItem {
  requestId: string;
  fromActor: FriendRequestParty;
  toActor: FriendRequestParty;
  status: FriendRequestStatus;
  message?: string;
  decisionReason?: string;
  decidedAt?: number;
}
export interface FriendRequestPage {
  items: FriendRequestItem[];
  total: number;
  page: number;
  pageSize: number;
}
export interface FriendRequestSnapshot {
  sent: FriendRequestPage;
  received: FriendRequestPage;
  detailsWarning?: string;
}
export function sameFriendActor(left: FriendRequestActor, right: FriendRequestActor): boolean {
  return left.type === right.type && left.id === right.id;
}
