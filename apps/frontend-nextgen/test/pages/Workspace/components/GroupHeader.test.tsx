/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupHeader, type GroupHeaderProps } from '@/pages/Workspace/components/GroupHeader';
import { PrimaryNavContext, type PrimaryNavApi } from '@/components/Workspace/PrimaryNav';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const group: GroupView = {
  groupId: 'g1',
  name: '主站群',
  kind: 'task_dag',
  status: 'active',
  participants: [
    { actorId: 'me', kind: 'human', name: '我', role: 'owner', mode: 'present' },
    { actorId: 'user2', kind: 'human', name: '同事', role: 'member', mode: 'absent' },
    { actorId: 'bot1', kind: 'bot', name: '协作 Bot', role: 'driver', mode: 'auto' },
  ],
  participantCount: 3,
  sessions: [],
  lastMessageAt: 0,
  createdAt: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const session: SessionView = {
  sessionId: 'sess-key-x1',
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
  connectionStatus: 'connected',
  onOpenSessionList: jest.fn(),
  canManageGroup: { allowed: true },
  activePanel: 'none',
  onTogglePanel: jest.fn(),
  favorites: { favorite: false, disabled: false, onToggle: jest.fn() },
  sidePaneOpen: false,
  onToggleSidePane: jest.fn(),
  onRequestDissolve: jest.fn(),
  onRequestShareGroup: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com' } })),
  onRequestShareSession: jest.fn(async () => ({ ok: true as const, data: { invitationUrl: 'http://example.com' } })),
  ...partial,
});

it('窄屏会话列表入口位于 chip 左侧并使用右箭头图标', () => {
  const onOpenSessionList = jest.fn();
  render(<GroupHeader {...buildProps({ onOpenSessionList })} />);

  const button = screen.getByRole('button', { name: '打开会话列表' });
  expect(button).toHaveClass('lg:hidden');
  fireEvent.click(button);
  expect(onOpenSessionList).toHaveBeenCalledTimes(1);
  expect(button.querySelector('svg')).toHaveClass('lucide-chevron-right');
});

it('chip 呈现群名、会话 ID 与用户/Bot 两级计数（artboard-010 形态）', () => {
  render(<GroupHeader {...buildProps()} />);

  expect(screen.getByRole('group', { name: '协作群 主站群' })).toBeInTheDocument();
  expect(screen.getByText('主站群')).toBeInTheDocument();
  expect(screen.getByText('sess-key-x1')).toBeInTheDocument();
  expect(screen.getByLabelText('2 用户')).toBeInTheDocument();
  expect(screen.getByLabelText('1 Bot')).toBeInTheDocument();
});

it('顶部连接状态随真实会话连接变化，无会话时不显示', () => {
  const { rerender } = render(<GroupHeader {...buildProps()} />);
  expect(screen.getByRole('status')).toHaveTextContent('已连接');
  for (const [status, label] of [
    ['connecting', '连接中'],
    ['reconnecting', '重连中'],
    ['error', '连接失败'],
    ['disconnected', '已断开'],
  ] as const) {
    rerender(<GroupHeader {...buildProps({ connectionStatus: status })} />);
    expect(screen.getByRole('status')).toHaveTextContent(label);
  }
  rerender(<GroupHeader {...buildProps({ selectedSession: null })} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('history-only sessions show a neutral membership status instead of a connection error', () => {
  render(<GroupHeader {...buildProps({
    selectedSession: { ...session, participants: group.participants },
    connectionStatus: 'disconnected', historyOnly: true,
  })} />);
  expect(screen.getByRole('status')).toHaveTextContent('未加入当前会话');
  expect(screen.queryByText('已断开')).not.toBeInTheDocument();
});

it('点击会话 ID 胶囊复制按钮写入剪贴板并 toast', async () => {
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  render(<GroupHeader {...buildProps()} />);

  fireEvent.click(screen.getByRole('button', { name: '复制会话 ID' }));
  await Promise.resolve();
  expect(writeText).toHaveBeenCalledWith('sess-key-x1');
});

it('未选会话时会话 ID 胶囊与会话级图标钮隐藏，群名/计数保留', () => {
  render(<GroupHeader {...buildProps({ selectedSession: null })} />);

  expect(screen.getByLabelText('协作群 主站群')).toBeInTheDocument();
  expect(screen.queryByText('sess-key-x1')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '复制会话 ID' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '管理会话' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '历史消息' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '资源管理' })).not.toBeInTheDocument();
  // 群级能力保留：分享协作群与管理协作群；副屏与会话无关恒渲染。
  expect(screen.getByRole('button', { name: '分享协作群' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '管理协作群' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '副屏' })).toBeInTheDocument();
});

it('participants 缺失时计数回退 participantCount 单计数', () => {
  render(<GroupHeader {...buildProps({ selectedGroup: { ...group, participants: [] } })} />);
  expect(screen.getByLabelText('3 用户')).toBeInTheDocument();
  expect(screen.queryByLabelText(/ Bot$/)).not.toBeInTheDocument();
});

it('右侧六枚图标钮按 artboard-003 顺序：收藏/分享/会话管理/历史消息/资源管理/副屏', () => {
  const onTogglePanel = jest.fn();
  render(<GroupHeader {...buildProps({ onTogglePanel })} />);

  const row = screen.getByRole('button', { name: '副屏' }).parentElement!;
  const icons = Array.from(row.querySelectorAll('button svg')).map((svg) =>
    Array.from(svg.classList).find((cls) => cls.startsWith('lucide-')),
  );
  expect(icons).toEqual([
    'lucide-star',
    'lucide-share2',
    'lucide-sliders-horizontal',
    'lucide-history',
    'lucide-folder-open',
    'lucide-monitor',
  ]);

  expect(screen.getByRole('button', { name: '收藏当前会话' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '分享会话' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '管理会话' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '历史消息' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '资源管理' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '副屏' })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '历史消息' }));
  expect(onTogglePanel).toHaveBeenCalledWith('history');
});

it('收藏按钮跟随 favorites 模型：收藏态填充、点击透传、无会话禁用', () => {
  const onToggle = jest.fn();
  const { rerender } = render(
    <GroupHeader {...buildProps({ favorites: { favorite: true, disabled: false, onToggle } })} />,
  );

  const star = screen.getByRole('button', { name: '取消收藏当前会话' });
  expect(star).toHaveAttribute('aria-pressed', 'true');
  expect(star.querySelector('svg')).toHaveClass('fill-star');
  fireEvent.click(star);
  expect(onToggle).toHaveBeenCalledTimes(1);

  rerender(
    <GroupHeader
      {...buildProps({ selectedSession: null, favorites: { favorite: false, disabled: true, onToggle } })}
    />,
  );
  expect(screen.getByRole('button', { name: '收藏当前会话' })).toBeDisabled();
});

it('图标钮面板开合状态以 aria-pressed 表达', () => {
  const { rerender } = render(<GroupHeader {...buildProps({ activePanel: 'resources' })} />);
  expect(screen.getByRole('button', { name: '关闭资源管理' })).toHaveAttribute('aria-pressed', 'true');
  rerender(<GroupHeader {...buildProps({ activePanel: 'sessionManage' })} />);
  expect(screen.getByRole('button', { name: '关闭会话管理' })).toHaveAttribute('aria-pressed', 'true');
  rerender(<GroupHeader {...buildProps({ activePanel: 'history' })} />);
  expect(screen.getByRole('button', { name: '关闭历史消息' })).toHaveAttribute('aria-pressed', 'true');
  rerender(<GroupHeader {...buildProps({ sidePaneOpen: true })} />);
  expect(screen.getByRole('button', { name: '副屏' })).toHaveAttribute('aria-pressed', 'true');
});

it('连接状态使用顶栏独立状态区，不进入身份胶囊', () => {
  render(<GroupHeader {...buildProps()} />);
  expect(screen.getByRole('group', { name: '协作群 主站群' })).not.toContainElement(screen.getByRole('status'));
});

/** AppShell 壳上下文桩：单测直渲 GroupHeader 时的真实壳契约最小复现。 */
const primaryNavApi: PrimaryNavApi = {
  openNav: jest.fn(),
  mountNavEntry: jest.fn(),
  unmountNavEntry: jest.fn(),
};

it('壳外直渲不渲染一级导航入口（上下文缺省 null，页头组件可独立渲染）', () => {
  render(<GroupHeader {...buildProps({ onOpenSessionList: jest.fn() })} />);
  expect(screen.queryByRole('button', { name: '打开导航' })).not.toBeInTheDocument();
});

it('壳内渲染：一级导航入口与「打开会话列表」同排且在其左侧（28px、<lg）', () => {
  render(
    <PrimaryNavContext.Provider value={primaryNavApi}>
      <GroupHeader {...buildProps({ onOpenSessionList: jest.fn() })} />
    </PrimaryNavContext.Provider>,
  );

  const navEntry = screen.getByRole('button', { name: '打开导航' });
  const sessionListEntry = screen.getByRole('button', { name: '打开会话列表' });
  expect(navEntry).toHaveClass('h-7', 'w-7', 'lg:hidden');
  expect(navEntry.compareDocumentPosition(sessionListEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(navEntry);
  expect(primaryNavApi.openNav).toHaveBeenCalledTimes(1);
});

it('壳内渲染（空群占位分支）：导航入口仍渲染且先于「打开会话列表」', () => {
  render(
    <PrimaryNavContext.Provider value={primaryNavApi}>
      <GroupHeader {...buildProps({ selectedGroup: null, selectedSession: null, onOpenSessionList: jest.fn() })} />
    </PrimaryNavContext.Provider>,
  );

  const navEntry = screen.getByRole('button', { name: '打开导航' });
  const sessionListEntry = screen.getByRole('button', { name: '打开会话列表' });
  expect(navEntry.compareDocumentPosition(sessionListEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('已连接文案窄宽收纳：<lg/副屏挤压仅保绿点，可访问名恒在；警示/错误态不收纳', () => {
  const view = render(<GroupHeader {...buildProps()} />);
  // success 专属收纳：视口 lg 与群头容器 32.5rem 双闸（副屏挤窄主区同样仅绿点）。
  const connectedStatus = screen.getByRole('status');
  expect(connectedStatus).toHaveAttribute('aria-label', '已连接');
  expect(connectedStatus).not.toHaveAttribute('title');
  expect(screen.getByText('已连接')).toHaveClass('hidden', 'lg:@min-[32.5rem]/group-header:inline');

  // 非 success 态保留完整文案（连接失败需要被读到），不参与收纳。
  view.rerender(<GroupHeader {...buildProps({ connectionStatus: 'error' })} />);
  expect(screen.getByText('连接失败')).not.toHaveClass('hidden');
  expect(screen.getByRole('status').querySelector('span')).toHaveClass('bg-destructive');
  view.unmount();
});

it('会话 ID 为空时复制静默不写剪贴板（抽取前原守卫原样保留）', () => {
  const writeText = jest.fn();
  Object.assign(navigator, { clipboard: { writeText } });
  render(
    <GroupHeader
      {...buildProps({
        selectedSession: { ...session, sessionId: '' },
      })}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '复制会话 ID' }));
  return Promise.resolve().then(() => expect(writeText).not.toHaveBeenCalled());
});
