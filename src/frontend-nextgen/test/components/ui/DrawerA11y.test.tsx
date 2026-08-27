/** @jest-environment jsdom */
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui/Drawer';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

/**
 * 离屏导航的可达性基线。聚焦可机器校验的部分：
 * - 抽屉通过 DrawerTitle 暴露可访问名（呼应一/二级抽屉的 sr-only 标题）
 * - Escape 关闭（Radix DismissableLayer 提供）
 * - aria-modal 标记
 * 焦点陷阱 / 焦点回返 / 遮罩点击关闭为 Radix Dialog 保证，归 6.3 浏览器手测。
 * 触发按钮的可访问名（「打开导航」「打开会话列表」）由 AppShellResponsive.test 按角色名命中佐证。
 */
describe('Drawer accessibility', () => {
  it('exposes an accessible name via DrawerTitle', () => {
    render(
      <Drawer open>
        <DrawerContent side="left">
          <DrawerTitle>导航</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    expect(screen.getByRole('dialog', { name: '导航' })).toBeInTheDocument();
  });

  it('closes on Escape via onOpenChange(false)', () => {
    const onOpenChange = jest.fn();
    render(
      <Drawer open onOpenChange={onOpenChange}>
        <DrawerContent side="left">
          <DrawerTitle>导航</DrawerTitle>
          <span>抽屉正文</span>
        </DrawerContent>
      </Drawer>,
    );
    const dialog = screen.getByRole('dialog', { name: '导航' });
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
