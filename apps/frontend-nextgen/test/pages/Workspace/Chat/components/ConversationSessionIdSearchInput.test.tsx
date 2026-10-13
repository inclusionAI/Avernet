/** @jest-environment jsdom */
// 会话 ID 检索输入框契约(展开区顶部,回车提交语义,与 sessionKey 搜索提交逻辑配套):
// - 受控 value(已生效条件)/onSearch(提交,空串=清除);键入仅落本地草稿,不发请求;
// - Enter 为唯一提交路径;提交 trim 首尾空白、保留内部内容;
// - 已生效条件变化时草稿同步回当前条件;
// - 清空按钮仅随已生效条件出现,点击提交空串恢复全列表。
import { ConversationSessionIdSearchInput } from '@/pages/Workspace/Chat/components/ConversationSessionIdSearchInput';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

/** 受控装配:value 保存最近一次提交结果,模拟 Store setter + 回读的真实数据流。 */
function Harness({ initial = '' }: { initial?: string } = {}) {
  const [value, setValue] = useState(initial);
  return <ConversationSessionIdSearchInput value={value} onSearch={setValue} />;
}

function getInput(): HTMLInputElement {
  return screen.getByRole('textbox', { name: '会话ID检索' }) as HTMLInputElement;
}

describe('ConversationSessionIdSearchInput', () => {
  it('提供检索输入框;未生效条件不出现清空按钮,已生效条件出现清空按钮', () => {
    const { rerender } = render(<ConversationSessionIdSearchInput value="" onSearch={jest.fn()} />);
    expect(screen.getByPlaceholderText('输入sessionID回车检索会话')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清空会话ID检索' })).not.toBeInTheDocument();
    rerender(<ConversationSessionIdSearchInput value="agent:main:k1" onSearch={jest.fn()} />);
    expect(getInput().value).toBe('agent:main:k1');
    expect(screen.getByRole('button', { name: '清空会话ID检索' })).toBeInTheDocument();
  });

  it('键入草稿不触发任何提交', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionIdSearchInput value="" onSearch={onSearch} />);
    fireEvent.change(getInput(), { target: { value: 'agent:main:draft' } });
    expect(onSearch).not.toHaveBeenCalled();
  });

  it('Enter 提交:trim 首尾空白、保留内部内容', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionIdSearchInput value="" onSearch={onSearch} />);
    const input = getInput();
    fireEvent.change(input, { target: { value: ' agent:main:sn-1 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('agent:main:sn-1');
  });

  it('空白条件提交空串,清除既有效果', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionIdSearchInput value="agent:main:old" onSearch={onSearch} />);
    const input = getInput();
    expect(input.value).toBe('agent:main:old');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearch).toHaveBeenCalledWith('');
  });

  it('清空按钮提交空串恢复全列表', () => {
    const onSearch = jest.fn();
    render(<ConversationSessionIdSearchInput value="agent:main:k2" onSearch={onSearch} />);
    fireEvent.click(screen.getByRole('button', { name: '清空会话ID检索' }));
    expect(onSearch).toHaveBeenCalledWith('');
  });

  it('受控流:提交生效后条件由父级回写,输入草稿同步最新已生效条件', () => {
    const { rerender } = render(<Harness />);
    const input = getInput();
    fireEvent.change(input, { target: { value: 'agent:main:applied' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    rerender(<Harness initial="agent:main:applied" />);
    expect(getInput().value).toBe('agent:main:applied');
  });
});