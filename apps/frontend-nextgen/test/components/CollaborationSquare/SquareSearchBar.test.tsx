/** @jest-environment jsdom */
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
});

describe('SquareSearchBar', () => {
  test('搜索工具栏使用 32px 高描边输入组，不使用圆角卡片容器', () => {
    const { container } = render(<SquareSearchBar resource="group" query="" onQueryChange={jest.fn()} />);

    const toolbar = container.firstElementChild as HTMLElement;
    expect(toolbar).toHaveClass('flex', 'min-w-0', 'flex-col');
    expect(toolbar).not.toHaveClass('rounded-lg', 'border', 'bg-card', 'p-4');
    // dmore Frame 2：32px 高、8px 圆角、描边输入组。
    expect(screen.getByRole('textbox').parentElement).toHaveClass('h-8', 'rounded-lg', 'border-input', 'bg-background');
  });

  test('Bot、群、任务搜索输入使用统一宽度基线', () => {
    const { rerender } = render(
      <SquareSearchBar resource="bot" query="" onQueryChange={jest.fn()} onModeChange={jest.fn()} />,
    );
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full');

    rerender(<SquareSearchBar resource="group" query="" onQueryChange={jest.fn()} />);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full');

    rerender(<SquareSearchBar resource="task" query="" onQueryChange={jest.fn()} />);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('w-full');
  });

  test('Bot 搜索模式内嵌在搜索框中，且与页头工具行等高', () => {
    render(<SquareSearchBar resource="bot" query="" mode="name" onQueryChange={jest.fn()} onModeChange={jest.fn()} />);

    const modeSelect = screen.getByRole('combobox', { name: 'Bot 搜索模式' });
    expect(modeSelect).toHaveTextContent('按名称');
    expect(modeSelect).toHaveClass('font-normal', 'text-foreground');
    expect(screen.getByRole('textbox').parentElement).toContainElement(modeSelect);
    expect(screen.getByRole('textbox').parentElement).toHaveClass('h-8');
    fireEvent.focus(screen.getByRole('textbox'));
    expect(screen.getByRole('textbox').parentElement).not.toHaveClass('focus-within:ring-2', 'focus-within:ring-ring');
    expect(screen.getByRole('textbox')).toHaveClass('focus-visible:border-transparent', 'focus-visible:ring-0');
  });

  test('按名称模式占位符使用 dmore 文案「输入Bot名称」', () => {
    render(<SquareSearchBar resource="bot" query="" mode="name" onQueryChange={jest.fn()} onModeChange={jest.fn()} />);
    expect(screen.getByRole('textbox', { name: '输入Bot名称' })).toHaveAttribute('placeholder', '输入Bot名称');
  });

  test('智能搜索模式展示「智能」渐变胶囊 + AI 图标', () => {
    render(<SquareSearchBar resource="bot" query="" mode="smart" onQueryChange={jest.fn()} onModeChange={jest.fn()} />);
    const trigger = screen.getByRole('combobox', { name: 'Bot 搜索模式' });
    expect(trigger).toHaveTextContent('智能');
    const pill = screen.getByText('智能');
    // dmore：「智能」为紫蓝渐变**文字色**（background-clip: text + 透明字），无胶囊背景。
    // 渐变文字走内联 style，jsdom 的 cssstyle 不解析 gradient，故断言原始 style 属性。
    const style = pill.getAttribute('style') ?? '';
    expect(style).toContain('linear-gradient(225.32deg, #AC56FF -2.66%, #006AFF 108.32%)');
    expect(style).toContain('background-clip: text');
    expect(style).toContain('color: transparent');
    // 无胶囊背景/白字兜底类；字号继承触发器常规字号（与「按名称」一致），不再用 11px。
    expect(pill).not.toHaveClass('text-white', 'rounded-full', 'bg-accent');
    expect(pill).toHaveClass('font-normal');
    expect(pill).not.toHaveClass('text-[11px]', 'font-medium');
    // AI 图标紧贴「智能」文案右侧（alt 为空、纯装饰），14px 渲染。
    const icon = trigger.querySelector('img') as HTMLImageElement;
    expect(icon).not.toBeNull();
    expect(pill.lastElementChild).toBe(icon);
    expect(icon).toHaveClass('h-3.5', 'w-3.5');
    expect(pill.textContent).toBe('智能');
  });

  test('智能搜索 name_fallback 回退时触发器改展示「名称匹配」（bcs-standalone-friend-discovery）', () => {
    const { rerender } = render(
      <SquareSearchBar
        resource="bot"
        query="规划"
        mode="smart"
        searchResultMode="name_fallback"
        onQueryChange={jest.fn()}
        onModeChange={jest.fn()}
      />,
    );
    const trigger = screen.getByRole('combobox', { name: 'Bot 搜索模式' });
    // 回退标识替代语义渐变胶囊：普通前景色文字，不出现「智能」。
    expect(trigger).toHaveTextContent('名称匹配');
    expect(screen.queryByText('智能')).toBeNull();
    const fallbackLabel = screen.getByText('名称匹配');
    expect(fallbackLabel).toHaveClass('font-normal', 'text-muted-foreground');
    expect(fallbackLabel.getAttribute('style')).toBeNull();

    // 智能命中（semantic）时保持渐变胶囊。
    rerender(
      <SquareSearchBar
        resource="bot"
        query="规划"
        mode="smart"
        searchResultMode="semantic"
        onQueryChange={jest.fn()}
        onModeChange={jest.fn()}
      />,
    );
    expect(screen.getByRole('combobox', { name: 'Bot 搜索模式' })).toHaveTextContent('智能');
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
    const option = screen.getByRole('option', { name: '智能' });
    expect(option).toHaveClass('font-normal', 'data-[state=checked]:text-muted-foreground');
    // 下拉面板收窄（144px → 112px，配合常规字号）。
    expect(screen.getByRole('listbox')).toHaveClass('min-w-28');
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
    fireEvent.click(screen.getByRole('option', { name: '按名称' }));

    expect(onQueryChange).not.toHaveBeenCalled();
    expect(onModeChange).not.toHaveBeenCalled();
  });
});
