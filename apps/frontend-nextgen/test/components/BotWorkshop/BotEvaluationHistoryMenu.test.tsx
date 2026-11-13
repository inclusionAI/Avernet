/** @jest-environment jsdom */

import { BotManagementRuntimeItems } from '@/components/BotWorkshop/BotCard/BotManagementRuntimeItems';
import { mapBotDto } from '@/services/bots/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const createBot = (engine: string, botType: 'service' | 'personal', canOpenEvalHistory: boolean) => {
  const bot = mapBotDto({
    bot_id: 'evaluation-bot',
    bot_name: 'Evaluation Bot',
    active_engine: engine,
    bot_type: botType,
    display_state: 'running',
    template_type: engine === 'claude_code' ? 'myTemplate' : undefined,
    actions: ['view', 'chat'],
  }).item;
  bot.canOpenEvalHistory = canOpenEvalHistory;
  return bot;
};

const renderMenu = (bot: ReturnType<typeof createBot>) => {
  const onClose = jest.fn();
  const onOpenEvalHistory = jest.fn();
  render(
    <BotManagementRuntimeItems
      bot={bot}
      lockedByOther={false}
      onClose={onClose}
      onConfirm={jest.fn()}
      onOpenEvalHistory={onOpenEvalHistory}
    />,
  );
  return { onClose, onOpenEvalHistory };
};

describe('Bot management evaluation history', () => {
  test('openclaw service bot shows its authorized evaluation history entry', () => {
    const bot = createBot('openclaw', 'service', true);
    const { onClose, onOpenEvalHistory } = renderMenu(bot);

    const entry = screen.getByRole('button', { name: '历史评测记录' });
    fireEvent.click(entry);

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onOpenEvalHistory).toHaveBeenCalledWith(bot);
  });

  test.each([
    ['aicoding', 'service'],
    ['applicationcoding', 'service'],
    ['claude_code', 'service'],
    ['claude_code', 'personal'],
    ['openclaw', 'personal'],
  ] as const)('%s %s bot hides evaluation history', (engine, botType) => {
    const { onOpenEvalHistory } = renderMenu(createBot(engine, botType, false));

    expect(screen.queryByRole('button', { name: '历史评测记录' })).not.toBeInTheDocument();
    expect(onOpenEvalHistory).not.toHaveBeenCalled();
  });
});
