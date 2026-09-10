/** @jest-environment jsdom */
import {
  MessageViewScopeBadge,
  MessageViewScopeCheckbox,
  MessageViewScopeField,
  MessageViewScopeMenuButton,
} from '@/components/MessageViewScope';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

describe('MessageViewScopeMenuButton', () => {
  it('主按钮打开菜单，点选项回调对应 scope 并关闭', () => {
    const onSelect = jest.fn();
    render(<MessageViewScopeMenuButton onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
    fireEvent.click(screen.getByText('参与者视角'));
    expect(onSelect).toHaveBeenCalledWith('participant');
  });

  it('菜单默认勾选完整视角（高亮 + 勾图标可见）', () => {
    render(<MessageViewScopeMenuButton onSelect={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
    const fullOption = screen.getByText('完整视角').closest('button');
    expect(fullOption).toHaveClass('border-primary');
    const participantOption = screen.getByText('参与者视角').closest('button');
    expect(participantOption).not.toHaveClass('border-primary');
  });
});

describe('MessageViewScopeCheckbox', () => {
  it('勾选回调 true 并展示提示文案', () => {
    const onCheckedChange = jest.fn();
    render(<MessageViewScopeCheckbox checked={false} onCheckedChange={onCheckedChange} />);
    expect(screen.getByText('未勾选时，可看到会话内的全部消息。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});

describe('MessageViewScopeBadge', () => {
  it('full 渲染「完整视角」且为 primary tone', () => {
    render(<MessageViewScopeBadge scope="full" />);
    expect(screen.getByText('完整视角')).toHaveClass('bg-primary/10', 'text-primary');
  });

  it('participant 渲染「参与者视角」且为 neutral tone', () => {
    render(<MessageViewScopeBadge scope="participant" />);
    expect(screen.getByText('参与者视角')).toHaveClass('bg-muted', 'text-muted-foreground');
  });
});

describe('MessageViewScopeField', () => {
  it('受控 value=full 时首个 radio 选中；切换回调 participant', () => {
    const onChange = jest.fn();
    render(<MessageViewScopeField value="full" onChange={onChange} />);
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    expect(radios[0]).toBeChecked();
    fireEvent.click(radios[1]);
    expect(onChange).toHaveBeenCalledWith('participant');
  });
});
