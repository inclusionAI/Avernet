/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { BotStatusPublicationMenu } from '@/components/BotWorkshop/BotCard/BotStatusPublicationMenu';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

test('悬停展开，跨越浮层间隙后仍可点击发布，移出后关闭', () => {
  jest.useFakeTimers();
  try {
    const bot = mapBotDto({ bot_id: 'service', bot_name: '服务 Bot', bot_type: 'service', kind: 'service', display_state: 'service_draft', actions: ['publish_staging'] }).item;
    const onManagePublication = jest.fn();
    render(<BotStatusPublicationMenu bot={bot} onManagePublication={onManagePublication}>草稿</BotStatusPublicationMenu>);
    const trigger = screen.getByRole('button', { name: '服务 Bot 发布状态' });
    expect(screen.queryByText('发布与阶段推进')).not.toBeInTheDocument();
    fireEvent.mouseEnter(trigger);
    const action = screen.getByRole('button', { name: '发布与阶段推进' });
    expect(document.activeElement).not.toBe(action);
    fireEvent.mouseLeave(trigger);
    act(() => jest.advanceTimersByTime(75));
    fireEvent.mouseEnter(screen.getByRole('dialog'));
    act(() => jest.advanceTimersByTime(200));
    expect(action).toBeInTheDocument();
    fireEvent.click(action);
    expect(onManagePublication).toHaveBeenCalledWith(bot);
    fireEvent.mouseEnter(trigger);
    fireEvent.mouseLeave(trigger);
    act(() => jest.advanceTimersByTime(200));
    expect(screen.queryByText('发布与阶段推进')).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});
