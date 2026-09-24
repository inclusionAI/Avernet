/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { formatSessionTime, SessionCard } from '@/pages/Workspace/components/SessionCard';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
    // 验收微调：会话行去底部分割线与行底色，保留 hover 反馈。
    expect(trigger.parentElement).toHaveClass('min-h-15', 'hover:bg-primary/5');
    expect(trigger.parentElement).not.toHaveClass('border-b', 'last:border-b-0', 'bg-background');
    trigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('验收微调：树形导轨——行自带干线、拐角横线紧贴干线接行首，末行干线止于拐角', () => {
    const { container, rerender } = render(
      <SessionCard title="拐角会话" subtitle="" indicator="message" selected={false} onSelect={jest.fn()} compact />,
    );
    const row = container.firstElementChild as HTMLElement;
    // 干线由行自带（-left-2 = 容器缩进 16px 处），非末行贯穿整行。
    const rail = row.querySelector('[data-session-tree-rail]') as HTMLElement | null;
    expect(rail).toHaveClass('-left-2', 'top-0', 'bottom-0', 'w-px', 'bg-border');
    // 末行干线止于拐角高度（CSS :last-child 变体驱动，jsdom 无法计算行为，锁定类名契约）。
    expect(row).toHaveClass('last:[&_[data-session-tree-rail]]:bottom-1/2');
    // 拐角横线 8px：左端紧贴干线、右端无缝接到会话卡片行首。
    const elbow = row.querySelector('[data-session-tree-elbow]') as HTMLElement | null;
    expect(elbow).toHaveClass('-left-2', 'top-1/2', 'h-px', 'w-2', 'bg-border');
    // 未选中：行首不渲染品牌条（替代原常显 1px 弱灰竖线）。
    expect(row.querySelector('[data-session-left-bar]')).toBeNull();

    rerender(<SessionCard title="拐角会话" subtitle="" indicator="message" selected onSelect={jest.fn()} compact />);
    const bar = row.querySelector('[data-session-left-bar]') as HTMLElement | null;
    expect(bar).toHaveClass('left-0', 'w-0.5', 'bg-primary');
    // 导轨在选中态仍保留（从属关系不因选中消失）。
    expect(row.querySelector('[data-session-tree-rail]')).not.toBeNull();
    expect(row.querySelector('[data-session-tree-elbow]')).not.toBeNull();
  });

  it('v1.4：会话选中态为品牌浅底 + 标题品牌色，与父行灰底异色系分工', () => {
    const { rerender, container } = render(<SessionCard title="当前会话" selected onSelect={jest.fn()} />);
    expect(screen.getByText('当前会话')).toHaveClass('font-medium', 'text-primary');
    const row = container.firstElementChild as HTMLElement;
    expect(row).toHaveClass('bg-primary/10');
    expect(row).not.toHaveClass('bg-muted');

    rerender(<SessionCard title="未选会话" selected={false} onSelect={jest.fn()} />);
    const unselectedRow = container.firstElementChild as HTMLElement;
    // 验收微调：未选中行不再铺行底色（透明融入侧栏底色）。
    expect(unselectedRow).not.toHaveClass('bg-background');
  });

  it('v1.4：会话名称截断时悬停显示完整标题 Tooltip', async () => {
    // jsdom 无布局：mock 溢出检测（scrollWidth > clientWidth + 1）
    Object.defineProperty(HTMLSpanElement.prototype, 'scrollWidth', { configurable: true, get: () => 120 });
    Object.defineProperty(HTMLSpanElement.prototype, 'clientWidth', { configurable: true, get: () => 60 });
    try {
      render(
        <SessionCard
          title="这是一个很长很长会被截断的会话标题"
          subtitle=""
          compact
          selected={false}
          onSelect={jest.fn()}
        />,
      );
      const titleSpan = screen.getByText('这是一个很长很长会被截断的会话标题');
      fireEvent.focus(titleSpan);
      // Tooltip 渲染完整标题（与行内标题同名，共两处）
      await waitFor(() => expect(screen.getAllByText('这是一个很长很长会被截断的会话标题')).toHaveLength(2));
    } finally {
      delete (HTMLSpanElement.prototype as { scrollWidth?: number }).scrollWidth;
      delete (HTMLSpanElement.prototype as { clientWidth?: number }).clientWidth;
    }
  });

  it('v1.4：右侧日期与更多操作共用位置——默认显示日期，操作按钮叠放右缘悬停浮现', () => {
    const trailing = <Button aria-label="会话操作" variant="ghost" size="icon" onClick={jest.fn()} />;
    const { rerender } = render(
      <SessionCard
        title="操作会话"
        dateText="08/30"
        dateTooltip="2026-08-30 12:00"
        selected={false}
        onSelect={jest.fn()}
        trailing={trailing}
      />,
    );
    const getWrapper = () => screen.getByRole('button', { name: '会话操作' }).parentElement as HTMLElement;
    expect(getWrapper()).toHaveClass(
      'absolute',
      'right-3',
      'opacity-0',
      'transition-opacity',
      'group-hover:opacity-100',
      'group-focus-within:opacity-100',
    );
    const date = screen.getByText('08/30');
    expect(date).toHaveClass(
      'transition-opacity',
      '[@media(hover:hover)]:group-hover:opacity-0',
      '[@media(hover:hover)]:group-focus-within:opacity-0',
    );
    expect(date).not.toHaveClass('opacity-0');
    // 验收微调（对齐缺陷）：日期占固定宽度右对齐槽位——星标位于日期左侧，
    // 槽位定宽后星标 x 位置不随「昨天/周X/MM/DD」等格式宽度漂移。
    // 槽宽 36px（min-w-9）贴合最长常见格式，避免短日期与星标间大空隙。
    expect(date).toHaveClass('min-w-9', 'shrink-0', 'text-right');

    // 选中态：日期保持可见，操作按钮不常显（操作由悬停/聚焦触发）
    rerender(
      <SessionCard
        title="操作会话"
        dateText="08/30"
        dateTooltip="2026-08-30 12:00"
        selected
        onSelect={jest.fn()}
        trailing={trailing}
      />,
    );
    expect(getWrapper()).not.toHaveClass('opacity-100');
    expect(screen.getByText('08/30')).not.toHaveClass('opacity-0');
  });

  it('验收微调：不同宽度日期格式（昨天/09/02）均占定宽右对齐槽位，星标列恒定对齐', () => {
    const star = <Button aria-label="收藏会话" variant="ghost" size="icon" onClick={jest.fn()} />;
    const { unmount } = render(
      <SessionCard
        title="短日期会话"
        dateText="昨天"
        dateTooltip="2026-09-13 10:00"
        selected={false}
        onSelect={jest.fn()}
        persistentAction={star}
      />,
    );
    expect(screen.getByText('昨天')).toHaveClass('min-w-9', 'shrink-0', 'text-right');
    unmount();

    render(
      <SessionCard
        title="数字日期会话"
        dateText="09/02"
        dateTooltip="2026-09-02 14:30"
        selected={false}
        onSelect={jest.fn()}
        persistentAction={star}
      />,
    );
    expect(screen.getByText('09/02')).toHaveClass('min-w-9', 'shrink-0', 'text-right');
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

  it('选中会话输出当前态，并以左缘 2px 品牌竖条补强选中效果（v1.4）', () => {
    const { container } = render(<SessionCard title="当前会话" selected onSelect={jest.fn()} />);
    expect(screen.getByRole('button', { name: /当前会话/ })).toHaveAttribute('aria-current', 'page');
    expect(container.querySelector('[class~="w-0.5"][class~="bg-primary"]')).toBeInTheDocument();
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
    expect(standardTrigger).toHaveClass('min-h-15', 'py-2.5');
    expect(standardTrigger.parentElement).toHaveClass('min-h-15');
    expect(standardTrigger.parentElement?.lastElementChild).toHaveClass('items-center', 'pt-2');
    expect(standardTrigger.parentElement?.lastElementChild).not.toHaveClass('items-start', 'pt-3');
  });
});
