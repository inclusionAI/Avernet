/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { appExtension } from '@/extensions/collab';
import BotsPage from '@/pages/CollaborationSquare/Bots';
import GroupsPage from '@/pages/CollaborationSquare/Groups';
import { AppShell } from '@/shell/AppShell';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import '@testing-library/jest-dom';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

const mockLocation = { pathname: '/collaboration-square/bots' };
const mockPush = jest.fn();
const mockRequest = jest.fn();
jest.mock('@umijs/max', () => ({
  history: { push: (...args: unknown[]) => mockPush(...args) }, useLocation: () => mockLocation,
  useNavigate: () => jest.fn(),
  Link: ({ to, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; children: ReactNode }) => <a href={to} {...props}>{children}</a>,
}));
jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: (...args: unknown[]) => mockRequest(...args),
}));
const base = '/openapi/v1/collaboration';
const requestsPath = `${base}/friend-connections/requests`;
const actor = { type: 'bot', id: 'mine:7' };
const peer = { type: 'bot', id: 'peer:8' };
const received = (id: string) => ({ request_id: id, from_actor: peer, to_actor: actor, status: 'pending', created_by: peer, message: '一起协作' });
const envelope = (data: unknown) => ({ code: 20000, message: 'ok', data });
let accepted: Set<string>;
function inboxReads() {
  return mockRequest.mock.calls.filter(([url, options]) => url === requestsPath && options.method === 'GET' && options.params.page_size === 20);
}
beforeEach(() => {
  accepted = new Set();
  mockLocation.pathname = '/collaboration-square/bots';
  window.localStorage.clear();
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useCollaborationSquareStore.getState().reset();
  useExternalAuthStore.getState().reset();
  useExternalAuthStore.getState().setAuthenticated({ userId: '7', displayName: '登录用户', provider: 'oauth-provider' });
  extendCapabilities(appExtension.capabilities);
  mockPush.mockClear();
  mockRequest.mockReset().mockImplementation(async (url: string, options: { method: string; params?: Record<string, unknown> }) => {
    if (url === `${base}/bots/mine`) return envelope({ items: [
      { kind: 'human', bot_id: 'human_7', name: '登录用户', status: 'online' },
      { kind: 'bot', bot_id: actor.id, name: '我的助手', status: 'online' },
    ], total: 2, offset: 0, limit: 50 });
    if (url.endsWith('/candidates')) return envelope({ items: [], total: 0, offset: 0, limit: 100 });
    if (url === `${base}/public-groups`) return envelope({ items: [], total: 0 });
    if (url === requestsPath) {
      const params = options.params ?? {};
      if (params.page_size !== 20) return envelope({ items: [], total: 0 }); // 发现卡片的 pending 回填。
      const self = { type: params.actor_type, id: params.actor_id };
      const items = params.actor_type !== 'bot' ? [] : params.direction === 'received'
        ? ['accept-id', 'reject-id'].filter((id) => !accepted.has(id)).map(received)
        : [{ request_id: 'sent-id', from_actor: self, to_actor: peer, status: 'pending', created_by: self }];
      return envelope({ items, total: items.length, page: params.page, page_size: 20 });
    }
    if (url === `${base}/bots/query`) return envelope({ items: [
      { kind: 'bot', bot_id: peer.id, name: '合作助手', avatar_url: '/peer.png' },
      { kind: 'bot', bot_id: actor.id, name: '我的助手', avatar_url: '/mine.png' },
    ] });
    const match = url.match(/\/requests\/(accept-id|reject-id)\/(accept|reject)$/);
    if (match && options.method === 'POST') {
      accepted.add(match[1]);
      return envelope({ ...received(match[1]), status: match[2] === 'accept' ? 'approved' : 'rejected', decided_by: { type: 'human', id: '7' } });
    }
    throw new Error(`Unexpected endpoint ${url}`);
  });
});
afterEach(() => {
  cleanup();
  extendCapabilities(defaultCapabilities);
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useCollaborationSquareStore.getState().reset();
  useExternalAuthStore.getState().reset();
});
async function selectBot() {
  fireEvent.click(await screen.findByRole('button', { name: '当前协作身份：登录用户' }));
  fireEvent.click(await screen.findByRole('button', { name: /我的助手/ }));
  await screen.findByRole('button', { name: '当前协作身份：我的助手' });
}

it('collab 页头按钮位于身份切换器之后，关闭时不加载收件箱；Bot 视角批量补齐并通过/拒绝', async () => {
  render(<AppShell><BotsPage /></AppShell>);
  await selectBot();
  const identity = screen.getByRole('button', { name: '当前协作身份：我的助手' });
  const entry = screen.getByRole('button', { name: '好友申请' });
  expect(identity.compareDocumentPosition(entry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(inboxReads()).toEqual([]);
  fireEvent.click(entry);
  const dialog = await screen.findByRole('dialog');
  await within(dialog).findAllByText('合作助手');
  for (const direction of ['sent', 'received']) expect(mockRequest).toHaveBeenCalledWith(requestsPath, expect.objectContaining({
    params: { actor_type: 'bot', actor_id: actor.id, direction, status: 'pending', page: 1, page_size: 20 },
  }));
  expect(mockRequest.mock.calls.filter(([url]) => url === `${base}/bots/query`)).toHaveLength(1);
  expect(mockRequest).toHaveBeenCalledWith(`${base}/bots/query`, expect.objectContaining({ data: { bot_ids: [actor.id, peer.id] } }));
  expect(within(dialog).getAllByRole('img', { name: '合作助手' })[0]).toHaveAttribute('src', '/peer.png');
  const before = mockRequest.mock.calls.filter(([url]) => url.endsWith('/candidates')).length;
  fireEvent.click(within(dialog).getAllByRole('button', { name: '通过' })[0]);
  await within(dialog).findByText('已通过');
  fireEvent.click(within(dialog).getByRole('button', { name: '拒绝' }));
  await within(dialog).findByText('已拒绝');
  expect(mockRequest).toHaveBeenCalledWith(`${requestsPath}/accept-id/accept`, expect.objectContaining({ method: 'POST' }));
  expect(mockRequest).toHaveBeenCalledWith(`${requestsPath}/reject-id/reject`, expect.objectContaining({ method: 'POST' }));
  await waitFor(() => expect(mockRequest.mock.calls.filter(([url]) => url.endsWith('/candidates')).length).toBeGreaterThan(before));
  fireEvent.click(within(dialog).getByRole('button', { name: '发出的申请' }));
  expect(within(dialog).getByText('合作助手')).toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: '通过' })).not.toBeInTheDocument();
  expect(mockPush).not.toHaveBeenCalled();
  const allowed = new Set([`${base}/bots/mine`, `${base}/bots/human_7/candidates`, `${base}/bots/mine%3A7/candidates`,
    requestsPath, `${base}/bots/query`, `${requestsPath}/accept-id/accept`, `${requestsPath}/reject-id/reject`]);
  expect(mockRequest.mock.calls.map(([url]) => url).filter((url) => !allowed.has(url))).toEqual([]);
});

it('登录 Human 使用 user id 查询，切换发现身份立即销毁旧弹窗并重置分页', async () => {
  render(<AppShell><BotsPage /></AppShell>);
  await screen.findByRole('button', { name: '当前协作身份：登录用户' });
  fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
  await screen.findByText('暂无收到的待处理申请');
  expect(inboxReads().map(([, options]) => options.params.actor_id)).toEqual(['7', '7']);
  // 身份可从其它同步入口变化，弹窗仍须按 actor scope 销毁。
  act(() => useSquareIdentityStore.getState().selectIdentity('mine:7'));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('Open 即使 BCS-only 也不展示此入口，公开协作群页面同样不展示', async () => {
  extendCapabilities({ ...defaultCapabilities, getPublicBotBackendEnabled: () => false });
  const { unmount } = render(<BotsPage />);
  expect(screen.getByRole('link', { name: '专家' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '好友申请' })).not.toBeInTheDocument();
  expect(inboxReads()).toEqual([]);
  unmount();
  extendCapabilities(appExtension.capabilities);
  render(<GroupsPage />);
  expect(screen.queryByRole('button', { name: '好友申请' })).not.toBeInTheDocument();
});
