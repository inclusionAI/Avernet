/** @jest-environment jsdom */

import { Button } from '@/components/ui';
import '@testing-library/jest-dom';
import { render } from '@testing-library/react';

const getButton = (node: ReturnType<typeof render>) => node.container.querySelector('button')!;

const normalize = (s: string) => s.replace(/\s+/g, ' ').trim();

describe('Button', () => {
  it('渲染 children', () => {
    const { container } = render(<Button>保存</Button>);
    expect(container.querySelector('button')).toHaveTextContent('保存');
  });

  it('default variant 是品牌蓝主操作(token 化、无硬编码 text-white)', () => {
    const btn = getButton(render(<Button variant="default">主操作</Button>));
    expect(btn.className).toContain('bg-primary');
    expect(btn.className).toContain('text-primary-foreground');
    expect(btn.className).not.toContain('text-white');
  });

  it('primary 是 default 的别名(类名一致)', () => {
    const a = normalize(getButton(render(<Button variant="default">A</Button>)).className);
    const b = normalize(getButton(render(<Button variant="primary">B</Button>)).className);
    expect(a).toBe(b);
  });

  it('outline variant 是线框(对齐 showcase)', () => {
    const btn = getButton(render(<Button variant="outline">线框</Button>));
    expect(btn.className).toContain('border-input');
    expect(btn.className).toContain('bg-background');
  });

  it('link variant 带下划线(对齐 showcase)', () => {
    const btn = getButton(render(<Button variant="link">链接</Button>));
    expect(btn.className).toContain('underline');
    expect(btn.className).toContain('text-primary');
  });

  it('size default 与 size md 等价(别名)', () => {
    const a = normalize(getButton(render(<Button size="default">A</Button>)).className);
    const b = normalize(getButton(render(<Button size="md">B</Button>)).className);
    expect(a).toBe(b);
  });

  it('loading 禁用按钮并渲染 spinner', () => {
    const btn = getButton(render(<Button loading>保存</Button>));
    expect(btn).toBeDisabled();
    expect(btn.querySelector('[aria-hidden]')).toBeInTheDocument();
  });

  it('可点击态给手型，禁用态退回 not-allowed', () => {
    const btn = getButton(render(<Button>保存</Button>));
    expect(btn.className).toContain('cursor-pointer');
    expect(btn.className).toContain('disabled:cursor-not-allowed');

    const disabled = getButton(render(<Button disabled>保存</Button>));
    expect(disabled).toBeDisabled();
    expect(disabled.className).toContain('disabled:cursor-not-allowed');
  });

  it('asChild 渲染非 button 元素时仍带手型（base 层元素选择器覆盖不到）', () => {
    const { container } = render(
      <Button asChild>
        <div>整行热区</div>
      </Button>,
    );
    expect(container.querySelector('div')!.className).toContain('cursor-pointer');
  });

  it('asChild 经 Slot 把类名透传给子元素', () => {
    const { container } = render(
      <Button asChild>
        <a href="#x">链接</a>
      </Button>,
    );
    const link = container.querySelector('a')!;
    expect(link).toHaveAttribute('href', '#x');
    expect(link.className).toContain('bg-primary');
  });
});
