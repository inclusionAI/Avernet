/** @jest-environment jsdom */
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
});

describe('SquareSearchBar', () => {
  test('搜索工具栏不使用外层圆角卡片容器', () => {
    const { container } = render(<SquareSearchBar resource="group" query="" onQueryChange={jest.fn()} />);

    const toolbar = container.firstElementChild as HTMLElement;
    expect(toolbar).toHaveClass('flex', 'min-h-8', 'flex-col', 'gap-3');
    expect(toolbar).not.toHaveClass('rounded-lg', 'border', 'bg-card', 'p-4');
    expect(screen.getByRole('textbox').parentElement).toHaveClass('h-9');
  });

  test('Bot、群、任务搜索输入使用统一宽度基线', () => {
    const { rerender } = render(
      <SquareSearchBar resource="bot" query="" onQueryChange={jest.fn()} onModeChange={jest.fn()} />,
    );
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full', 'max-w-md');

    rerender(<SquareSearchBar resource="group" query="" onQueryChange={jest.fn()} />);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full', 'max-w-md');

    rerender(<SquareSearchBar resource="task" query="" onQueryChange={jest.fn()} />);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full', 'max-w-md');
  });

  test('Bot 搜索模式内嵌在搜索框中，且与身份框等高', () => {
    render(<SquareSearchBar resource="bot" query="" mode="name" onQueryChange={jest.fn()} onModeChange={jest.fn()} />);

    const modeSelect = screen.getByRole('combobox', { name: 'Bot 搜索模式' });
    expect(modeSelect).toHaveTextContent('名称搜索');
    expect(modeSelect).toHaveClass('font-normal', 'text-muted-foreground');
    expect(screen.getByRole('textbox').parentElement).toContainElement(modeSelect);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('h-9');
    fireEvent.focus(screen.getByRole('textbox'));
    expect(screen.getByRole('textbox').parentElement).not.toHaveClass('focus-within:ring-2', 'focus-within:ring-ring');
    expect(screen.getByRole('textbox')).toHaveClass('focus-visible:border-transparent', 'focus-visible:ring-0');
  });

  test('切换 Bot 搜索模式时先清空已有输入', () => {
    const onQueryChange = jest.fn();
    const onModeChange = jest.fn();

    render(
      <SquareSearchBar
        resource="bot"
        query="已有关键词"
        mode="name"
        onQueryChange={onQueryChange}
        onModeChange={onModeChange}
      />,
    );

    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Bot 搜索模式' }), { key: 'ArrowDown' });
    const option = screen.getByRole('option', { name: '智能搜索' });
    expect(option).toHaveClass('font-normal', 'data-[state=checked]:text-muted-foreground');
    fireEvent.click(option);

    expect(onQueryChange).toHaveBeenCalledWith('');
    expect(onModeChange).toHaveBeenCalledWith('smart');
    expect(onQueryChange.mock.invocationCallOrder[0]).toBeLessThan(onModeChange.mock.invocationCallOrder[0]);
  });

  test('重复点击当前 Bot 搜索模式不清空输入', () => {
    const onQueryChange = jest.fn();
    const onModeChange = jest.fn();

    render(
      <SquareSearchBar
        resource="bot"
        query="已有关键词"
        mode="name"
        onQueryChange={onQueryChange}
        onModeChange={onModeChange}
      />,
    );

    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Bot 搜索模式' }), { key: 'ArrowDown' });
    fireEvent.click(screen.getByRole('option', { name: '名称搜索' }));

    expect(onQueryChange).not.toHaveBeenCalled();
    expect(onModeChange).not.toHaveBeenCalled();
  });
});
