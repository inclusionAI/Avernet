/** @jest-environment jsdom */

import {
  Input,
  Modal,
  ModalContent,
  ModalTitle,
  ModalTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

// jsdom 未实现 Pointer Capture，补齐 Radix Select 测试所需的最小 API。
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

describe('Wave 1A UI 组件', () => {
  test('Textarea 透传原生属性、ref 和错误状态', () => {
    const ref = React.createRef<HTMLTextAreaElement>();
    render(<Textarea ref={ref} aria-label="备注" variant="error" placeholder="请输入" />);

    expect(ref.current).toBeInstanceOf(HTMLTextAreaElement);
    expect(screen.getByRole('textbox', { name: '备注' })).toHaveAttribute('placeholder', '请输入');
    expect(screen.getByRole('textbox', { name: '备注' })).toHaveClass('border-destructive');
  });

  test('Select 支持打开、选择和关闭', async () => {
    const user = userEvent.setup();
    render(
      <Select>
        <SelectTrigger aria-label="状态">
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="enabled">启用</SelectItem>
          <SelectItem value="disabled">停用</SelectItem>
        </SelectContent>
      </Select>,
    );

    await user.click(screen.getByRole('combobox', { name: '状态' }));
    expect(screen.getByRole('option', { name: '启用' })).toBeVisible();
    await user.click(screen.getByRole('option', { name: '启用' }));
    expect(screen.getByRole('combobox', { name: '状态' })).toHaveTextContent('启用');
  });

  test('SelectTrigger 带手型，SelectItem 维持 cursor-default', async () => {
    const user = userEvent.setup();
    render(
      <Select>
        <SelectTrigger aria-label="优先级">
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="high">高</SelectItem>
        </SelectContent>
      </Select>,
    );

    const trigger = screen.getByRole('combobox', { name: '优先级' });
    expect(trigger).toHaveClass('cursor-pointer');
    expect(trigger).toHaveClass('disabled:cursor-not-allowed');

    await user.click(trigger);
    expect(screen.getByRole('option', { name: '高' })).toHaveClass('cursor-default');
  });

  test('Input / Textarea 不加手型，保留浏览器文本 I-beam', () => {
    render(<Input aria-label="名称" />);
    render(<Textarea aria-label="备注" />);

    const input = screen.getByLabelText('名称');
    const textarea = screen.getByLabelText('备注');
    expect(input.className).not.toContain('cursor-pointer');
    expect(textarea.className).not.toContain('cursor-pointer');
    expect(input).toHaveClass('disabled:cursor-not-allowed');
  });

  test('Tooltip 在 focus 时显示提示', async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" aria-label="帮助">
              ?
            </button>
          </TooltipTrigger>
          <TooltipContent>帮助说明</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );

    await user.tab();
    expect(await screen.findByRole('tooltip')).toHaveTextContent('帮助说明');
  });

  test('Modal 打开后提供 dialog，Escape 后关闭并恢复触发器焦点', async () => {
    const user = userEvent.setup();
    render(
      <Modal>
        <ModalTrigger asChild>
          <button type="button">打开弹窗</button>
        </ModalTrigger>
        <ModalContent>
          <ModalTitle>编辑设置</ModalTitle>
          <p>内容</p>
        </ModalContent>
      </Modal>,
    );

    const trigger = screen.getByRole('button', { name: '打开弹窗' });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: '编辑设置' })).toBeVisible();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: '编辑设置' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
