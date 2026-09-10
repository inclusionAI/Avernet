/** @jest-environment jsdom */

import { CapabilityMembers } from '@/components/BotWorkshop/Editor/CapabilityMembers';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

beforeEach(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('MCP 调用身份点击后展示说明并由用户明确选择', () => {
  const onIdentity = jest.fn().mockResolvedValue(undefined);
  render(
    <CapabilityMembers
      kind="mcp"
      items={[{ serverCode: 'mcp-1', name: '知识检索', active: true }]}
      editable
      identityEditable
      identities={{ 'mcp-1': 'owner' }}
      onIdentity={onIdentity}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '修改知识检索调用身份' }));
  expect(screen.getByText('使用 Bot 所有者身份调用')).toBeInTheDocument();
  expect(screen.getByText('使用当前对话用户身份调用')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Caller 模式/ }));
  expect(onIdentity).toHaveBeenCalledWith('mcp-1', 'caller');
});

test('个人 Bot 的 MCP 访问方式不可切换时展示原因', async () => {
  render(
    <CapabilityMembers
      kind="mcp"
      items={[{ serverCode: 'mcp-1', name: '知识检索', active: true }]}
      editable
      identityDisabledReason="个人 Bot 固定使用 Owner 模式，不支持切换访问方式"
    />,
  );

  fireEvent.focus(screen.getByTestId('mcp-identity-disabled-mcp-1'));
  expect(await screen.findByText('个人 Bot 固定使用 Owner 模式，不支持切换访问方式')).toBeInTheDocument();
});
