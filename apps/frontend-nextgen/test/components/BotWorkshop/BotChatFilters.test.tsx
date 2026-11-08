/** @jest-environment jsdom */
import { BotChatFilters } from '@/components/BotWorkshop/BotChatLogs/BotChatFilters';
import type { BotChatFilters as Filters } from '@/domain/botChats';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const emptyFilters: Filters = {
  traceId: '',
  sessionId: '',
  sessionKey: '',
  keyword: '',
  bizScene: '',
  bizTaskId: '',
  groupId: '',
  fromDate: '',
  toDate: '',
};

/** Radix 浮层在 jsdom 下需要 pointer capture 桩（仓库既有 DOM 测试同款）。 */
function stubPointerCapture() {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
}

describe('BotChatFilters 查询脊线', () => {
  it('高频字段常驻：时间起止、traceID、输入/输出与操作按钮均可见', () => {
    render(
      <BotChatFilters
        value={emptyFilters}
        loading={false}
        onChange={jest.fn()}
        onQuery={jest.fn()}
        onReset={jest.fn()}
      />,
    );
    expect(screen.getByLabelText('开始时间')).toBeInTheDocument();
    expect(screen.getByLabelText('结束时间')).toBeInTheDocument();
    expect(screen.getByLabelText('traceID')).toBeInTheDocument();
    expect(screen.getByLabelText('输入/输出')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更多筛选' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查询/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重置/ })).toBeInTheDocument();
  });

  it('低频字段有生效值时「更多筛选」显示数量徽标，无值时不渲染', () => {
    const { rerender } = render(
      <BotChatFilters
        value={{ ...emptyFilters, bizScene: 'scene-a', sessionKey: 'key-1' }}
        loading={false}
        onChange={jest.fn()}
        onQuery={jest.fn()}
        onReset={jest.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /更多筛选/ }).querySelector('span[aria-label="已生效 2 个筛选"]'),
    ).toHaveTextContent('2');

    rerender(
      <BotChatFilters value={emptyFilters} loading={false} onChange={jest.fn()} onQuery={jest.fn()} onReset={jest.fn()} />,
    );
    expect(
      screen.getByRole('button', { name: /更多筛选/ }).querySelector('span[aria-label^="已生效"]'),
    ).not.toBeInTheDocument();
  });

  it('Enter 或点查询触发 onQuery，点重置触发 onReset', () => {
    const onQuery = jest.fn();
    const onReset = jest.fn();
    render(
      <BotChatFilters value={emptyFilters} loading={false} onChange={jest.fn()} onQuery={onQuery} onReset={onReset} />,
    );
    fireEvent.keyDown(screen.getByLabelText('traceID'), { key: 'Enter' });
    expect(onQuery).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /查询/ }));
    expect(onQuery).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: /重置/ }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it('「更多筛选」Popover 内低频字段完整可填，「清空低频」逐项分发空值', () => {
    stubPointerCapture();
    const onChange = jest.fn();
    render(
      <BotChatFilters
        value={{ ...emptyFilters, groupId: 'g-1' }}
        loading={false}
        onChange={onChange}
        onQuery={jest.fn()}
        onReset={jest.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /更多筛选/ }));

    const lowFreqLabels = ['业务场景', '业务任务', '群ID', 'sessionID', 'sessionKey'];
    for (const label of lowFreqLabels) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText('sessionID'), { target: { value: 's-1' } });
    expect(onChange).toHaveBeenCalledWith('sessionId', 's-1');

    fireEvent.click(screen.getByRole('button', { name: /清空低频/ }));
    const clearedKeys = onChange.mock.calls.filter((call) => call[1] === '').map((call) => call[0]);
    expect(clearedKeys).toEqual(
      expect.arrayContaining(['bizScene', 'bizTaskId', 'groupId', 'sessionId', 'sessionKey']),
    );
  });
});