/** @jest-environment jsdom */
import { Dashboard } from '@/pages/TaskEscort/Dashboard';
import { backendRequest } from '@/services/backendApi/httpClient';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const demo = { period: '7d', setPeriod: jest.fn(), navigate: jest.fn(), setWorkflowId: jest.fn() } as any;
function response(endpoint: string, range: Record<string, unknown> = {}) {
  if (endpoint === 'overview') return { totalRuns: 1234, successRate: 0.875, statusDistribution: {} };
  if (endpoint === 'daily-trend')
    return { ...range, dates: [{ date: '2026-11-17', failedCount: 3, successRate: 0.875 }] };
  if (endpoint === 'failure-hotspots')
    return { ...range, hotspots: [{ nodeLabel: '真实节点', workflowTitle: '真实流程', count: 3, sharePct: 75 }] };
  if (endpoint === 'workflow-health')
    return {
      ...range,
      available: true,
      workflows: [{ workflowId: 'real-health', workflowTitle: '真实流程', released: false, runCount: 8 }],
    };
  return { available: true, workflows: [] };
}
beforeEach(() => jest.clearAllMocks());
it.each([
  ['daily-trend', { dates: [{}] }, '重试运行质量趋势'],
  ['failure-hotspots', { hotspots: [null] }, '重试异常热点'],
  ['workflow-health', { workflows: [{}] }, '重试运行健康'],
  ['workflow-release-stats', { workflows: [null] }, '重试发布统计'],
] as const)(
  '%s malformed wire element renders a truthful retryable widget error, not an app crash',
  async (brokenEndpoint, broken, retryLabel) => {
    let corrupt = true;
    request.mockImplementation(async (url, options) => {
      const endpoint = url.split('/').at(-1)!;
      const good = response(endpoint, options?.params);
      return (corrupt && endpoint === brokenEndpoint ? { ...good, ...broken } : good) as any;
    });
    render(<Dashboard demo={demo} scope={{ key: 'real-user:real-space', ready: true }} />);
    await screen.findByText(/大盘接口返回了非预期的数据结构，请重试/);
    expect(screen.getByText('1,234')).toBeTruthy();
    expect(screen.getByRole('heading', { name: '任务护航数据大盘' })).toBeTruthy();
    const calls = request.mock.calls.length;
    corrupt = false;
    fireEvent.click(screen.getByRole('button', { name: retryLabel }));
    await waitFor(() => expect(screen.queryByText(/大盘接口返回了非预期的数据结构，请重试/)).toBeNull());
    expect(request).toHaveBeenCalledTimes(calls + 1);
  },
);
it('invalid optional wire metrics render unknowns in hotspots, trend details and matched workflow rows', async () => {
  request.mockImplementation(async (url, options) => {
    const endpoint = url.split('/').at(-1)!;
    const good = response(endpoint, options?.params);
    if (endpoint === 'failure-hotspots')
      return {
        ...good,
        hotspots: [{ nodeLabel: '真实节点', workflowTitle: '真实流程', count: '3', sharePct: '75' }],
      } as any;
    if (endpoint === 'daily-trend')
      return { ...good, dates: [{ date: '2026-11-17', failedCount: '3', successRate: '0.875' }] } as any;
    if (endpoint === 'workflow-health')
      return { ...good, workflows: [{ workflowId: 'real-health', workflowTitle: '真实流程', runCount: '8' }] } as any;
    return good as any;
  });
  render(<Dashboard demo={demo} scope={{ key: 'real-user:real-space', ready: true }} />);
  await screen.findByText('1,234');
  expect(screen.getByText('— 次 · —')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '查看趋势明细' }));
  const detail = screen.getByRole('table', { name: '运行质量趋势明细' });
  expect(within(detail).getAllByText('—')).toHaveLength(2);
  const workflows = screen.getByRole('table', { name: '工作流列表' });
  expect(within(workflows).getAllByText('—')).toHaveLength(9);
  expect(screen.queryByText('0.0%')).toBeNull();
});
