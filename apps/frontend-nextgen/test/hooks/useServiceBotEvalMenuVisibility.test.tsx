/** @jest-environment jsdom */
import { useServiceBotEvalMenuVisibility } from '@/hooks/useServiceBotEvalMenuVisibility';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop', () => ({
  canOpenServiceBotEvalHistory: jest.fn(() => true),
}));

jest.mock('@/services/botWorkshop/serviceBotEvalService', () => ({
  serviceBotEvalService: {
    detail: jest.fn(),
  },
}));

const createServiceBot = (botId: string, lifecycle: 'draft' | 'running') => {
  const bot = mapBotDto({
    bot_id: botId,
    card_id: `${botId}:1`,
    bot_name: `Service bot ${botId}`,
    bot_type: 'service',
    kind: 'service',
    engine: 'openclaw',
    display_state: lifecycle === 'draft' ? 'DRAFT' : 'ACTIVE',
    owner_entity_id: 'owner-id',
  }).item;
  bot.lifecycle = lifecycle;
  return bot;
};

const createPersonalBot = () =>
  mapBotDto({
    bot_id: 'personal-bot',
    bot_name: 'Personal bot',
    bot_type: 'personal',
    kind: 'personal',
    engine: 'openclaw',
    display_state: 'ACTIVE',
    owner_entity_id: 'owner-id',
  }).item;

it('queries gate visibility for service bots across lifecycle states and reuses it for both evaluation menus', async () => {
  const draftBot = createServiceBot('draft-bot', 'draft');
  const runningBot = createServiceBot('running-bot', 'running');
  const personalBot = createPersonalBot();

  const detail = serviceBotEvalService.detail as jest.Mock;
  detail.mockImplementation(async (botId: string) => ({
    tabs: { simple_cert_submit: { visible: botId === 'draft-bot' } },
  }));

  const botList = [draftBot, runningBot, personalBot];

  const { result, unmount } = renderHook(() => useServiceBotEvalMenuVisibility(botList, 'owner-id'));

  await waitFor(() => expect(result.current[0].evalGateVisible).toBe(true));
  expect(result.current[1].evalGateVisible).toBe(false);
  expect(result.current[2].evalGateVisible).toBe(false);
  expect(detail).toHaveBeenCalledTimes(2);
  expect(detail).toHaveBeenCalledWith('draft-bot', 'latest', 'owner-id');
  expect(detail).toHaveBeenCalledWith('running-bot', 'latest', 'owner-id');
  expect(result.current.map((bot) => bot.canOpenEvalHistory)).toEqual([true, true, true]);

  unmount();
  jest.clearAllMocks();
});
