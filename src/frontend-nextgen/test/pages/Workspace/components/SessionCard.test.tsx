/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { SessionCard } from '@/pages/Workspace/components/SessionCard';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('SessionCard', () => {
  it('会话主触发区支持键盘选择,右侧操作不触发选择', async () => {
    const onSelect = jest.fn();
    const onAction = jest.fn();
    render(
      <SessionCard
        title="项目会话"
        subtitle="3 条消息"
        dateText="08/30 12:00"
        selected={false}
        onSelect={onSelect}
        trailing={<Button aria-label="会话操作" variant="ghost" size="icon" onClick={onAction} />}
      />,
    );

    const trigger = screen.getByRole('button', { name: /项目会话/ });
    expect(trigger).toHaveAttribute('aria-pressed', 'false');
    expect(trigger.parentElement).toHaveClass('min-h-[70px]', 'border-t', 'bg-background');
    trigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('无副行内容时使用紧凑行高，保留标准会话的双行高度', () => {
    const { rerender } = render(
      <SessionCard
        title="协作群会话"
        subtitle=""
        dateText="08/30 12:00"
        selected={false}
        onSelect={jest.fn()}
        compact
      />,
    );

    const compactTrigger = screen.getByRole('button', { name: '协作群会话' });
    expect(compactTrigger).toHaveClass('min-h-[56px]', 'items-center', 'py-2');
    expect(compactTrigger.parentElement).toHaveClass('min-h-[56px]');
    expect(compactTrigger.parentElement?.lastElementChild).toHaveClass('items-center');

    rerender(
      <SessionCard title="Bot 会话" subtitle="3 条消息" dateText="08/30 12:00" selected={false} onSelect={jest.fn()} />,
    );

    const standardTrigger = screen.getByRole('button', { name: /Bot 会话/ });
    expect(standardTrigger).toHaveClass('min-h-[70px]', 'py-3');
    expect(standardTrigger.parentElement).toHaveClass('min-h-[70px]');
  });
});
