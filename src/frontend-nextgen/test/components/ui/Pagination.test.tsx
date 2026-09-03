/** @jest-environment jsdom */

import { Pagination } from '@/components/ui';
import '@testing-library/jest-dom';
import { fireEvent, render } from '@testing-library/react';

describe('Pagination', () => {
  it('默认不渲染跳页输入（向后兼容）', () => {
    const { container } = render(<Pagination current={1} pageSize={10} total={100} onChange={() => {}} />);
    expect(container.querySelector('input')).toBeNull();
  });

  it('渲染信息文案与上/下一页按钮', () => {
    const { container, getByLabelText } = render(
      <Pagination current={2} pageSize={10} total={100} onChange={() => {}} />,
    );
    expect(container.textContent).toContain('共 100 条 · 第 2/10 页');
    expect(getByLabelText('上一页')).toBeEnabled();
    expect(getByLabelText('下一页')).toBeEnabled();
  });

  it('showQuickJumper 渲染跳页输入框和 Go 按钮', () => {
    const { getByLabelText, getByText } = render(
      <Pagination current={1} pageSize={10} total={100} onChange={() => {}} showQuickJumper />,
    );
    expect(getByLabelText('跳至页码')).toBeInTheDocument();
    expect(getByText('Go')).toBeInTheDocument();
  });

  it('点击 Go 跳转到指定页并清空输入框', () => {
    const onChange = jest.fn();
    const { getByLabelText, getByText } = render(
      <Pagination current={1} pageSize={10} total={100} onChange={onChange} showQuickJumper />,
    );
    const input = getByLabelText('跳至页码') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.click(getByText('Go'));
    expect(onChange).toHaveBeenCalledWith(5);
    expect(input.value).toBe('');
  });

  it('回车也可触发跳转', () => {
    const onChange = jest.fn();
    const { getByLabelText } = render(
      <Pagination current={1} pageSize={10} total={100} onChange={onChange} showQuickJumper />,
    );
    const input = getByLabelText('跳至页码');
    fireEvent.change(input, { target: { value: '3' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it('越界页码钳制到末页', () => {
    const onChange = jest.fn();
    const { getByLabelText, getByText } = render(
      <Pagination current={1} pageSize={10} total={30} onChange={onChange} showQuickJumper />,
    );
    fireEvent.change(getByLabelText('跳至页码'), { target: { value: '99' } });
    fireEvent.click(getByText('Go'));
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it('非法输入（非数字/0/负数/小数/空）不触发跳转且清空', () => {
    const onChange = jest.fn();
    const { getByLabelText, getByText } = render(
      <Pagination current={1} pageSize={10} total={100} onChange={onChange} showQuickJumper />,
    );
    const input = getByLabelText('跳至页码') as HTMLInputElement;
    for (const value of ['abc', '0', '-2', '1.5', '']) {
      fireEvent.change(input, { target: { value } });
      fireEvent.click(getByText('Go'));
    }
    expect(onChange).not.toHaveBeenCalled();
    expect(input.value).toBe('');
  });

  it('目标页等于当前页时不触发 onChange', () => {
    const onChange = jest.fn();
    const { getByLabelText, getByText } = render(
      <Pagination current={4} pageSize={10} total={100} onChange={onChange} showQuickJumper />,
    );
    fireEvent.change(getByLabelText('跳至页码'), { target: { value: '4' } });
    fireEvent.click(getByText('Go'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('total=0 不渲染', () => {
    const { container } = render(
      <Pagination current={1} pageSize={10} total={0} onChange={() => {}} showQuickJumper />,
    );
    expect(container.textContent).toBe('');
  });
});
