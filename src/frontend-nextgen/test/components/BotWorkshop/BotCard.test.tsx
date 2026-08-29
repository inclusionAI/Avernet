/** @jest-environment jsdom */

import BotCard from '@/components/BotWorkshop/BotCard';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

const noop = () => undefined;

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

describe('BotCard health check action', () => {
  test('renders health check when availability is visible', () => {
    const bot = mapBotDto({ bot_id: 'b1', bot_name: 'Openclaw Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    render(
      <BotCard
        bot={bot}
        onView={noop}
        onHealthCheck={noop}
        healthCheckAvailability={{ action: 'health-check', visible: true, enabled: true }}
      />,
    );

    expect(screen.getByRole('button', { name: '健康检查' })).toBeInTheDocument();
  });

  test('hides health check when availability is invisible', () => {
    const bot = mapBotDto({ bot_id: 'b2', bot_name: 'TEClaw Bot', engine: 'teclaw', status: 'ACTIVE' }).item;
    render(
      <BotCard
        bot={bot}
        onView={noop}
        onHealthCheck={noop}
        healthCheckAvailability={{ action: 'health-check', visible: false, enabled: false }}
      />,
    );

    expect(screen.queryByRole('button', { name: '健康检查' })).not.toBeInTheDocument();
  });
});

describe('BotCard conversation action', () => {
  test('renders the conversation entry and delegates navigation', () => {
    const bot = mapBotDto({ bot_id: 'b3', bot_name: 'Chat Bot', engine: 'openclaw', status: 'ACTIVE' }).item;
    const onConversation = jest.fn();

    render(<BotCard bot={bot} onView={noop} onConversation={onConversation} />);
    fireEvent.click(screen.getByRole('button', { name: '对话' }));

    expect(onConversation).toHaveBeenCalledWith(bot);
  });
});

describe('BotCard management actions', () => {
  test('closes the management menu before opening delete confirmation', () => {
    const bot = mapBotDto({
      bot_id: 'b4',
      bot_name: 'Delete Bot',
      engine: 'openclaw',
      status: 'ACTIVE',
      actions: ['delete'],
    }).item;

    render(<BotCard bot={bot} onView={noop} onAction={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '管理 Delete Bot' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText('确认删除 Bot')).toBeInTheDocument();
    expect(screen.queryByText('变更归属空间')).not.toBeInTheDocument();
  });

  test('shows a red lock and confirms stealing another editor lock', async () => {
    const onClaimLock = jest.fn().mockResolvedValue(undefined);
    const bot = {
      ...mapBotDto({
        bot_id: 'b5',
        bot_name: 'Locked Service Bot',
        bot_type: 'service',
        kind: 'service',
        engine: 'openclaw',
        display_state: 'service_draft',
      }).item,
      ownership: 'team' as const,
      lock: { status: 'other' as const, holderName: '李四', lockedAt: '2026-08-26 10:00' },
    };

    render(<BotCard bot={bot} onView={noop} onClaimLock={onClaimLock} />);
    fireEvent.click(screen.getByRole('button', { name: '抢占 Locked Service Bot 的编辑锁' }));

    const dialog = within(screen.getByRole('alertdialog'));
    expect(dialog.getByText('该 Bot 正在被编辑')).toBeInTheDocument();
    expect(dialog.getByText(/李四/)).toBeInTheDocument();
    expect(dialog.getByText(/2026-08-26 10:00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '抢锁并编辑' }));

    expect(onClaimLock).toHaveBeenCalledWith(bot);
  });
});
