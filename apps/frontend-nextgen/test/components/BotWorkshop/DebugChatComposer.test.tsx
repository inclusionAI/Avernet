/** @jest-environment jsdom */
import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

let mockSenderProps: Record<string, any> = {};

jest.mock('@tc-chat/ui/es/Sender', () => {
  const { createElement, forwardRef } = require('react');
  return {
    Sender: forwardRef((props: Record<string, any>, _ref: unknown) => {
      void _ref;
      mockSenderProps = props;
      return createElement(
        'div',
        { 'data-testid': 'debug-sender', 'data-placeholder': props.placeholder },
        createElement(
          'button',
          {
            type: 'button',
            onClick: () =>
              props.onSubmit?.('报告', {
                commands: [],
                fileRefs: [
                  {
                    resource_id: 'resource-1',
                    insert_id: 'debug_file_1',
                    display_name: '需求说明.md',
                  },
                ],
                resolvedContent: '<file-ref insert_id="debug_file_1"></file-ref>报告',
              }),
          },
          '模拟发送',
        ),
        props.toolbar?.left,
        props.toolbar?.right,
      );
    }),
  };
});

jest.mock('@/components/Workspace/TaskComposerMenu', () => ({
  ComposerCapabilitiesMenu: ({ onUpload }: { onUpload?: () => void }) => (
    <button type="button" onClick={onUpload}>
      上传文件
    </button>
  ),
}));

beforeEach(() => {
  mockSenderProps = {};
});

it('复用线上态 Sender 的斜杠命令与文件引用配置', () => {
  const command = { categories: [], onSelect: jest.fn(), format: jest.fn() };
  const fileChip = { label: '文件', onPick: jest.fn(), onRemove: jest.fn() };

  render(
    <DebugChatComposer
      sessionId="session-1"
      userId="user-1"
      isRequesting={false}
      command={command}
      fileChip={fileChip}
      onSend={jest.fn()}
      onStop={jest.fn()}
    />,
  );

  expect(mockSenderProps.command).toBe(command);
  expect(mockSenderProps.fileChip).toBe(fileChip);
  expect(screen.getByTestId('debug-sender')).toHaveAttribute(
    'data-placeholder',
    expect.stringContaining('/skill'),
  );
  expect(mockSenderProps.placeholder).toContain('/file');
  expect(mockSenderProps.placeholder).toContain('/clear');
});

it('按线上态协议把 Sender 文件引用发送给调试会话', () => {
  const onSend = jest.fn();

  render(
    <DebugChatComposer
      sessionId="session-1"
      userId="user-1"
      isRequesting={false}
      onSend={onSend}
      onStop={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '模拟发送' }));

  expect(onSend).toHaveBeenCalledWith('<file-ref insert_id="debug_file_1"></file-ref>报告', {
    resourceReferences: [{ type: 'file', resource_id: 'resource-1', insert_id: 'debug_file_1' }],
    promptFileRefs: [{ resource_id: 'resource-1', insert_id: 'debug_file_1' }],
    fileRefDisplay: [{ insert_id: 'debug_file_1', name: '需求说明.md' }],
  });
});

it('从能力菜单打开与线上态相同的上传入口', () => {
  const onUpload = jest.fn();

  render(
    <DebugChatComposer
      sessionId="session-1"
      userId="user-1"
      isRequesting={false}
      onUpload={onUpload}
      onSend={jest.fn()}
      onStop={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '上传文件' }));
  expect(onUpload).toHaveBeenCalledTimes(1);
});

it('保留调试会话模型选择器', () => {
  render(
    <DebugChatComposer
      sessionId="session-1"
      userId="user-1"
      isRequesting={false}
      modelSelector={<span data-testid="debug-model-selector">模型选择</span>}
      onSend={jest.fn()}
      onStop={jest.fn()}
    />,
  );

  expect(screen.getByTestId('debug-model-selector')).toBeInTheDocument();
});
