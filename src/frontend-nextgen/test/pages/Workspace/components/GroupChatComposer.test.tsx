/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { GroupChatComposer } from '@/pages/Workspace/components/GroupChatPane/GroupChatComposer';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

const mockInsertFileChips = jest.fn();

// 根因 4 的 Composer→<Sender ref> 绑定 leg 验证(GroupWorkspaceArea→GroupChatPane 上游 leg 由 typecheck + 手动 AC 9.4 覆盖)。
// 桩 Sender 用 forwardRef + useImperativeHandle 暴露 SenderRef 子集,证明 inputRef prop 经 <Sender ref={inputRef}> 真绑定(forwardRef)。
jest.mock('@tc-chat/ui/es/Sender', () => {
  const { forwardRef, useImperativeHandle } = require('react');
  return {
    Sender: forwardRef(
      (
        _props: {
          className?: string;
          fileChip?: unknown;
          onSubmit?: (content: string, context: { mentions: Array<{ id: string; name: string }> }) => void;
        },
        ref: React.Ref<unknown>,
      ) => {
        useImperativeHandle(
          ref,
          () => ({
            focus: () => {},
            insert: () => {},
            insertFileChips: mockInsertFileChips,
            clear: () => {},
            getValue: () => '',
            blur: () => {},
          }),
          [],
        );
        return (
          <div
            data-testid="sender"
            className={_props.className}
            data-file-chip={_props.fileChip ? 'enabled' : 'disabled'}
          >
            <button
              type="button"
              data-testid="sender-submit"
              onClick={() => _props.onSubmit?.('追问内容', { mentions: [] })}
            >
              submit
            </button>
          </div>
        );
      },
    ),
    ToolbarButton: () => null,
  };
});
jest.mock('@/pages/Workspace/hooks/useGroupChatImageUpload', () => ({
  useGroupChatImageUpload: () => ({
    images: [],
    isProcessing: false,
    isUploading: false,
    addFiles: () => {},
    removeImage: () => {},
    canAddMore: false,
    uploadAll: () => Promise.resolve([]),
    clear: () => {},
  }),
}));
jest.mock('@/pages/Workspace/hooks/useSessionFileUpload', () => ({
  useSessionFileUpload: () => ({
    queue: [
      {
        localId: 'file-task',
        name: '需求说明.md',
        size: 10,
        mime: 'text/markdown',
        phase: 'ready',
        progress: 100,
        fileId: 'file-1',
      },
    ],
    isUploading: false,
    stageFiles: () => {},
    submitStaged: () => {},
    cancelTask: () => {},
    retryTask: () => {},
    discardAll: () => {},
    clearCompleted: () => {},
    hasPending: false,
  }),
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/UploadFilesModal', () => ({
  UploadFilesModal: (props: { onAddToSession?: () => void }) => (
    <button type="button" data-testid="add-uploaded-files" onClick={props.onAddToSession}>
      add files
    </button>
  ),
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/mentionHelpers', () => ({
  expandMentionIds: () => [],
}));
jest.mock('@/components/ui', () => ({
  Badge: () => null,
  IconButton: ({ label, onClick }: { label: string; onClick?: () => void }) => (
    <button type="button" aria-label={label} onClick={onClick} />
  ),
}));

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: 't',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

describe('GroupChatComposer — 根因 4 inputRef 经 <Sender ref> 真绑定', () => {
  beforeEach(() => {
    mockInsertFileChips.mockClear();
  });
  it('输入区宽度使用容器全宽，不受固定 max-width 限制', () => {
    render(
      <GroupChatComposer
        session={session}
        isRequesting={false}
        connectionStatus="connected"
        mentionConfig={undefined}
        showReconnectToolbar={false}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    const sender = document.querySelector('[data-testid="sender"]');
    expect(sender).toHaveClass('w-full');
    expect(sender).not.toHaveClass('max-w-4xl');
  });

  it('添加已上传文件至会话输入区时插入 Sender 文件胶囊', () => {
    const inputRef = React.createRef<SenderRef>();
    render(
      <GroupChatComposer
        session={session}
        isRequesting={false}
        connectionStatus="connected"
        mentionConfig={undefined}
        showReconnectToolbar={false}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        draft=""
        onDraftChange={() => {}}
        inputRef={inputRef}
      />,
    );

    expect(screen.getByTestId('sender')).toHaveAttribute('data-file-chip', 'enabled');
    fireEvent.click(screen.getByTestId('add-uploaded-files'));
    expect(mockInsertFileChips).toHaveBeenCalledWith([{ resource_id: 'file-1', display_name: '需求说明.md' }]);
  });

  it('提交时把引用上下文拼入现有正文，不改变 onSend 参数结构', () => {
    const onSend = jest.fn();
    render(
      <GroupChatComposer
        session={session}
        isRequesting={false}
        connectionStatus="connected"
        mentionConfig={undefined}
        showReconnectToolbar={false}
        onSend={onSend}
        onStop={() => {}}
        onReconnect={() => {}}
        draft=""
        onDraftChange={() => {}}
        quote={{ messageId: 'm1', senderName: 'Bot 甲', text: '被引用的内容' }}
        onClearQuote={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId('sender-submit'));

    expect(onSend).toHaveBeenCalledWith('引用 Bot 甲 的消息：\n> 被引用的内容\n\n追问内容', [], undefined);
    expect(screen.getByText(/引用 Bot 甲/)).toBeInTheDocument();
  });

  it('inputRef prop 绑定到 <Sender ref>(forwardRef),挂载后 inputRef.current 非 null 且暴露 insert', () => {
    const inputRef = React.createRef<SenderRef>();
    render(
      <GroupChatComposer
        session={session}
        isRequesting={false}
        connectionStatus="connected"
        mentionConfig={undefined}
        showReconnectToolbar={false}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        draft=""
        onDraftChange={() => {}}
        inputRef={inputRef}
      />,
    );
    // 桩 Sender 经 useImperativeHandle 暴露 SenderRef 子集 → inputRef.current 非 null,BridgeInputRef.insert 可达。
    expect(inputRef.current).not.toBeNull();
    expect(typeof inputRef.current?.insert).toBe('function');
  });
});
