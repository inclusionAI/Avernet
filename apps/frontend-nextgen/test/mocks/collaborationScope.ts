import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
export const scopeIdentity: IdentityView = { id: 'human_test', kind: 'user', displayName: '测试用户', online: true };
export const scopeSession: SessionView = {
  sessionId: 'g:s',
  groupId: 'g',
  title: '目标会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  createdAt: 1,
  lastMessageAt: 1,
  favorite: false,
};
export const scopeGroup: GroupView = {
  groupId: 'g',
  name: '目标群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  participantCount: 0,
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};
