/** @jest-environment jsdom */
import { ChoiceGroup } from '@/components/CollaborationPrivacy/ChoiceGroup';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const options = [
  {
    value: 'none' as const,
    label: '不可见',
    description: '其他用户以个人身份，无法在协作广场看到当前 Bot，也不能发起申请。',
  },
  { value: 'all' as const, label: '全部可见', description: '其他用户以个人身份，在协作广场可见当前 Bot 并申请好友。' },
  {
    value: 'restricted' as const,
    label: '限定组织可申请',
    description: '其他用户以个人身份，在协作广场可见当前 Bot，但仅选中组织范围的用户可申请好友。',
  },
];

describe('ChoiceGroup', () => {
  it('exposes radio semantics and keeps only the selected option tabbable', () => {
    render(<ChoiceGroup value="all" options={options} ariaLabel="Bot 可见性" onChange={jest.fn()} />);

    expect(screen.getByRole('radiogroup', { name: 'Bot 可见性' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /全部可见/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /全部可见/ })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: /不可见/ })).toHaveAttribute('tabindex', '-1');
  });

  it('changes selection with arrow keys and wraps at the ends', () => {
    const onChange = jest.fn();
    render(<ChoiceGroup value="all" options={options} ariaLabel="Bot 可见性" onChange={onChange} />);
    const selected = screen.getByRole('radio', { name: /全部可见/ });

    fireEvent.keyDown(selected, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('restricted');
    expect(screen.getByRole('radio', { name: /限定组织可申请/ })).toHaveFocus();

    fireEvent.keyDown(screen.getByRole('radio', { name: /限定组织可申请/ }), { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('none');
    expect(screen.getByRole('radio', { name: /不可见/ })).toHaveFocus();
  });
});
