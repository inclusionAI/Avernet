/** @jest-environment jsdom */
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui/Drawer';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

/** 找到 Drawer 内层滚动容器（持有 children 的那个 div）。 */
function bodyContainer() {
  return screen.getByText('抽屉正文').parentElement as HTMLElement;
}

describe('Drawer bodyClassName', () => {
  it('default body keeps the p-6 padding', () => {
    render(
      <Drawer open>
        <DrawerContent side="left">
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    expect(bodyContainer().className).toContain('p-6');
  });

  it('bodyClassName="p-0" overrides the default padding (tailwind-merge)', () => {
    render(
      <Drawer open>
        <DrawerContent side="left" bodyClassName="p-0">
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    const cls = bodyContainer().className;
    expect(cls).toContain('p-0');
    expect(cls).not.toContain('p-6');
  });

  it('bodyClassName can add extra layout classes (flex flex-col)', () => {
    render(
      <Drawer open>
        <DrawerContent side="left" bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    const cls = bodyContainer().className;
    expect(cls).toContain('flex');
    expect(cls).toContain('flex-col');
  });

  it('shows the close button by default and hides it when showClose=false', () => {
    const { rerender } = render(
      <Drawer open>
        <DrawerContent side="left">
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.getByLabelText('关闭抽屉')).toBeInTheDocument();

    rerender(
      <Drawer open>
        <DrawerContent side="left" showClose={false}>
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.queryByLabelText('关闭抽屉')).not.toBeInTheDocument();
  });

  it('calls onOpenChange(false) when closing', () => {
    const onOpenChange = jest.fn();
    render(
      <Drawer open onOpenChange={onOpenChange}>
        <DrawerContent side="left" showClose>
          <DrawerTitle className="sr-only">标题</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    // 关闭按钮触发 Radix 的 onOpenChange(false)
    screen.getByLabelText('关闭抽屉').click();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
