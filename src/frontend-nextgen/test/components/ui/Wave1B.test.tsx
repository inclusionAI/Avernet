/** @jest-environment jsdom */

import {
  ConfirmDialog,
  Drawer,
  DrawerContent,
  DrawerTitle,
  DrawerTrigger,
  Popconfirm,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Switch,
} from '@/components/ui';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;

describe('Wave 1B UI 组件', () => {
  test('ConfirmDialog 异步确认期间 loading，成功后关闭', async () => {
    const user = userEvent.setup();
    let resolveConfirm!: () => void;
    const onConfirm = jest.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveConfirm = resolve;
        }),
    );
    render(
      <ConfirmDialog title="删除 Bot" description="不可恢复" onConfirm={onConfirm}>
        <button type="button">删除</button>
      </ConfirmDialog>,
    );
    await user.click(screen.getByRole('button', { name: '删除' }));
    await user.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled();
    resolveConfirm();
    expect(await screen.findByRole('button', { name: '删除' })).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('Drawer 默认右侧打开，并支持关闭', async () => {
    const user = userEvent.setup();
    render(
      <Drawer>
        <DrawerTrigger asChild>
          <button type="button">打开抽屉</button>
        </DrawerTrigger>
        <DrawerContent>
          <DrawerTitle>设置</DrawerTitle>
          <p>内容</p>
        </DrawerContent>
      </Drawer>,
    );
    const trigger = screen.getByRole('button', { name: '打开抽屉' });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: '设置' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(trigger).toHaveFocus();
  });

  test('Popover 和 Popconfirm 可打开并执行确认', async () => {
    const user = userEvent.setup();
    const onConfirm = jest.fn();
    render(
      <div>
        <Popover>
          <PopoverTrigger asChild>
            <button type="button">更多</button>
          </PopoverTrigger>
          <PopoverContent>详情</PopoverContent>
        </Popover>
        <Popconfirm title="确认删除" onConfirm={onConfirm}>
          <button type="button">删除项目</button>
        </Popconfirm>
      </div>,
    );
    await user.click(screen.getByRole('button', { name: '更多' }));
    expect(screen.getByText('详情')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '删除项目' }));
    await user.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  test('Switch 支持键盘切换和 disabled', async () => {
    const user = userEvent.setup();
    render(
      <>
        <Switch aria-label="启用" />
        <Switch aria-label="禁用" disabled />
      </>,
    );
    const enabled = screen.getByRole('switch', { name: '启用' });
    await user.click(enabled);
    expect(enabled).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('switch', { name: '禁用' }));
    expect(screen.getByRole('switch', { name: '禁用' })).toHaveAttribute('aria-checked', 'false');
  });
});
