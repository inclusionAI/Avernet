/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { GroupChatComposer } from '@/pages/Workspace/components/GroupChatPane/GroupChatComposer';
import { describe, expect, it, jest } from '@jest/globals';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { render } from '@testing-library/react';
import React from 'react';

// 根因 4 的 Composer→<Sender ref> 绑定 leg 验证(GroupWorkspaceArea→GroupChatPane 上游 leg 由 typecheck + 手动 AC 9.4 覆盖)。
// 桩 Sender 用 forwardRef + useImperativeHandle 暴露 SenderRef 子集,证明 inputRef prop 经 <Sender ref={inputRef}> 真绑定(forwardRef)。
jest.mock('@tc-chat/ui/es/Sender', () => {
  const { forwardRef, useImperativeHandle } = require('react');
  return {
    Sender: forwardRef((_props: unknown, ref: React.Ref<unknown>) => {
      useImperativeHandle(
        ref,
        () => ({ focus: () => {}, insert: () => {}, clear: () => {}, getValue: () => '', blur: () => {} }),
        [],
      );
      return <div data-testid="sender" />;
    }),
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
    queue: [],
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
  UploadFilesModal: () => null,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/mentionHelpers', () => ({
  expandMentionIds: () => [],
}));
jest.mock('@/components/ui', () => ({ Badge: () => null }));

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
