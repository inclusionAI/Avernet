/** @jest-environment jsdom */
import { CreateCapabilitySetModal } from '@/components/BotWorkshop/Editor/CreateCapabilitySetModal';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';

test('创建期间禁止重复提交、编辑及关闭，成功后关闭并清空名称', async () => {
  let finish!: () => void;
  const onCreate = jest.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  const onOpenChange = jest.fn();
  const { rerender } = render(<CreateCapabilitySetModal open onOpenChange={onOpenChange} onCreate={onCreate} />);
  const input = screen.getByPlaceholderText('输入能力集名称');
  fireEvent.change(input, { target: { value: '  研发能力  ' } });
  const submit = screen.getByRole('button', { name: '创建' });
  act(() => { submit.click(); submit.click(); });
  expect(onCreate).toHaveBeenCalledTimes(1);
  expect(onCreate).toHaveBeenCalledWith('研发能力');
  expect(input).toBeDisabled();
  expect(screen.getByRole('button', { name: '正在创建…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(onOpenChange).not.toHaveBeenCalled();
  await act(async () => { finish(); });
  expect(onOpenChange).toHaveBeenCalledWith(false);
  rerender(<CreateCapabilitySetModal open onOpenChange={onOpenChange} onCreate={onCreate} />);
  expect(screen.getByPlaceholderText('输入能力集名称')).toHaveValue('');
});

test('创建失败保留名称、恢复按钮并允许重试，不关闭弹窗', async () => {
  const onCreate = jest.fn().mockRejectedValueOnce(new Error('网络异常')).mockResolvedValueOnce(undefined);
  const onOpenChange = jest.fn();
  render(<CreateCapabilitySetModal open onOpenChange={onOpenChange} onCreate={onCreate} />);
  fireEvent.change(screen.getByPlaceholderText('输入能力集名称'), { target: { value: '研发能力' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '创建' })); });
  expect(screen.getByRole('alert')).toHaveTextContent('网络异常');
  expect(screen.getByPlaceholderText('输入能力集名称')).toHaveValue('研发能力');
  expect(screen.getByRole('button', { name: '创建' })).toBeEnabled();
  expect(onOpenChange).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '创建' })); });
  expect(onCreate).toHaveBeenCalledTimes(2);
  expect(onOpenChange).toHaveBeenCalledWith(false);
});
