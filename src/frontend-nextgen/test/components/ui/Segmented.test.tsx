/** @jest-environment jsdom */
import { Segmented } from '@/components/ui/Segmented';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('Segmented accessibility', () => {
  test('exposes the current option through aria-pressed and an accessible group name', () => {
    render(
      <Segmented
        aria-label="Bot 搜索模式"
        value="name"
        options={[
          { value: 'name', label: '名称搜索' },
          { value: 'smart', label: '智能搜索' },
        ]}
        onChange={jest.fn()}
      />,
    );

    expect(screen.getByRole('group', { name: 'Bot 搜索模式' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '名称搜索' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '智能搜索' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('复用 Button 基类，可点击项带手型、禁用项退回 not-allowed', () => {
    render(
      <Segmented
        aria-label="状态筛选"
        value="all"
        options={[
          { value: 'all', label: '全部' },
          { value: 'off', label: '不可用', disabledReason: '暂不可用' },
        ]}
        onChange={jest.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '全部' })).toHaveClass('cursor-pointer');
    const disabled = screen.getByRole('button', { name: '不可用' });
    expect(disabled).toBeDisabled();
    expect(disabled).toHaveClass('disabled:cursor-not-allowed');
  });

  test('supports arrow-key selection and skips disabled options', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    render(
      <Segmented
        aria-label="状态筛选"
        value="all"
        options={[
          { value: 'all', label: '全部' },
          { value: 'disabled', label: '不可用', disabledReason: '暂不可用' },
          { value: 'completed', label: '已完成' },
        ]}
        onChange={onChange}
      />,
    );

    const allOption = screen.getByRole('button', { name: '全部' });
    const completedOption = screen.getByRole('button', { name: '已完成' });
    allOption.focus();
    await user.keyboard('{ArrowRight}');

    expect(onChange).toHaveBeenCalledWith('completed');
    expect(completedOption).toHaveFocus();
  });
});
