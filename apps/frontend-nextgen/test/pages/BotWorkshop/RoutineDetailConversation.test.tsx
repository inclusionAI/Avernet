/** @jest-environment jsdom */
import { RoutineDetail } from '@/pages/BotWorkshop/ScheduledTasks/RoutineDetail';
import type { createRoutineClient } from '@/services/botScheduledTasks/service';
import type { RoutineContext } from '@/services/botScheduledTasks/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/pages/BotWorkshop/ScheduledTasks/RoutineNextRun', () => ({
  RoutineNextRun: () => null, useRoutineClock: () => 0,
}));
jest.mock('@/components/ui/Pagination', () => ({
  Pagination: ({ onChange }: { onChange: (page: number) => void }) => <button type="button" onClick={() => onChange(2)}>下一页</button>,
}));

test('Bot management history opens the selected instance and clears the drawer when paging', async () => {
  const context: RoutineContext = { bot_id: 'bot', bot_name: 'Bot', owner_id: 'owner', bot_type: 'service',
    engine: 'openclaw', supported: true, can_manage: false, environments: [] };
  const task = { id: 'job', key: 'address', runtime_stage: 'online' as const };
  const client: ReturnType<typeof createRoutineClient> = {
    scope: { botId: 'bot', userId: 'actor', entityId: 'owner' }, context: jest.fn(), getLock: jest.fn(),
    list: jest.fn(), save: jest.fn(), toggle: jest.fn(), run: jest.fn(), remove: jest.fn(),
    detail: jest.fn().mockRejectedValue(new Error('definition unavailable')),
    runs: jest.fn().mockResolvedValue({ total: 3, failed_targets: [], items: [
      { id: 'same', routineId: 'job', status: 'ok', device_uuid: 'a', output: 'answer-a' },
      { id: 'same', routineId: 'job', status: 'ok', device_uuid: 'b', output: 'answer-b' },
    ] }),
  };
  render(<RoutineDetail task={task} context={context} client={client} revision={0} actions={() => null} />);
  const buttons = await screen.findAllByRole('button', { name: '查看会话' });
  fireEvent.click(buttons[1]);
  expect(screen.getByRole('dialog')).toHaveTextContent('answer-b');
  expect(screen.getByRole('dialog')).not.toHaveTextContent('answer-a');
  fireEvent.click(screen.getByRole('button', { name: '关闭执行会话' }));
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(client.runs).toHaveBeenLastCalledWith(context, task, 2, 20));
  await screen.findAllByRole('button', { name: '查看会话' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
