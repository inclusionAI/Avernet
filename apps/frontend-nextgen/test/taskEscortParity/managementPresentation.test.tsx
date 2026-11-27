/** @jest-environment jsdom */
import { managementTime } from '@/domain/taskEscortParity/management';
import type {
  ManagementHistory as History,
  ManagementHistoryItem,
  ManagementNotification as Notification,
} from '@/domain/taskEscortParity/managementTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { ManagementHistory } from '@/pages/TaskEscort/parity/management/ManagementHistory';
import { ManagementNotification } from '@/pages/TaskEscort/parity/management/ManagementNotifications';
import { managementService } from '@/services/taskEscortParity/managementService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/managementService', () => ({
  managementService: { notification: jest.fn(), version: jest.fn(), deploy: jest.fn() },
}));
const service = jest.mocked(managementService);
const context: TaskEscortParityContext = {
  scopeKey: 'space:actor',
  ready: true,
  userId: 'actor',
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const revisions: ManagementHistoryItem[] = Array.from({ length: 7 }, (_, i) => ({
  version: 7 - i,
  deployNumber: 13 - i,
  tagName: null,
  action: 'deploy',
  fromDeployNumber: null,
  note: `release-${i}`,
  botId: null,
  ownerId: null,
  isActive: i === 0, // A stale row marker must not override the authoritative active deployment.
  createdAtMs: 1794873600000,
  source: null,
}));
const notification: Notification = {
  workflowId: 'wf',
  robotCode: 'robot',
  hasSecret: true,
  onFailureUsers: [],
  onFailureGroups: [],
  onFailureMessageTitle: '默认失败标题',
  onFailureMessageIncludeRunLink: true,
  notifyOn: ['node_failed', 'workflow_succeeded', 'provider_event_v2'],
  eventOptions: {
    node_failed: {
      title: '节点错误标题',
      includeRunLink: false,
      includeNodeOutput: false,
      maxOutputFields: 0,
      maxOutputValueLen: 0,
    },
    workflow_succeeded: { title: '工作流完成标题', includeFinalOutput: true },
    provider_event_v2: {},
  },
};
beforeEach(() => {
  jest.clearAllMocks();
  service.notification.mockResolvedValue(notification);
});
function renderHistory(data: History, canEdit = true) {
  return render(
    <ManagementHistory
      context={context}
      canEdit={canEdit}
      resource={{ data, loading: false, error: null, refresh: jest.fn() }}
    />,
  );
}

it('groups version, current marker and deployment above the note/provenance in outlined cards', () => {
  renderHistory({ history: revisions, active: revisions[2] });
  const cards = screen.getAllByRole('listitem');
  expect(cards).toHaveLength(5);
  cards.forEach((card, i) => {
    expect(card).toHaveClass('rounded-lg', 'border');
    const heading = within(card).getByRole('heading', { level: 3, name: `v${revisions[i].version}` });
    const header = heading.closest('header')!;
    expect(header).not.toBeNull();
    expect(header).toHaveTextContent(`deploy #${revisions[i].deployNumber}`);
    expect(header).not.toHaveTextContent(revisions[i].note!);
    expect(header).not.toHaveTextContent('来源未知');
    expect(
      header.compareDocumentPosition(within(card).getByText(revisions[i].note!)) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(within(card).getByText(`${managementTime(revisions[i].createdAtMs)} · 来源未知`)).toBeVisible();
  });
  expect(cards[2]).toHaveClass('bg-primary/5', 'border-primary');
  expect(within(cards[2]).getByText('当前')).toHaveClass('text-primary');
  expect(cards[0]).not.toHaveClass('bg-primary/5', 'border-primary');
  expect(screen.getAllByText('当前')).toHaveLength(1);
  expect(within(cards[2]).queryByRole('button', { name: '回滚' })).not.toBeInTheDocument();
  expect(within(cards[0]).getByRole('button', { name: '回滚' })).toBeDisabled();
  expect(service.version).not.toHaveBeenCalled();
  expect(service.deploy).not.toHaveBeenCalled();
});

it('expands and collapses real history with a full-width dashed control', () => {
  renderHistory({ history: revisions, active: revisions[2] });
  const toggle = screen.getByRole('button', { name: '展开其余 2 个版本' });
  expect(toggle).toHaveClass('w-full', 'border-dashed');
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(toggle);
  expect(screen.getAllByRole('listitem')).toHaveLength(7);
  expect(screen.getByText('release-6')).toBeVisible();
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(toggle).toHaveAccessibleName('收起历史版本');
  fireEvent.click(toggle);
  expect(screen.getAllByRole('listitem')).toHaveLength(5);
  expect(screen.queryByText('release-6')).not.toBeInTheDocument();
});

it('does not infer an active version or a known source and preserves empty history', () => {
  const { unmount } = renderHistory({ history: revisions, active: null }, false);
  expect(screen.getByText(/暂无激活版本/)).toBeVisible();
  expect(screen.queryByText('当前')).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem').every((card) => !card.classList.contains('bg-primary/5'))).toBe(true);
  expect(screen.getAllByText(/来源未知/)).toHaveLength(5);
  expect(screen.queryByRole('button', { name: '回滚' })).not.toBeInTheDocument();
  unmount();
  renderHistory({ history: [], active: null });
  expect(screen.getByText('暂无版本历史')).toBeVisible();
  expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /历史版本|展开其余/ })).not.toBeInTheDocument();
});

it('shows all configured event-message cards together without a disclosure or event selector', async () => {
  render(<ManagementNotification context={context} />);
  const messages = await screen.findByRole('region', { name: '事件消息配置' });
  for (const label of ['节点失败', '工作流成功', 'provider_event_v2']) {
    const card = within(messages).getByRole('region', { name: `${label}消息配置` });
    expect(card).toBeVisible();
    expect(card.closest('details')).toBeNull();
    expect(within(card).getByRole('heading', { name: label })).toBeVisible();
    expect(card).toHaveClass('rounded-lg', 'border');
  }
  expect(within(messages).getByText('节点错误标题')).toBeVisible();
  expect(within(messages).getByText('工作流完成标题')).toBeVisible();
  const unknown = within(messages).getByRole('region', { name: 'provider_event_v2消息配置' });
  expect(within(unknown).getAllByText('未记录')).toHaveLength(6);
  expect(within(unknown).queryByText('默认失败标题')).not.toBeInTheDocument();
  const failed = within(messages).getByRole('region', { name: '节点失败消息配置' });
  expect(within(failed).getAllByText('否')).toHaveLength(2);
  expect(within(failed).getAllByText('0')).toHaveLength(2);
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByLabelText('appSecret')).toHaveValue('');
  expect(screen.getByLabelText('appSecret')).toBeDisabled();
  expect(screen.getByLabelText('appSecret')).toHaveAttribute('type', 'password');
  for (const name of ['保存通知配置', '删除通知配置']) {
    const button = screen.getByRole('button', { name });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription('未接入写入契约');
  }
});

it.each([
  [null, '未显式配置（使用服务端旧版事件策略）'],
  [[], '未订阅任何事件'],
] as const)('preserves subscription state %s without hiding configured overrides', async (notifyOn, label) => {
  service.notification.mockResolvedValue({ ...notification, notifyOn: notifyOn === null ? null : [...notifyOn] });
  render(<ManagementNotification context={context} />);
  expect(await screen.findByText(label)).toBeVisible();
  expect(screen.getByRole('region', { name: '节点失败消息配置' })).toBeVisible();
  expect(screen.getByRole('region', { name: '工作流成功消息配置' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'provider_event_v2消息配置' })).toBeVisible();
});

it('shows an explicit empty override state, not fabricated configured event templates', async () => {
  service.notification.mockResolvedValue({ ...notification, eventOptions: {} });
  render(<ManagementNotification context={context} />);
  expect(await screen.findByText('未配置事件级覆盖')).toBeVisible();
  const messages = screen.getByRole('region', { name: '事件消息配置' });
  expect(within(messages).queryByRole('region')).not.toBeInTheDocument();
  expect(within(messages).queryByText('默认失败标题')).not.toBeInTheDocument();
  expect(screen.getByText('节点失败、工作流成功、provider_event_v2')).toBeVisible();
});
