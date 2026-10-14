/** @jest-environment jsdom */
// sessionKey 搜索 Popover 契约(spec 2026-10-14 session-key-search):
// - 受控 value(已生效条件)/onSearch(提交,空串=清除);弹层内仅为草稿,键入不发请求;
// - Enter 与「搜索」按钮同一提交路径;提交 trim 首尾空白、保留内部内容;
// - 关闭弹层丢弃未提交草稿,再次打开回到当前已生效条件;
// - 生效条件以主色浅底 + 数字 1 标签标注(dmore 截图 2.png)。
import { ConversationSessionSearch } from '@/pages/Workspace/Chat/components/ConversationSessionSearch';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

/** 受控装配:value 保存最近一次提交结果,模拟 Store setter + 回读的真实数据流。 */
function Harness({ initial = '' }: { initial?: string } = {}) {
  const [value, setValue] = useState(initial);
  return <ConversationSessionSearch value={value} onSearch={setValue} />;
}

function getTrigger() {
  return screen.getByRole('button', { name: '搜索会话' });
}

function openSearch(): HTMLInputElement {
  fireEvent.click(getTrigger());
  return screen.getByPlaceholderText('sessionKey') as HTMLInputElement;
}

describe('ConversationSessionSearch', () => {
  it('弹层提供 sessionKey 输入与「搜索」按钮,打开即聚焦输入框', () => {
    render(<ConversationSessionSearch value="" onSearch={jest.fn()} />);
    const trigger = getTrigger();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(screen.getByPlaceholderText('sessionKey')).toHaveFocus();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '搜索' })).toBeInTheDocument();
  });

  it('键入草稿不触发任何查询', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionSearch value="" onSearch={onSearch} />);
    fireEvent.click(getTrigger());
    fireEvent.change(screen.getByPlaceholderText('sessionKey'), {
      target: { value: 'agent:main:draft' },
    });
    expect(onSearch).not.toHaveBeenCalled();
  });

  it('Enter 提交:trim 首尾空白、保留内部内容并关闭弹层', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionSearch value="" onSearch={onSearch} />);
    const trigger = getTrigger();
    fireEvent.click(trigger);
    const input = screen.getByPlaceholderText('sessionKey');
    fireEvent.change(input, { target: { value: ' agent:main:sn-1 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('agent:main:sn-1');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('点击「搜索」按钮与 Enter 行为一致', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionSearch value="" onSearch={onSearch} />);
    const trigger = getTrigger();
    fireEvent.click(trigger);
    fireEvent.change(screen.getByPlaceholderText('sessionKey'), {
      target: { value: 'agent:main:k2' },
    });
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));
    expect(onSearch).toHaveBeenCalledWith('agent:main:k2');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('空白条件提交空串,清除既有效果', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionSearch value="agent:main:old" onSearch={onSearch} />);
    const input = openSearch();
    expect(input.value).toBe('agent:main:old');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearch).toHaveBeenCalledWith('');
  });

  it('关闭弹层丢弃未提交草稿,重开回到已生效条件;提交后重开同步最新条件', () => {
    const onSearch = jest.fn();
    const ui = render(<ConversationSessionSearch value="" onSearch={onSearch} />);
    const trigger = getTrigger();
    fireEvent.click(trigger);
    let input = screen.getByPlaceholderText('sessionKey') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'agent:main:discard-me' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onSearch).not.toHaveBeenCalled();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    // 重开:弹层回到已生效条件(空),被丢弃的草稿不复现。
    fireEvent.click(trigger);
    input = screen.getByPlaceholderText('sessionKey') as HTMLInputElement;
    expect(input.value).toBe('');

    // 提交生效后由父级受控回写,重开同样回到最新已生效条件。
    fireEvent.change(input, { target: { value: ' agent:main:applied ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearch).toHaveBeenCalledWith('agent:main:applied');
    ui.rerender(<ConversationSessionSearch value="agent:main:applied" onSearch={onSearch} />);
    fireEvent.click(trigger);
    expect((screen.getByPlaceholderText('sessionKey') as HTMLInputElement).value).toBe('agent:main:applied');
  });

  it('生效条件高亮触发按钮并展示数字 1 标签;未生效不出现标签', () => {
    const { rerender } = render(<ConversationSessionSearch value="agent:main:k1" onSearch={jest.fn()} />);
    const trigger = getTrigger();
    expect(trigger).toHaveClass('bg-primary/10');
    expect(trigger).toHaveClass('text-primary');
    const badge = screen.getByText('1');
    expect(badge).toHaveClass('bg-primary');
    expect(badge).toHaveClass('text-primary-foreground');
    expect(badge).toHaveAttribute('aria-hidden', 'true');

    rerender(<ConversationSessionSearch value="" onSearch={jest.fn()} />);
    expect(trigger).not.toHaveClass('bg-primary/10');
    expect(screen.queryByText('1')).not.toBeInTheDocument();
  });

  it('受控流:清空提交传播空串,父级回写后按钮恢复默认态', () => {
    render(<Harness initial="agent:main:h1" />);
    const input = openSearch();
    expect(input.value).toBe('agent:main:h1');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    // Harness 将空提交写回 value,按钮不再高亮、标签消失。
    expect(getTrigger()).not.toHaveClass('bg-primary/10');
    expect(screen.queryByText('1')).not.toBeInTheDocument();
  });
});