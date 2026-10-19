/** @jest-environment jsdom */
import type { Task } from '@/domain/evolution/types';
import { formatTime } from '@/domain/evolution/types';
import { BenchSessionReader } from '@/pages/EvolutionFactory/BenchReader';
import VersionList, { stableVersion, versionStatus } from '@/pages/EvolutionFactory/VersionList';
import { evolutionApi, type EvolutionVersion } from '@/services/backendApi/clawevolve/controller';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';
let mockSearch = '';
jest.mock('@umijs/max', () => {
  const j = require('@jest/globals').jest;
  return { history: { push: j.fn() }, useLocation: () => ({ search: mockSearch }) };
});
jest.mock('@/pages/EvolutionFactory/useEvolutionContext', () => ({
  useEvolutionContext: () => ({ ready: true, key: 'viewer:42', userId: 'viewer', spaceId: 42 }),
}));
jest.mock('@/services/backendApi/clawevolve/controller', () => {
  const j = require('@jest/globals').jest;
  return { evolutionApi: { versions: j.fn(), diff: j.fn(), packDownload: j.fn() } };
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
it.each(['42', '99'])('applies linked Bot filter only within the matching space (%s)', async (space) => {
  mockSearch = `?tab=versions&space_id=${space}&botOwner=author&botId=bot`;
  api.versions.mockResolvedValue({ items: [item(), item({ versionId: 'other', botId: 'another-bot' })] });
  render(<VersionList />);
  if (space === '42') {
    expect(await screen.findByText('共 1 条 · 第 1 页')).toBeInTheDocument();
    expect(screen.queryByText('another-bot', { selector: 'td' })).not.toBeInTheDocument();
  } else expect(await screen.findByText('共 2 条 · 第 1 页')).toBeInTheDocument();
});
