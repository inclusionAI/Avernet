/** @jest-environment jsdom */
import { ServicePublicationDrawer } from '@/components/BotWorkshop/ServicePublicationDrawer';
import { useServicePublications } from '@/hooks/useServicePublications';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/hooks/useServicePublications', () => ({ useServicePublications: jest.fn() }));

const mockedPublications = useServicePublications as jest.MockedFunction<typeof useServicePublications>;

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
});

test('在发布与阶段推进中展示并确认升级服务 Bot 版本', async () => {
  const upgrade = jest.fn().mockResolvedValue(undefined);
  mockedPublications.mockReturnValue({
    items: [
      {
        publicationId: 17,
        cardId: 'service:bot-1:17',
        version: 2,
        status: 'running',
        internalStatus: 'success',
        availableActions: ['upgrade'],
        createdAt: '2026-08-26T10:00:00Z',
        updatedAt: '2026-08-26T10:00:00Z',
      },
    ],
    loading: false,
    reload: jest.fn(),
    advance: jest.fn(),
    restart: jest.fn(),
    cancel: jest.fn(),
    offline: jest.fn(),
    retry: jest.fn(),
    upgrade,
    deleteDraft: jest.fn(),
  });
  const bot = mapBotDto({
    bot_id: 'bot-1',
    bot_name: 'Service Bot',
    bot_type: 'service',
    kind: 'service',
    engine: 'openclaw',
    display_state: 'service_online',
  }).item;

  render(<ServicePublicationDrawer bot={bot} onClose={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '升级' }));
  fireEvent.click(screen.getByRole('button', { name: '确认升级' }));

  expect(upgrade).toHaveBeenCalledWith(17);
});

test('发布与阶段推进不混入重启发布和删除', () => {
  mockedPublications.mockReturnValue({
    items: [
      {
        publicationId: 18,
        cardId: 'bot-2:18',
        version: 1,
        status: 'running',
        internalStatus: 'success',
        availableActions: ['publish_online', 'restart_publish', 'delete'],
        createdAt: '',
        updatedAt: '',
      },
    ],
    loading: false,
    reload: jest.fn(),
    advance: jest.fn(),
    restart: jest.fn(),
    cancel: jest.fn(),
    offline: jest.fn(),
    retry: jest.fn(),
    upgrade: jest.fn(),
    deleteDraft: jest.fn(),
  });
  const bot = mapBotDto({ bot_id: 'bot-2', bot_name: 'Service Bot', engine: 'openclaw', bot_type: 'service' }).item;
  render(<ServicePublicationDrawer bot={bot} onClose={jest.fn()} />);
  expect(screen.getByRole('button', { name: '发布上线' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重启发布' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '删除草稿' })).not.toBeInTheDocument();
});
