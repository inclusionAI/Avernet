/** @jest-environment jsdom */
import { ChoiceGroup } from '@/components/CollaborationPrivacy/ChoiceGroup';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const options = [
  { value: 'none' as const, label: '不公开', description: '其他用户无法发现当前 Bot' },
  { value: 'all' as const, label: '全部公开', description: '其他用户可发现当前 Bot' },
  { value: 'restricted' as const, label: '指定组织', description: '仅所选组织范围可发现当前 Bot' },
];

describe('ChoiceGroup', () => {
  it('exposes radio semantics and keeps only the selected option tabbable', () => {
    render(<ChoiceGroup value="all" options={options} ariaLabel="公开范围" onChange={jest.fn()} />);

    expect(screen.getByRole('radiogroup', { name: '公开范围' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /全部公开/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /全部公开/ })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: /不公开/ })).toHaveAttribute('tabindex', '-1');
  });

  it('changes selection with arrow keys and wraps at the ends', () => {
    const onChange = jest.fn();
    render(<ChoiceGroup value="all" options={options} ariaLabel="公开范围" onChange={onChange} />);
    const selected = screen.getByRole('radio', { name: /全部公开/ });

    fireEvent.keyDown(selected, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('restricted');
    expect(screen.getByRole('radio', { name: /指定组织/ })).toHaveFocus();

    fireEvent.keyDown(screen.getByRole('radio', { name: /指定组织/ }), { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('none');
    expect(screen.getByRole('radio', { name: /不公开/ })).toHaveFocus();
  });
});
