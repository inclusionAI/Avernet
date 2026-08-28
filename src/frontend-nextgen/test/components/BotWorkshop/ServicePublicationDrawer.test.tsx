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
