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
