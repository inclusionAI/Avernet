/** @jest-environment jsdom */
import { BotManagementMenu } from '@/components/BotWorkshop/BotCard/BotManagementMenu';
import BotPrimaryActionsCell from '@/components/BotWorkshop/BotCard/BotPrimaryActionsCell';
import BotStatusCell from '@/components/BotWorkshop/BotCard/BotStatusCell';
import { useBotWorkshopActions } from '@/hooks/useBotWorkshopActions';
import { botManagementActionService } from '@/services/botWorkshop/botManagementActionService';
import { mapBotDto } from '@/services/bots/botMapper';
import { getInventoryActionAvailability } from '@/services/botWorkshop/botPolicy';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const bot = mapBotDto({
  bot_id: 'b1',
  bot_name: '回收 Bot',
  engine: 'openclaw',
  bot_type: 'personal',
  status: 'RECYCLED',
  display_state: 'pending',
  owner_entity_id: '149608',
  actions: ['view', 'activate', 'delete'],
}).item;
afterEach(() => {
  jest.restoreAllMocks();
  jest.clearAllMocks();
  useBotWorkshopStore.getState().reset();
});

test.each([
  ['RECYCLED', 'pending', '已回收', 'offline'],
  ['REACTIVATING', 'dormant', '激活中', 'deploying'],
  ['RECYCLING', 'running', '回收中', 'deploying'],
])('%s overrides stale display state %s', (status, displayState, label, lifecycle) => {
  const item = mapBotDto({ bot_id: 'b1', engine: 'openclaw', status, display_state: displayState }).item;
  expect(item.lifecycle).toBe(lifecycle);
  render(<BotStatusCell bot={item} />);
  expect(screen.getByText(label)).toBeInTheDocument();
  expect(getInventoryActionAvailability({ ...item, actions: ['edit', 'chat'] }, 'edit').enabled).toBe(false);
  expect(getInventoryActionAvailability({ ...item, actions: ['edit', 'chat'] }, 'chat').enabled).toBe(false);
});

test('activation entry uses confirmation and does not expose restart or configuration for a recycled bot', () => {
  const onAction = jest.fn().mockResolvedValue(undefined);
  render(<BotManagementMenu bot={bot} open lockedByOther={false} onAction={onAction} />);
  fireEvent.click(screen.getByRole('button', { name: /激活 Bot/ }));
  expect(screen.getByText(/将重新启动已回收/)).toBeInTheDocument();
  expect(onAction).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: /重启 Bot/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '变更归属空间' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认' }));
  expect(onAction).toHaveBeenCalledWith('activate', bot);
});

test('collaborator without backend activate grant has no activation entry', () => {
  render(<BotManagementMenu bot={{ ...bot, actions: ['view'] }} open lockedByOther={false} onAction={jest.fn()} />);
  expect(screen.queryByRole('button', { name: /激活 Bot/ })).not.toBeInTheDocument();
});

test('activating bot has no primary operations', () => {
  render(
    <BotPrimaryActionsCell bot={{ ...bot, lifecycle: 'deploying', rawStatus: 'REACTIVATING' }} onView={jest.fn()} />,
  );
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test.each(['service', 'desktop'])('%s does not receive the personal cloud activation entry', (botType) => {
  const item = mapBotDto({
    bot_id: 'b2',
    engine: 'openclaw',
    bot_type: botType,
    status: 'RECYCLED',
    display_state: botType === 'service' ? 'service_offline' : 'local_offline',
    actions: ['activate'],
  }).item;
  render(<BotManagementMenu bot={item} open lockedByOther={false} onAction={jest.fn()} />);
  expect(screen.queryByRole('button', { name: /激活 Bot/ })).not.toBeInTheDocument();
});

test('fresh list results win over the activation acceptance, including rollback to RECYCLED', async () => {
  useBotWorkshopStore.setState({ items: [bot] });
  jest.spyOn(botManagementActionService, 'run').mockResolvedValue({ bot_id: 'b1', status: 'REACTIVATING' });
  const load = jest.fn(async () => {
    useBotWorkshopStore.setState({ items: [bot] });
  });
  const { result } = renderHook(() => useBotWorkshopActions(load, '149608'));
  await act(async () => {
    await result.current('activate', bot);
  });
  expect(useBotWorkshopStore.getState().items[0]).toEqual(bot);
});

test('a coding restart pins the card as restarting before the inventory catches up', async () => {
  const runningBot = mapBotDto({
    bot_id: 'restart-b1',
    owner_entity_id: '149608',
    bot_name: 'Coding Bot',
    active_engine: 'aicoding',
    bot_type: 'personal',
    status: 'ACTIVE',
    display_state: 'running',
    actions: ['view', 'chat', 'edit', 'restart'],
  }).item;
  useBotWorkshopStore.setState({ items: [runningBot] });
  jest.spyOn(botManagementActionService, 'run').mockResolvedValue({
    status: 'ACTIVE',
    ext: {
      aicoding: {
        restart: { operation_id: 'op-restart-1', status: 'RUNNING', phase: 'QUEUED', message: null },
      },
    },
  } as never);
  const load = jest.fn(async () => {});
  const { result } = renderHook(() => useBotWorkshopActions(load, '149608'));
  await act(async () => {
    await result.current('restart', runningBot);
  });

  expect(load).toHaveBeenCalledTimes(1);
  expect(useBotWorkshopStore.getState().items[0]).toMatchObject({
    lifecycle: 'deploying',
    rawStatus: 'PENDING',
  });
  expect(useBotWorkshopStore.getState().items[0].pendingRestart).toMatchObject({
    operationId: 'op-restart-1',
  });
});

test('a synchronous restart adopts the status returned by the mutation response', async () => {
  const runningBot = mapBotDto({
    bot_id: 'restart-b2',
    owner_entity_id: '149608',
    bot_name: 'OpenClaw Bot',
    active_engine: 'openclaw',
    bot_type: 'personal',
    status: 'ACTIVE',
    display_state: 'running',
    actions: ['view', 'chat', 'edit', 'restart'],
  }).item;
  useBotWorkshopStore.setState({ items: [runningBot] });
  jest.spyOn(botManagementActionService, 'run').mockResolvedValue({ status: 'PENDING' } as never);
  const load = jest.fn(async () => {});
  const { result } = renderHook(() => useBotWorkshopActions(load, '149608'));
  await act(async () => {
    await result.current('restart', runningBot);
  });

  expect(load).toHaveBeenCalledTimes(1);
  expect(useBotWorkshopStore.getState().items[0]).toMatchObject({
    lifecycle: 'deploying',
    rawStatus: 'PENDING',
  });
  expect(useBotWorkshopStore.getState().items[0].pendingRestart).toBeUndefined();
});

test('accepted activation updates status immediately, suppresses duplicates and refreshes the list', async () => {
  useBotWorkshopStore.setState({ items: [bot] });
  let resolveActivation!: (value: { bot_id: string; status: string }) => void;
  const run = jest.spyOn(botManagementActionService, 'run').mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveActivation = resolve;
      }),
  );
  const load = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotWorkshopActions(load, '149608'));
  await act(async () => {
    const pending = result.current('activate', bot);
    await result.current('activate', bot);
    resolveActivation({ bot_id: 'b1', status: 'REACTIVATING' });
    await pending;
  });
  expect(run).toHaveBeenCalledTimes(1);
  expect(run).toHaveBeenCalledWith('activate', bot, '149608');
  expect(useBotWorkshopStore.getState().items[0]).toMatchObject({ lifecycle: 'deploying', rawStatus: 'REACTIVATING' });
  expect(useBotWorkshopStore.getState().items[0].actions).not.toContain('activate');
  expect(load).toHaveBeenCalledTimes(1);
});

test('failure leaves the bot recoverable and allows retry', async () => {
  useBotWorkshopStore.setState({ items: [bot] });
  const run = jest
    .spyOn(botManagementActionService, 'run')
    .mockRejectedValueOnce(new Error('failed'))
    .mockResolvedValueOnce({ bot_id: 'b1', status: 'ACTIVE' });
  const load = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotWorkshopActions(load, '149608'));
  await act(async () => {
    await expect(result.current('activate', bot)).rejects.toThrow('failed');
  });
  expect(useBotWorkshopStore.getState().items[0]).toEqual(bot);
  expect(toast.error).toHaveBeenCalled();
  await act(async () => {
    await result.current('activate', bot);
  });
  expect(run).toHaveBeenCalledTimes(2);
  expect(useBotWorkshopStore.getState().items[0].lifecycle).toBe('running');
});
