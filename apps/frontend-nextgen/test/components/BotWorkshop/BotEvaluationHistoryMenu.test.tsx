/** @jest-environment jsdom */

import { BotManagementRuntimeItems } from '@/components/BotWorkshop/BotCard/BotManagementRuntimeItems';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

describe('Bot management evaluation history', () => {
  test.each([
    ['aicoding', 'service', false],
    ['applicationcoding', 'service', false],
    ['claude_code', 'service', false],
    ['claude_code', 'personal', false],
    ['openclaw', 'service', true],
    ['openclaw', 'personal', false],
  ] as const)('%s %s history visibility is %s', (engine, botType, visible) => {
    const bot = mapBotDto({
      bot_id: 'evaluation-bot', bot_name: 'Evaluation Bot',
      active_engine: engine, bot_type: botType, display_state: 'running',
      template_type: engine === 'claude_code' ? 'myTemplate' : undefined,
      actions: ['view', 'chat'],
    }).item;
    bot.canOpenEvalHistory = visible;
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
    const entry = screen.queryByRole('button', { name: '历史评测记录' });
    if (visible) {
      expect(entry).toBeInTheDocument();
      fireEvent.click(entry!);
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onOpenEvalHistory).toHaveBeenCalledWith(bot);
    } else {
      expect(entry).not.toBeInTheDocument();
      expect(onOpenEvalHistory).not.toHaveBeenCalled();
    }
  });
});
