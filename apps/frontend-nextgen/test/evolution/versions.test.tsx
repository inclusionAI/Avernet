/** @jest-environment jsdom */
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import type { EvolutionVersion, SpaceBot, Task } from '@/domain/evolution/types';
import { formatTime } from '@/domain/evolution/types';
import { BenchSessionReader } from '@/pages/EvolutionFactory/BenchReader';
import VersionList, { stableVersion, versionStatus } from '@/pages/EvolutionFactory/VersionList';
import { evolutionApi } from '@/services/evolution/evolutionService';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { history } from '@umijs/max';
let mockSearch = '';
jest.mock('@umijs/max', () => {
  const j = require('@jest/globals').jest;
  return { history: { push: j.fn() }, useLocation: () => ({ search: mockSearch }) };
});
jest.mock('@/pages/EvolutionFactory/useEvolutionContext', () => ({
  useEvolutionContext: () => ({
    api: jest.requireMock<typeof import('@/services/evolution/evolutionService')>(
      '@/services/evolution/evolutionService',
    ).evolutionApi,
    ready: true,
    key: 'viewer:42',
    userId: 'viewer',
    spaceId: 42,
  }),
}));
jest.mock('@/services/evolution/evolutionService', () => {
  const j = require('@jest/globals').jest;
  return { evolutionApi: { versions: j.fn(), bots: j.fn(), diff: j.fn(), packDownload: j.fn() } };
});
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
});
afterEach(() => {
  if (originalScrollIntoView) HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  else delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
});
const api = jest.mocked(evolutionApi);
const item = (overrides: Partial<EvolutionVersion> = {}): EvolutionVersion => ({
  versionId: 'round-1',
  kind: 'round',
  taskId: 'task',
  stepId: 'step',
  userId: 'author',
  botId: 'bot',
  round: 1,
  acceptanceStatus: 'accepted',
  accepted: true,
  pack: { packId: 'pack', status: 'available' },
  ...overrides,
});
beforeEach(() => {
  mockSearch = '';
  api.versions.mockResolvedValue({ items: [] });
  api.bots.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 50 });
});
it('stable means accepted AND registered; higher rounds that failed promotion do not replace it', () => {
  const accepted = item();
  const pending = item({ versionId: 'new', round: 2, pack: null, acceptanceStatus: 'accepted_unregistered' });
  expect(stableVersion([pending, accepted])).toBe(accepted);
  expect(versionStatus(pending)).toBe('已接受 · Pack 未登记');
  const initial = item({ kind: 'initial', accepted: null });
  expect(stableVersion([pending, initial])).toBe(initial);
  expect(stableVersion([pending])).toBeUndefined();
});
it('task sidebar requests only the task and renders real empty state without claiming deployment', async () => {
  const task = {
    task_id: 'task',
    task_name: 'Task',
    task_type: 'full',
    created_by: 'author',
    user_id: 'author',
    bot_id: 'bot',
    config: {},
    status: 'completed',
    steps: [],
  } as Task;
  render(<VersionList task={task} />);
  expect(await screen.findByText('当前任务尚无已登记进化版本。')).toBeInTheDocument();
  expect(api.versions).toHaveBeenCalledWith('task');
  expect(screen.getByText('尚未建立')).toBeInTheDocument();
});
it('renders accepted version with score comparison and Pack operations, not a template version', async () => {
  api.versions.mockResolvedValue({
    items: [item({ scoreComparison: { name: 'success', baseline: 0.7, candidate: 0.9, delta: 0.2 } })],
  });
  render(
    <VersionList
      task={
        {
          task_id: 'task',
          task_name: 'Task',
          task_type: 'full',
          created_by: 'author',
          user_id: 'author',
          bot_id: 'bot',
          config: {},
          status: 'completed',
          steps: [],
        } as Task
      }
    />,
  );
  await screen.findByRole('button', { name: '下载' });
  expect(screen.getByRole('button', { name: '应用 Pack' })).toBeInTheDocument();
  expect(screen.getByText('Pack 已登记')).toBeInTheDocument();
});
it('distinguishes load failure from no versions', async () => {
  api.versions.mockRejectedValue(new Error('space denied'));
  render(
    <VersionList
      task={
        {
          task_id: 'task',
          task_name: 'Task',
          task_type: 'full',
          created_by: 'author',
          user_id: 'author',
          bot_id: 'bot',
          config: {},
        } as Task
      }
    />,
  );
  expect(await screen.findByRole('alert')).toHaveTextContent('space denied');
  expect(screen.queryByText('当前任务尚无已登记进化版本。')).not.toBeInTheDocument();
});
it('keeps session roles, thinking, tools, unknown events and Token details readable', () => {
  render(
    <BenchSessionReader
      session={{
        events: [
          {
            type: 'message',
            message: {
              role: 'assistant',
              usage: { input: 3, output: 4, totalTokens: 7 },
              content: [
                { type: 'thinking', thinking: 'reasoning text' },
                { type: 'toolCall', name: 'read', arguments: { path: 'file' } },
                { type: 'text', text: 'Final answer' },
              ],
            },
          },
          { type: 'custom_new', value: 1 },
        ],
      }}
    />,
  );
  expect(screen.getByText('模型回复', { exact: false })).toBeInTheDocument();
  expect(screen.getByText('Final answer')).toBeInTheDocument();
  fireEvent.click(screen.getByText('思考'));
  expect(screen.getByText('reasoning text')).toBeInTheDocument();
  expect(screen.getByText(/Token · total 7/)).toBeInTheDocument();
  expect(screen.getByText(/custom_new/, { selector: 'strong' })).toBeInTheDocument();
});

it('sidebar shows generation time and links to same-space Bot-filtered versions', async () => {
  api.versions.mockResolvedValue({ items: [item({ createdAt: 1789100000000 })] });
  render(<VersionList task={{ task_id: 'task', user_id: 'author', bot_id: 'bot', steps: [] } as unknown as Task} />);
  expect(await screen.findByText(`生成时间：${formatTime(1789100000000)}`)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看全部进化版本' }));
  const url = new URL(String(jest.mocked(history.push).mock.calls.at(-1)?.[0]), 'http://localhost');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    tab: 'versions',
    space_id: '42',
    botOwner: 'author',
    botId: 'bot',
  });
});
it.each([
  ['42', 1, false],
  ['99', 2, true],
] as const)('applies linked Bot filter only within the matching space (%s)', async (space, count, otherVisible) => {
  mockSearch = `?tab=versions&space_id=${space}&botOwner=author&botId=bot`;
  api.versions.mockResolvedValue({ items: [item(), item({ versionId: 'other', botId: 'another-bot' })] });
  render(<VersionList />);
  expect(await screen.findByText(`共 ${count} 条`)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '第 1 页' })).toHaveAttribute('aria-current', 'page');
  expect(screen.queryAllByText('another-bot', { selector: 'p' }).length > 0).toBe(otherVisible);
});

it('shows a stable Bot avatar and falls back to its ID without displaying the owner', async () => {
  api.versions.mockResolvedValue({ items: [item()] });
  render(<VersionList />);
  const avatar = await screen.findByRole('img', { name: 'bot' });
  expect(avatar).toHaveAttribute('src', resolveBotAvatarUrl(undefined, 'bot'));
  expect(avatar).toHaveStyle({ width: '32px', height: '32px' });
  expect(screen.getAllByText('bot', { selector: 'p' })).toHaveLength(2);
  expect(screen.queryByText('author', { selector: 'p' })).not.toBeInTheDocument();
  fireEvent.error(avatar);
  expect(screen.getByRole('img', { name: 'bot' }).tagName).toBe('SPAN');
});

it('uses 72px version rows, red rejected-status badges and blue text actions', async () => {
  api.versions.mockResolvedValue({
    items: [
      item({
        acceptanceStatus: 'rejected',
        reviewStatus: 'passed',
        specVersion: 'v1',
        diff: { available: true },
        taskName: '来源任务',
      }),
    ],
  });
  render(<VersionList />);
  const table = await screen.findByRole('table', { name: '进化版本列表' });
  expect(within(table).getAllByRole('row')[1]).toHaveClass('h-18');
  expect(within(table).getByRole('columnheader', { name: 'Bot' })).toBeInTheDocument();
  expect(within(table).getByRole('columnheader', { name: '来源任务' })).toBeInTheDocument();
  const sourceCell = within(table).getByRole('button', { name: '查看来源任务 来源任务' }).closest('td')!;
  expect(sourceCell).toHaveTextContent(/^来源任务$/);
  expect(within(sourceCell).queryByText('task')).not.toBeInTheDocument();
  expect(within(table).getByRole('columnheader', { name: '状态' })).toBeInTheDocument();
  expect(within(table).getByText('未接受')).toHaveClass('text-destructive');
  expect(within(table).getByText('未接受').querySelector('[aria-hidden]')).toBeNull();
  for (const name of ['Diff', '下载', '应用 Pack']) {
    expect(within(table).getByRole('button', { name })).toHaveClass('text-primary');
    expect(within(table).getByRole('button', { name })).not.toHaveClass('border-input');
  }
  const actionRow = within(table).getByRole('button', { name: 'Diff' }).parentElement!;
  expect(actionRow).toHaveClass('flex-nowrap', 'whitespace-nowrap');
  expect(actionRow).not.toHaveClass('flex-wrap');
  expect(within(actionRow).getAllByRole('button')).toHaveLength(3);
  expect(screen.getByRole('button', { name: '新建 Pack' })).toBeInTheDocument();
  fireEvent.click(within(table).getByRole('button', { name: '查看来源任务 来源任务' }));
  expect(history.push).toHaveBeenCalledWith('/evolution-factory/tasks/task');
});

it.each([
  [0.2, '+0.2000', 'text-success'],
  [-0.1, '-0.1000', 'text-destructive'],
  [0, '0.0000', 'text-muted-foreground'],
  [null, '—', 'text-muted-foreground'],
] as const)('renders the real score delta %s without inventing a value', async (delta, label, tone) => {
  api.versions.mockResolvedValue({
    items: [item({ scoreComparison: { baseline: 0.7, candidate: 0.9, delta } })],
  });
  render(<VersionList />);
  const comparison = await screen.findByLabelText('test_score');
  expect(comparison).toHaveClass('flex-nowrap', 'whitespace-nowrap');
  expect(comparison).not.toHaveClass('flex-wrap');
  expect(within(comparison).getByText('0.7 → 0.9')).toBeInTheDocument();
  expect(within(comparison).getByText(label)).toHaveClass(tone);
});


it('shows the real source task ID when its name is not returned', async () => {
  api.versions.mockResolvedValue({ items: [item({ taskId: 'task/one' })] });
  render(<VersionList />);
  const link = await screen.findByRole('button', { name: '查看来源任务 task/one' });
  expect(link).toHaveTextContent('task/one');
  fireEvent.click(link);
  expect(history.push).toHaveBeenCalledWith('/evolution-factory/tasks/task%2Fone');
});

it('searches the visible source task name', async () => {
  api.versions.mockResolvedValue({ items: [item({ taskName: '客服优化任务' }), item({ versionId: 'another', taskId: 'other' })] });
  render(<VersionList />);
  await screen.findByText('共 2 条');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索进化版本' }), { target: { value: '客服优化' } });
  expect(screen.getByText('共 1 条')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '查看来源任务 客服优化任务' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '查看来源任务 other' })).not.toBeInTheDocument();
});

it('filters version source without adding a Review result filter', async () => {
  api.versions.mockResolvedValue({ items: [
    item(),
    item({ versionId: 'snapshot-1', kind: 'snapshot', acceptanceStatus: 'unregistered' }),
  ] });
  render(<VersionList />);
  await screen.findByText('共 2 条');
  const source = screen.getByRole('combobox', { name: '版本来源' });
  expect(screen.queryByRole('combobox', { name: 'Review结果' })).not.toBeInTheDocument();
  fireEvent.keyDown(source, { key: 'ArrowDown' });
  expect(await screen.findByRole('option', { name: '主动快照' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: '已接受' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('option', { name: '主动快照' }));
  expect(screen.getByText('共 1 条')).toBeInTheDocument();
  expect(within(screen.getByRole('table', { name: '进化版本列表' })).getByText('主动快照')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: '状态' })).toBeInTheDocument();
});


it('shows the real Bot name above its ID and supports name search', async () => {
  api.versions.mockResolvedValue({ items: [item()] });
  api.bots.mockResolvedValue({ items: [
    { ownerId: 'other-owner', botId: 'bot', botName: '其他人的 Bot', env: 'dev', activeEngine: 'openclaw' },
    { ownerId: 'author', botId: 'bot', botName: 'PPT助手', env: 'dev', activeEngine: 'openclaw' },
  ], total: 2, page: 1, pageSize: 50 });
  render(<VersionList />);
  expect(await screen.findByRole('img', { name: 'PPT助手' })).toBeInTheDocument();
  const table = screen.getByRole('table', { name: '进化版本列表' });
  expect(within(table).getByText('PPT助手', { selector: 'p' })).toBeInTheDocument();
  expect(within(table).getByText('bot', { selector: 'p' })).toBeInTheDocument();
  expect(within(table).queryByText('author')).not.toBeInTheDocument();
  expect(within(table).queryByText('其他人的 Bot')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索进化版本' }), { target: { value: 'PPT助手' } });
  expect(screen.getByText('共 1 条')).toBeInTheDocument();
});

it('loads Bot names beyond the first page', async () => {
  const firstPage: SpaceBot[] = Array.from({ length: 50 }, (_, i) => ({
    ownerId: 'author', botId: `other-${i}`, botName: `Other ${i}`, env: 'dev', activeEngine: 'openclaw',
  }));
  api.versions.mockResolvedValue({ items: [item()] });
  api.bots.mockResolvedValueOnce({ items: firstPage, total: 51, page: 1, pageSize: 50 });
  api.bots.mockResolvedValueOnce({ items: [{ ownerId: 'author', botId: 'bot', botName: '第二页 Bot', env: 'dev', activeEngine: 'openclaw' }], total: 51, page: 2, pageSize: 50 });
  render(<VersionList />);
  expect(await screen.findByRole('img', { name: '第二页 Bot' })).toBeInTheDocument();
  expect(api.bots).toHaveBeenCalledWith(2);
});

it('keeps versions readable when Bot name lookup fails', async () => {
  api.versions.mockResolvedValue({ items: [item()] });
  api.bots.mockRejectedValue(new Error('Bot names unavailable'));
  render(<VersionList />);
  expect(await screen.findByRole('img', { name: 'bot' })).toBeInTheDocument();
  expect(screen.getByText('共 1 条')).toBeInTheDocument();
});


it('exposes generation time and raw Review / Spec fields as table columns', async () => {
  const createdAt = '2026-11-13T06:00:00Z';
  api.versions.mockResolvedValue({ items: [item({ createdAt, reviewStatus: 'passed', specVersion: 'v2' })] });
  render(<VersionList />);
  const table = await screen.findByRole('table', { name: '进化版本列表' });
  expect(within(table).getByRole('columnheader', { name: '生成时间' })).toBeInTheDocument();
  expect(within(table).getByRole('columnheader', { name: 'Review / Spec' })).toBeInTheDocument();
  expect(within(table).getByText(formatTime(createdAt))).toBeInTheDocument();
  expect(within(table).getByText('passed / v2')).toBeInTheDocument();
  expect(within(table).queryByText(/Review：|Spec：/)).not.toBeInTheDocument();
});

it('does not invent missing generation time or Review / Spec data', async () => {
  api.versions.mockResolvedValue({ items: [item()] });
  render(<VersionList />);
  const table = await screen.findByRole('table', { name: '进化版本列表' });
  const headers = within(table).getAllByRole('columnheader');
  const timeIndex = headers.findIndex((header) => header.textContent === '生成时间');
  const cells = within(within(table).getAllByRole('row')[1]).getAllByRole('cell');
  expect(cells[timeIndex]).toHaveTextContent(/^—$/);
  expect(within(table).getByText('— / —')).toBeInTheDocument();
});
