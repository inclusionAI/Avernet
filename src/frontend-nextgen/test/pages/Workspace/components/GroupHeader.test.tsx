/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupHeader, type GroupHeaderProps } from '@/pages/Workspace/components/GroupHeader';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const group: GroupView = {
  groupId: 'g1',
  name: '主站群',
  kind: 'task_dag',
  status: 'active',
  participants: [
    { actorId: 'me', kind: 'human', name: '我', role: 'owner', mode: 'present' },
    { actorId: 'bot1', kind: 'bot', name: '协作 Bot', role: 'driver', mode: 'auto' },
  ],
  participantCount: 2,
  sessions: [],
  lastMessageAt: 0,
  createdAt: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '发布排期讨论',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

const buildProps = (partial: Partial<GroupHeaderProps> = {}): GroupHeaderProps => ({
  selectedGroup: group,
  selectedSession: session,
  supportState: { phase: 'ready', error: null },
  connectionStatus: 'connected',
  onReconnect: jest.fn(),
  canManageGroup: { allowed: true },
  activePanel: 'none',
  onTogglePanel: jest.fn(),
  onRequestDissolve: jest.fn(),
  onRequestShareGroup: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com' } })),
  onRequestShareSession: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com' } })),
  ...partial,
});

it('选中会话时标题用会话名称，副标题展示群名/成员数/群类型', () => {
  render(<GroupHeader {...buildProps()} />);
  expect(screen.getByRole('heading', { name: '发布排期讨论' })).toBeInTheDocument();
  expect(screen.getByText('主站群 · 2 个成员 · 自定义协同')).toBeInTheDocument();
  expect(screen.queryByText('协作群 · 群组对话')).not.toBeInTheDocument();
});

it('未选会话时标题用群名称，副标题展示成员数与群类型', () => {
  render(<GroupHeader {...buildProps({ selectedSession: null })} />);
  expect(screen.getByRole('heading', { name: '主站群' })).toBeInTheDocument();
  expect(screen.getByText('2 个成员 · 自定义协同')).toBeInTheDocument();
});

it('participants 为空时回退 participantCount', () => {
  render(<GroupHeader {...buildProps({ selectedGroup: { ...group, participants: [] } })} />);
  expect(screen.getByText('主站群 · 2 个成员 · 自定义协同')).toBeInTheDocument();
});

it('群聊连接状态文案：connected=已连接、disconnected=已断开', () => {
  const { rerender } = render(<GroupHeader {...buildProps({ connectionStatus: 'connected' })} />);
  expect(screen.getByText('已连接')).toBeInTheDocument();
  expect(screen.queryByText('在线')).not.toBeInTheDocument();
  rerender(<GroupHeader {...buildProps({ connectionStatus: 'disconnected' })} />);
  expect(screen.getByText('已断开')).toBeInTheDocument();
  expect(screen.queryByText('离线')).not.toBeInTheDocument();
});
