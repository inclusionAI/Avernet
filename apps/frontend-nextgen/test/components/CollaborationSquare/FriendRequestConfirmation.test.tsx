/** @jest-environment jsdom */
import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import type { BotCatalogViewModel, FriendRequestActor } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

jest.mock('@/components/CollaborationSquare/SquareSearchBar', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/CollaborationSquare/SquareIdentityPicker', () => ({ SquareIdentityPicker: () => null }));
jest.mock('@/components/CollaborationSquare/BotProfileModal', () => ({ BotProfileModal: () => null }));

function buildVm(actor: FriendRequestActor, overrides: Partial<BotCatalogViewModel> = {}): BotCatalogViewModel {
  return {
    activeActor: actor, activeActorName: actor.type === 'human' ? '风太' : '助手一号',
    bots: [{ id: 'target', name: '自我鼓励师', ownerName: '常至', description: '鼓励伙伴', capabilities: [], relationshipStatus: 'none' }],
    busyKeys: [], query: '', mode: 'name', loading: false, error: null,
    hasMore: false, loadingMore: false, loadMoreError: null,
    setQuery: jest.fn(), setMode: jest.fn(), reload: jest.fn(), loadMore: jest.fn(),
    primaryAction: jest.fn(), share: jest.fn(), openProfile: jest.fn(), closeProfile: jest.fn(),
    selectedBotId: null, botProfile: null, botProfileError: null, detailLoading: false, copyBotId: jest.fn(),
    ...overrides,
  };
}

const show = (vm: BotCatalogViewModel) => <PublicBotCatalogPanel vm={vm} scrollRootRef={{ current: null }} />;
/** 卡片上的「申请好友权限」+ 图标按钮（进入申请弹窗）。 */
const open = () => fireEvent.click(screen.getAllByRole('button', { name: '申请好友权限' })[0]);
/** 弹窗内的「申请好友权限」发送按钮。 */
const send = () => fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '申请好友权限' }));

describe('公开 Bot 申请好友流程（dmore artboard-003/007 弹窗）', () => {
  it('点击 + 打开申请弹窗并拉取画像详情，弹窗内再次点击才发送申请', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' });
    render(show(vm));

    open();
    expect(vm.primaryAction).not.toHaveBeenCalled();
    expect(vm.openProfile).toHaveBeenCalledTimes(1);
    expect(vm.openProfile).toHaveBeenCalledWith(vm.bots[0]);
    const dialog = screen.getByRole('dialog');
    // 卡片摘要信息先就位：名称 + Owner + UUID + 描述。
    expect(within(dialog).getByText('自我鼓励师')).toBeInTheDocument();
    expect(within(dialog).getByText('常至')).toBeInTheDocument();
    expect(within(dialog).getByText('target')).toBeInTheDocument();
    expect(within(dialog).getByText('鼓励伙伴')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '申请好友权限' })).toBeInTheDocument();
    send();
    expect(vm.primaryAction).toHaveBeenCalledTimes(1);
    expect(vm.primaryAction).toHaveBeenCalledWith(vm.bots[0]);
    expect(vm.closeProfile).toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('关闭弹窗不发送申请且复位画像选中', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' });
    render(show(vm)); open();
    fireEvent.click(screen.getByRole('button', { name: '关闭弹窗' }));
    expect(vm.primaryAction).not.toHaveBeenCalled();
    expect(vm.closeProfile).toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('画像详情就位后按已公开能力切换版式', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' }, {
      selectedBotId: 'target',
      botProfile: {
        id: 'target', name: '自我鼓励师', ownerName: '常至', description: '鼓励伙伴',
        engine: 'OpenClaw',
        capabilities: [{ id: 'c1', name: 'AntV 可视化图表' }, { id: 'c2', name: 'Dima MCP' }],
        skills: [],
      },
    });
    render(show(vm)); open();

    const dialog = screen.getByRole('dialog');
    // 有已公开能力（artboard-007）：682px 宽 + 引擎胶囊 + 能力清单 + 「N 个MCP」计数。
    expect(dialog).toHaveClass('max-w-[682px]');
    expect(within(dialog).getByText('OpenClaw')).toBeInTheDocument();
    expect(within(dialog).getByText('已公开能力')).toBeInTheDocument();
    expect(within(dialog).getByText('2 个MCP')).toBeInTheDocument();
    expect(within(dialog).getByText('AntV 可视化图表')).toBeInTheDocument();
  });

  it('身份切换后关闭旧弹窗，重新申请走新身份', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' });
    const { rerender } = render(show(vm)); open();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    rerender(show({ ...vm, activeActor: { type: 'bot', id: 'helper' }, activeActorName: '助手一号' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(vm.primaryAction).not.toHaveBeenCalled();
    open();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(vm.openProfile).toHaveBeenCalledTimes(2);
  });

  it('目标已移除时关闭弹窗且不发送', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' });
    const { rerender } = render(show(vm)); open();
    rerender(show({ ...vm, bots: [] }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(vm.primaryAction).not.toHaveBeenCalled();
  });

  it('已有好友直接进入对话，不弹申请弹窗', () => {
    const vm = buildVm({ type: 'human', id: 'viewer' });
    vm.bots[0].relationshipStatus = 'friend';
    render(show(vm));
    fireEvent.click(screen.getByRole('button', { name: '立即开始对话' }));
    expect(vm.primaryAction).toHaveBeenCalledTimes(1);
    expect(vm.openProfile).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Escape 关闭弹窗且不发送', () => {
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
});
