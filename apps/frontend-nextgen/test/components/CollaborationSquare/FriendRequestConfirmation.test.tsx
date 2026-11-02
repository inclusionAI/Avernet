/** @jest-environment jsdom */
import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import type { BotCatalogViewModel, FriendRequestActor } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/components/CollaborationSquare/SquareSearchBar', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/CollaborationSquare/SquareIdentityPicker', () => ({ SquareIdentityPicker: () => null }));
jest.mock('@/components/CollaborationSquare/BotProfileModal', () => ({ BotProfileModal: () => null }));

function buildVm(actor: FriendRequestActor): BotCatalogViewModel {
  return {
    activeActor: actor, activeActorName: actor.type === 'human' ? '风太' : '助手一号',
    bots: [{ id: 'target', name: '自我鼓励师', ownerName: '常至', description: '鼓励伙伴', capabilities: [], relationshipStatus: 'none' }],
    busyKeys: [], query: '', mode: 'name', loading: false, error: null,
    hasMore: false, loadingMore: false, loadMoreError: null,
    setQuery: jest.fn(), setMode: jest.fn(), reload: jest.fn(), loadMore: jest.fn(),
    primaryAction: jest.fn(), share: jest.fn(), openProfile: jest.fn(), closeProfile: jest.fn(),
    selectedBotId: null, botProfile: null, detailLoading: false, copyBotId: jest.fn(),
  };
}

const show = (vm: BotCatalogViewModel) => <PublicBotCatalogPanel vm={vm} scrollRootRef={{ current: null }} />;
const open = () => fireEvent.click(screen.getByRole('button', { name: '申请好友权限' }));

it.each(['human', 'bot'] as const)('以 %s 身份确认后才发送申请', (type) => {
  const vm = buildVm({ type, id: 'viewer' });
  render(show(vm));
  open();
  expect(vm.primaryAction).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog')).toHaveTextContent(type === 'human' ? '以用户 风太 身份申请添加Bot 自我鼓励师 为好友' : '以Bot 助手一号 身份申请添加Bot 自我鼓励师 为好友');
  expect(screen.getByRole('dialog')).not.toHaveTextContent('本次将为');
  expect(screen.getByRole('dialog', { name: 'Bot好友申请' })).toBeInTheDocument();
  const hint = screen.getByText(type === 'human'
    ? 'Bot Owner用户通过申请后，该申请身份可与该Bot单聊。'
    : 'Bot Owner用户通过申请后，该申请身份仅可与该Bot发起群聊，不支持直接单聊。');
  expect(hint).toHaveClass('text-left', 'text-xs', 'text-muted-foreground');
  expect(screen.getByText(/身份申请添加/)).toHaveClass('text-left');
  expect(screen.getByText(type === 'human' ? '风太' : '助手一号', { selector: 'code' })).toHaveClass('bg-muted', 'rounded');
  expect(screen.getByText('自我鼓励师', { selector: 'code' })).toHaveClass('bg-muted', 'rounded');
  fireEvent.click(screen.getByRole('button', { name: '发送申请' }));
  expect(vm.primaryAction).toHaveBeenCalledTimes(1);
  expect(vm.primaryAction).toHaveBeenCalledWith(vm.bots[0]);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it.each(['取消', '关闭弹窗'])('%s 不发送申请', (label) => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  render(show(vm)); open();
  fireEvent.click(screen.getByRole('button', { name: label }));
  expect(vm.primaryAction).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('身份切换后关闭旧确认，重新申请展示新身份', () => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  const { rerender } = render(show(vm)); open();
  const next = { ...vm, activeActor: { type: 'bot' as const, id: 'helper' }, activeActorName: '助手一号' };
  rerender(show(next));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vm.primaryAction).not.toHaveBeenCalled();
  open();
  expect(screen.getByRole('dialog')).toHaveTextContent('以Bot 助手一号 身份申请添加');
});

it('目标已移除时关闭确认且不发送', () => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  const { rerender } = render(show(vm)); open();
  rerender(show({ ...vm, bots: [] }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vm.primaryAction).not.toHaveBeenCalled();
});

it('已有好友直接进入对话，不弹申请确认', () => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  vm.bots[0].relationshipStatus = 'friend';
  render(show(vm));
  fireEvent.click(screen.getByRole('button', { name: '立即开始对话' }));
  expect(vm.primaryAction).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('Escape 关闭确认且不发送', () => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  render(show(vm)); open();
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vm.primaryAction).not.toHaveBeenCalled();
});

it('申请状态变化后不允许通过旧弹窗再次申请', () => {
  const vm = buildVm({ type: 'human', id: 'viewer' });
  const { rerender } = render(show(vm)); open();
  rerender(show({ ...vm, bots: [{ ...vm.bots[0], relationshipStatus: 'applying' }] }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vm.primaryAction).not.toHaveBeenCalled();
});
