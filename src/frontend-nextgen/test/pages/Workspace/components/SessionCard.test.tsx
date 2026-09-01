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
    trigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
