/** @jest-environment jsdom */
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

describe('SquareSearchBar', () => {
  test('搜索工具栏不使用外层圆角卡片容器', () => {
    const { container } = render(
      <SquareSearchBar
        resource="group"
        query=""
        onQueryChange={jest.fn()}
      />,
    );

    const toolbar = container.firstElementChild as HTMLElement;
    expect(toolbar).toHaveClass('flex', 'min-h-8', 'flex-col', 'gap-3');
    expect(toolbar).not.toHaveClass('rounded-lg', 'border', 'bg-card', 'p-4');
    expect(screen.getByRole('textbox').parentElement).toHaveClass('h-8');
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

  test('未选中的 Bot 搜索模式使用明确的弱化文字色', () => {
    render(
      <SquareSearchBar
        resource="bot"
        query=""
        mode="name"
        onQueryChange={jest.fn()}
        onModeChange={jest.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '智能搜索' })).toHaveClass('text-muted-foreground');
    expect(screen.getByRole('button', { name: '名称搜索' })).toHaveClass('text-primary');
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

    fireEvent.click(screen.getByRole('button', { name: '智能搜索' }));

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

    fireEvent.click(screen.getByRole('button', { name: '名称搜索' }));

    expect(onQueryChange).not.toHaveBeenCalled();
    expect(onModeChange).toHaveBeenCalledWith('name');
  });
});
