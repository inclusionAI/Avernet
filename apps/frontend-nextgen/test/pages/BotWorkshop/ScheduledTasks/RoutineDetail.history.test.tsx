/** @jest-environment jsdom */
import { RoutineDetail } from '@/pages/BotWorkshop/ScheduledTasks/RoutineDetail';
import { backendRequest } from '@/services/backendApi/httpClient';
import { createRoutineClient, mapManagedRoutine } from '@/services/botScheduledTasks/service';
import type { RoutineContext } from '@/services/botScheduledTasks/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));
jest.mock('@/components/ScheduledTasks/RoutineRunConversation', () => ({ RoutineRunConversation: () => null }));

const request = backendRequest as jest.Mock;

it.each(['original-owner', 'new-owner'])('刷新草稿记录查询原实例且不重复执行（%s）', async (owner) => {
  const client = createRoutineClient('bot', 'stable-entity', owner);
  const row = {
    routine_id: 'job',
    bot_id: 'bot',
    owner_id: owner,
    runtime_stage: 'draft' as const,
    device_uuid: 'draft-instance',
    operator_id: null,
    name: 'Daily',
    trigger: { type: 'schedule' as const, cron: '0 9 * * *' },
    command: 'hello',
    enabled: true,
  };
  const context: RoutineContext = {
    bot_id: 'bot',
    bot_name: 'Bot',
    owner_id: owner,
    bot_type: 'service',
    engine: 'openclaw',
    caller_mode: false,
    can_manage: true,
    supported: true,
    environments: [],
  };
  let historyReads = 0;
  request.mockReset();
  request.mockImplementation(async (url: string) => ({
    code: 200000,
    message: 'OK',
    request_id: 'trace',
    data: url.endsWith('/runs')
      ? {
          total: 1,
          items: [{ run_id: `manual-job-${++historyReads}`, routine_id: 'job', status: 'ok' }],
          failed_targets: [],
        }
      : row,
  }));
  render(
    <RoutineDetail
      task={mapManagedRoutine(row, client.scope)}
      context={context}
      client={client}
      revision={0}
      actions={() => null}
    />,
  );
  expect(await screen.findByText('manual-job-1')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新记录' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '刷新记录' }));
  expect(await screen.findByText('manual-job-2')).toBeInTheDocument();
  const historyCalls = request.mock.calls.filter(([url]) => String(url).endsWith('/runs'));
  expect(historyCalls).toHaveLength(2);
  for (const [, options] of historyCalls) {
    expect(options.method).toBe('GET');
    expect(options.params).toMatchObject({
      entity_id: 'stable-entity',
      runtime_stage: 'draft',
      device_uuid: 'draft-instance',
    });
    expect(options.params.operator_id).toBeUndefined();
  }
  expect(request.mock.calls.some(([url]) => String(url).endsWith('/run'))).toBe(false);
});
