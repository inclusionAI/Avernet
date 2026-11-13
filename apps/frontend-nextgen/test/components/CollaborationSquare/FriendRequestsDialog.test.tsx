/** @jest-environment jsdom */
import { FriendRequestsButton } from '@/components/CollaborationSquare/FriendRequestsButton';
import { friendConnectionRequestService as service } from '@/services/collaborationSquare/friendConnectionRequestService';
import type { FriendRequestSnapshot } from '@/domain/collaborationSquare/friendRequests';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@/services/collaborationSquare/friendConnectionRequestService', () => ({
  friendConnectionRequestService: { load: jest.fn(), decide: jest.fn() },
}));
jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
const actor = { type: 'bot' as const, id: 'mine:7' };
const human = { userId: '7', displayName: '登录用户', avatarUrl: '/human.png', online: true };
const snapshot: FriendRequestSnapshot = {
  received: { items: [{ requestId: 'received', fromActor: { type: 'bot', id: 'peer:8', name: '同伴助手', avatarUrl: '/peer.png', online: true },
    toActor: { ...actor, name: '我的 Bot' }, status: 'pending', message: '共同完成任务' }], total: 30, page: 1, pageSize: 20 },
  sent: { items: [{ requestId: 'sent', fromActor: { ...actor, name: '我的 Bot' },
    toActor: { type: 'human', id: '7', name: '7' }, status: 'pending' }], total: 1, page: 1, pageSize: 20 },
};
beforeEach(() => {
  jest.resetAllMocks();
  jest.mocked(service.load).mockResolvedValue(snapshot);
});
function mount() {
  const onChanged = jest.fn();
  render(<FriendRequestsButton actor={actor} actorName="我的 Bot" human={human} onChanged={onChanged} />);
  return { onChanged };
}
async function openDialog() {
  fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
  await screen.findByText('同伴助手');
  return screen.getByRole('dialog');
}

it('关闭不加载；打开默认收到列表，展示头像名称/消息，切到发出列表不可审批', async () => {
  mount();
  expect(service.load).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const dialog = await openDialog();
  expect(within(dialog).getByRole('img', { name: '同伴助手' })).toHaveAttribute('src', '/peer.png');
  expect(within(dialog).getByText('共同完成任务')).toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: '通过' })).toBeEnabled();
  fireEvent.click(within(dialog).getByRole('button', { name: '发出的申请' }));
  expect(within(dialog).getByText('登录用户')).toBeInTheDocument();
  expect(within(dialog).getByRole('img', { name: '登录用户' })).toHaveAttribute('src', '/human.png');
  expect(within(dialog).queryByRole('button', { name: '通过' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: '拒绝' })).not.toBeInTheDocument();
});

it.each(['通过', '拒绝'] as const)('%s 确认成功后更新行且通知父页面，不关闭弹窗', async (label) => {
  const status = label === '通过' ? 'approved' : 'rejected';
  jest.mocked(service.decide).mockResolvedValue({ ...snapshot.received.items[0], status });
  const { onChanged } = mount();
  const dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole('button', { name: label }));
  await within(dialog).findByText(label === '通过' ? '已通过' : '已拒绝');
  expect(service.decide).toHaveBeenCalledWith('received', label === '通过' ? 'accept' : 'reject', expect.any(AbortSignal));
  expect(onChanged).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: '通过' })).not.toBeInTheDocument();
});

it('审批失败显示行内错误并保留可重试操作', async () => {
  jest.mocked(service.decide).mockRejectedValue(new Error('拒绝失败'));
  const { onChanged } = mount();
  const dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole('button', { name: '拒绝' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('拒绝失败');
  expect(within(dialog).getByRole('button', { name: '拒绝' })).toBeEnabled();
  expect(onChanged).not.toHaveBeenCalled();
});

it('待审批中同一条的两个按钮都禁用，刷新与分页不能覆盖写入', async () => {
  let finish!: (value: typeof snapshot.received.items[number]) => void;
  jest.mocked(service.decide).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  mount();
  const dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole('button', { name: '通过' }));
  expect(within(dialog).getByRole('button', { name: '通过' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '拒绝' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '刷新' })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: '下一页' }));
  expect(service.load).toHaveBeenCalledTimes(1);
  await act(async () => finish({ ...snapshot.received.items[0], status: 'approved' }));
});

it('分页使用当前方向，刷新重试资料加载警告', async () => {
  jest.mocked(service.load).mockResolvedValue({ ...snapshot, detailsWarning: '资料暂不可用' });
  mount();
  const dialog = await openDialog();
  expect(within(dialog).getByText('资料暂不可用')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(service.load).toHaveBeenLastCalledWith(actor, { sent: 1, received: 2 }, expect.any(AbortSignal)));
});
it('加载失败可刷新，空结果有独立空态', async () => {
  jest.mocked(service.load).mockRejectedValueOnce(new Error('连接失败')).mockResolvedValue({
    sent: { items: [], total: 0, page: 1, pageSize: 20 }, received: { items: [], total: 0, page: 1, pageSize: 20 },
  });
  mount();
  fireEvent.click(screen.getByRole('button', { name: '好友申请' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('连接失败');
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  await screen.findByText('暂无收到的待处理申请');
  fireEvent.click(screen.getByRole('button', { name: '发出的申请' }));
  expect(screen.getByText('暂无发出的待处理申请')).toBeInTheDocument();
});
it('关闭后重新打开重新加载', async () => {
  mount();
  await openDialog();
  fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }));
  await openDialog();
  expect(service.load).toHaveBeenCalledTimes(2);
});

it('身份不可用时入口禁用且不加载', () => {
  render(<FriendRequestsButton actor={null} human={null} onChanged={jest.fn()} />);
  expect(screen.getByRole('button', { name: '好友申请' })).toBeDisabled();
  expect(service.load).not.toHaveBeenCalled();
});
