/** @jest-environment jsdom */
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { groupService } from '@/services/workspace/groupService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/botClassificationService');
jest.mock('@/services/workspace/collaborationCandidateService');
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: () => null,
}));

const cs = jest.mocked(collaborationCandidateService);
const gs = jest.mocked(groupService);

it('selects discover-only Bots with SVG data avatars and retains their metadata after clearing the search', async () => {
  const avatarUrl = 'data:image/svg+xml;utf8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E';
  cs.listCandidates.mockResolvedValue({
    ok: true, data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false },
  });
  cs.recommendCandidates.mockResolvedValue({ ok: true, data: [{
    id: 'discover-only:owner', name: '测试专用', avatarUrl, online: true, status: 'online',
    reachability: 'reachable', visibility: 'protected', recommendScore: 0.839,
  }] });
  gs.createGroup.mockResolvedValue({ ok: true, data: {
    groupId: 'discovered-group', name: '测试协作群', kind: 'free_chat', status: 'active',
    participants: [], sessions: [], participantCount: 2, lastMessageAt: 0, createdAt: 0,
    isPublic: false, deliveryPolicy: 'send_to_driver',
  } });
  cs.listFriends.mockResolvedValue({
    ok: true, data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false },
  });
  jest.mocked(queryIsServiceBot).mockResolvedValue(false);
  render(<CreateGroupModal open activeIdentity={{ id: 'human_1', kind: 'user', displayName: '我', online: true }}
    onClose={jest.fn()} onCreated={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '可协作 Bot' }));
  fireEvent.click(screen.getByText('按画像筛选'));
  const search = await screen.findByLabelText('按画像关键词搜索可协作 Bot');
  fireEvent.change(search, { target: { value: '数据分析' } });
  const row = await within(await screen.findByTestId('group-participant-list'))
    .findByRole('button', { name: /测试专用/ });
  expect(within(row).getByRole('img', { name: '测试专用' })).toHaveAttribute('src', avatarUrl);
  fireEvent.click(row);
  expect(screen.getByTestId('group-participant-selected')).toHaveTextContent('测试专用');
  fireEvent.click(screen.getByRole('button', { name: '群主 Bot' }));
  fireEvent.click(await screen.findByRole('option', { name: /测试专用/ }));
  fireEvent.change(search, { target: { value: '' } });
  await screen.findByText('暂无可协作 Bot');
  expect(screen.getByTestId('group-participant-selected')).toHaveTextContent('测试专用');
  expect(within(screen.getByRole('button', { name: '群主 Bot' }))
    .getByRole('img', { name: '测试专用' })).toHaveAttribute('src', avatarUrl);
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() => expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({
    driverBotUuid: 'discover-only:owner', name: expect.stringContaining('测试专用'),
  })));
});
