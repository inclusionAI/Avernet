/** @jest-environment jsdom */
// 真实 collab 装配 + AppShell + 路由页面；仅在 HTTP 出口供给 BCS 响应。
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { appExtension } from '@/extensions/collab';
import BotsPage from '@/pages/CollaborationSquare/Bots';
import GroupsPage from '@/pages/CollaborationSquare/Groups';
import { AppShell } from '@/shell/AppShell';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import '@testing-library/jest-dom';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

const mockLocation = { pathname: '/collaboration-square/bots' };
const mockHistoryPush = jest.fn();
const mockBackendRequest = jest.fn();

jest.mock('@umijs/max', () => ({
  history: { push: (...args: unknown[]) => mockHistoryPush(...args) },
  useLocation: () => mockLocation,
  useNavigate: () => jest.fn(),
  Link: ({ to, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; children: ReactNode }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));
jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: (...args: unknown[]) => mockBackendRequest(...args),
}));

const PREFIX = '/openapi/v1/collaboration';
const paths = {
  mine: `${PREFIX}/bots/mine`, candidates: `${PREFIX}/bots/human_7/candidates`,
  requests: `${PREFIX}/friend-connections/requests`, relations: `${PREFIX}/friend-connections`,
  groups: `${PREFIX}/public-groups`, group: `${PREFIX}/groups/public-group`,
  sessions: `${PREFIX}/groups/public-group/sessions`,
};
const candidate = (id: string, name: string, createdBy = '8', friend = false) => ({
  bot: { bot_id: id, name, avatar_url: null, created_by: createdBy,
    descriptor: { summary: '公开简介' }, visibility: 'public', user_visibility: 'public', status: 'online' },
  is_friend: friend,
});
const group = {
  group_id: 'public-group', version: 1, kind: 'normal', status: 'active', visibility: 'public',
  originator_actor_id: 'human_8', driver_bot_uuid: 'driver:8', driver_bot_name: '群主助手',
  driver_bot_owner: '8', driver_bot_owner_name: '群主用户',
  collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
  name: '公开协作示例', created_at: 1, updated_at: 1, participant_count: 1,
  participants: [{ actor_id: 'driver:8', actor_kind: 'bot', name: '群内助手', role: 'driver', mode: 'auto' }],
};
function envelope(data: unknown, code = 20000) { return { code, message: 'ok', request_id: 'r', data }; }

beforeEach(() => {
  window.localStorage.clear();
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useCollaborationSquareStore.getState().reset();
  useExternalAuthStore.getState().reset();
  useExternalAuthStore.getState().setAuthenticated({ userId: '7', displayName: '测试用户', provider: 'oauth-provider' });
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    configurable: true, value: { PUBLIC_BOT_BACKEND_ENABLED: true },
  });
  extendCapabilities(appExtension.capabilities);
  mockHistoryPush.mockClear();
  mockBackendRequest.mockReset().mockImplementation(async (url: string, options: { method?: string } = {}) => {
    if (url === paths.mine) return envelope({ items: [
      { kind: 'human', bot_id: 'human_7', name: '测试用户', status: 'online' },
      { kind: 'bot', bot_id: 'owned:7', name: '管理助手', status: 'online', engine: 'openclaw' },
    ], total: 2, offset: 0, limit: 50 });
    if (url === paths.candidates) return envelope({ items: [
      candidate('friend:8', '好友助手', '8', true), candidate('owned:7', '管理助手', '7'),
      candidate('new:8', '新助手'),
    ], total: 3, offset: 0, limit: 100 });
    if (url === paths.requests && options.method === 'POST') return envelope({ status: 'approved' }, 20100);
    if (url === paths.requests || url === paths.relations) return envelope({ items: [], total: 0 });
    if (url === paths.groups) return envelope({ items: [group], total: 1 });
    if (url === paths.group) return envelope(group);
    if (url === paths.sessions && options.method === 'POST') return envelope({
      session_id: 'public-session', group_id: 'public-group',
      participants: [{ actor_id: 'human_7', actor_kind: 'human', role: 'consultant' }],
    }, 20100);
    throw new Error(`collab 不允许此请求: ${url}`);
  });
});
afterEach(() => {
  cleanup();
  extendCapabilities(defaultCapabilities);
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useCollaborationSquareStore.getState().reset();
  useExternalAuthStore.getState().reset();
});
function expectBcsOnlyRequests() {
  const requested = mockBackendRequest.mock.calls.map(([url]) => url);
  expect(requested.length).toBeGreaterThan(0);
  expect(requested.filter((url) => !Object.values(paths).includes(url))).toEqual([]);
}

it('真实 Bot 页面通过 BCS 加好友；已有好友/管理 Bot/申请成功均无单聊入口和请求', async () => {
  mockLocation.pathname = '/collaboration-square/bots';
  render(<AppShell><BotsPage /></AppShell>);
  await screen.findByText('新助手');
  expect(screen.getByRole('link', { name: '公开 Bot' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '公开协作群' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '专家' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '立即开始对话' })).not.toBeInTheDocument();
  expect(screen.getAllByText('已是好友')).toHaveLength(2);

  fireEvent.click(screen.getByRole('button', { name: '申请好友权限' }));
  const dialog = await screen.findByRole('dialog');
  const apply = await within(dialog).findByRole('button', { name: '申请好友权限' });
  fireEvent.click(apply);
  await waitFor(() => expect(useCollaborationSquareStore.getState().bots.find((item) => item.id === 'new:8')?.relationshipStatus).toBe('friend'));
  await act(async () => { await Promise.resolve(); });
  expect(mockBackendRequest).toHaveBeenCalledWith(paths.requests, expect.objectContaining({
    method: 'POST', data: { to_actor: { type: 'bot', id: 'new:8' }, from_actor: { type: 'human', id: '7' } },
  }));
  expect(screen.queryByRole('button', { name: '立即开始对话' })).not.toBeInTheDocument();
  expect(mockHistoryPush).not.toHaveBeenCalled();
  expectBcsOnlyRequests();
});

it('真实公开群页面查看成员、创建会话后只跳转到 collab 保留的协作群页面', async () => {
  mockLocation.pathname = '/collaboration-square/groups';
  render(<AppShell><GroupsPage /></AppShell>);
  await screen.findByText('公开协作示例');
  fireEvent.click(screen.getByRole('button', { name: '1 位成员' }));
  const members = await screen.findByRole('dialog');
  await within(members).findByText('群内助手');
  fireEvent.click(within(members).getByRole('button', { name: '关闭弹窗' }));
  fireEvent.click(screen.getByRole('button', { name: '创建新会话' }));
  fireEvent.change(screen.getByPlaceholderText('请输入会话名称'), { target: { value: '协作测试' } });
  fireEvent.change(screen.getByPlaceholderText('请描述本会话希望达成的协作目标'), { target: { value: '生成方案' } });
  fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
  await waitFor(() => expect(mockHistoryPush).toHaveBeenCalledTimes(1));
  expect(mockBackendRequest).toHaveBeenCalledWith(paths.sessions, expect.objectContaining({
    method: 'POST', data: expect.objectContaining({ title: '协作测试', acting_bot_id: 'human_7', input: { query: '生成方案' } }),
  }));
  const destination = new URL(mockHistoryPush.mock.calls[0][0], 'http://localhost');
  expect(destination.pathname).toBe('/workspace/collaboration');
  expect(destination.searchParams.get('group')).toBe('public-group');
  expect(destination.searchParams.get('session')).toBe('public-session');
  expectBcsOnlyRequests();
});
