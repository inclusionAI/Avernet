/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { formatSessionTime, SessionCard } from '@/pages/Workspace/components/SessionCard';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('SessionCard', () => {
  it('按固定时间分层展示相对时间', () => {
    const now = new Date(2026, 8, 4, 15, 30);
    expect(formatSessionTime('2026-09-04T09:05:00', now)).toBe('09:05');
    expect(formatSessionTime('2026-09-03T23:05:00', now)).toBe('昨天');
    expect(formatSessionTime('2026-09-01T12:00:00', now)).toBe('周二');
    expect(formatSessionTime('2026-08-30T12:00:00', now)).toBe('08/30');
    expect(formatSessionTime('2025-12-31T12:00:00', now)).toBe('2025/12/31');
    expect(formatSessionTime('not-a-date', now)).toBe('');
  });

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
    expect(trigger.parentElement).toHaveClass(
      'min-h-[60px]',
      'border-b',
      'last:border-b-0',
      'bg-background',
      'hover:bg-primary/5',
    );
    trigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('使用圆点强化会话识别，选中态同步品牌色', () => {
    const { container, rerender } = render(
      <SessionCard title="普通会话" subtitle="暂无消息" selected={false} onSelect={jest.fn()} />,
    );

    const indicator = container.querySelector('[data-session-indicator]');
    expect(indicator).toHaveClass(
      'h-1.5',
      'w-1.5',
      'rounded-full',
      'bg-muted-foreground/50',
      'group-hover:bg-primary/60',
    );
    expect(container.querySelector('svg.lucide-message-square')).not.toBeInTheDocument();

    rerender(<SessionCard title="当前会话" subtitle="3 条消息" selected onSelect={jest.fn()} />);

    expect(container.querySelector('[data-session-indicator]')).toHaveClass('bg-primary');
    expect(screen.getByText('当前会话')).toHaveClass('font-medium', 'text-primary');
    expect(screen.getByText('3 条消息')).toHaveClass('text-primary/80');
  });

  it('Bot 与协作群会话都支持垂直居中的消息 Icon', () => {
    const { container, rerender } = render(
      <SessionCard title="Bot 会话" subtitle="暂无消息" indicator="message" selected={false} onSelect={jest.fn()} />,
    );

    const botMessageIcon = container.querySelector('svg.lucide-message-square');
    expect(botMessageIcon).toBeInTheDocument();
    expect(botMessageIcon?.parentElement).toHaveClass('self-center');

    rerender(
      <SessionCard title="协作群会话" subtitle="" indicator="message" selected={false} onSelect={jest.fn()} compact />,
    );
    const groupMessageIcon = container.querySelector('svg.lucide-message-square');
    expect(groupMessageIcon).toBeInTheDocument();
    expect(groupMessageIcon?.parentElement).toHaveClass('self-center');
  });

  it('选中会话输出当前态，但不复用对象行左侧指示线', () => {
    const { container } = render(<SessionCard title="当前会话" selected onSelect={jest.fn()} />);
    expect(screen.getByRole('button', { name: /当前会话/ })).toHaveAttribute('aria-current', 'page');
    expect(container.querySelector('[class~="w-0.5"][class~="bg-primary"]')).not.toBeInTheDocument();
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
    expect(compactTrigger).toHaveClass('min-h-12', 'items-center', 'py-2');
    expect(compactTrigger.parentElement).toHaveClass('min-h-12');
    expect(compactTrigger.parentElement?.lastElementChild).toHaveClass('items-center');

    rerender(
      <SessionCard title="Bot 会话" subtitle="3 条消息" dateText="08/30 12:00" selected={false} onSelect={jest.fn()} />,
    );

    const standardTrigger = screen.getByRole('button', { name: /Bot 会话/ });
    expect(standardTrigger).toHaveClass('min-h-[60px]', 'py-2.5');
    expect(standardTrigger.parentElement).toHaveClass('min-h-[60px]');
    expect(standardTrigger.parentElement?.lastElementChild).toHaveClass('items-center', 'pt-2');
    expect(standardTrigger.parentElement?.lastElementChild).not.toHaveClass('items-start', 'pt-3');
  });
});
