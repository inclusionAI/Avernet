/** @jest-environment jsdom */
// Open 装配真实任务槽位必须在「composer import 之上」（ES import 按声明序求值）：GroupChatComposer
// 在模块作用域一次性绑定槽位常量，jest 全局 setup 只注册零行为占位，本行先经 in-file 通道注册
// 真实 groupTasks（ComposerCapabilitiesMenu + buildTaskInstructions），下面的静态 import 才把
// 真实实现绑进 composer。collab 空槽位形态（槽位常量绑定 null）由 GroupChatComposerCollabSlots
// 套件在独立文件（独立模块注册表）内覆盖，避免 resetModules 双 React 实例纠缠。
import '../../../helpers/registerOpenGroupTaskSlots';
import type { SessionView } from '@/domain/collaboration';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import { GroupChatComposer } from '@/pages/Workspace/components/GroupChatPane/GroupChatComposer';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

const mockInsertFileChips = jest.fn();
const mockAddFiles = jest.fn();

// 根因 4 的 Composer→<Sender ref> 绑定 leg 验证(GroupWorkspaceArea→GroupChatPane 上游 leg 由 typecheck + 手动 AC 9.4 覆盖)。
// 桩 Sender 用 forwardRef + useImperativeHandle 暴露 SenderRef 子集,证明 inputRef prop 经 <Sender ref={inputRef}> 真绑定(forwardRef)。
jest.mock('@tc-chat/ui/es/Sender', () => {
  const { forwardRef, useImperativeHandle } = require('react');
  return {
    Sender: forwardRef(
      (
        _props: {
          className?: string;
          disabled?: boolean;
          placeholder?: string;
          fileChip?: { onPick: () => void };
          imageUpload?: { enabled?: boolean };
          onPasteFile?: (files: File[]) => void;
          onSubmit?: (content: string, context: { mentions: Array<{ id: string; name: string }> }) => void;
          /** 真实 Sender 会渲染 toolbar.left 装配区；桩同步渲染，供 composer 工具栏接线断言。 */
          toolbar?: { left?: React.ReactNode };
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
            data-disabled={_props.disabled ? 'true' : 'false'}
            data-file-chip={_props.fileChip ? 'enabled' : 'disabled'}
            data-image-upload={_props.imageUpload?.enabled ? 'enabled' : 'disabled'}
            data-placeholder={_props.placeholder}
          >
            <div data-testid="sender-toolbar">{_props.toolbar?.left}</div>
            <button type="button" onClick={_props.fileChip?.onPick}>上传文件</button>
            <button
              type="button"
              data-testid="sender-paste-image"
              onClick={() => _props.onPasteFile?.([new File(['image'], 'pasted.png', { type: 'image/png' })])}
            >
              paste image
            </button>
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
    addFiles: mockAddFiles,
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
  UploadFilesModal: ({ open }: { open: boolean }) => open ? <div data-testid="upload-files-modal" /> : null,
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/mentionHelpers', () => ({
  expandMentionIds: () => [],
}));
// 不 mock @/components/ui：真实能力菜单（经槽位解析）与 AttachmentMenu 依赖真实
// Button/Popover/Tooltip 原语，jsdom 下 Radix 交互可径直驱动（见 TaskComposerMenu 套件先例）。

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

/** 任务执行装配夹具（结构与 useTaskExecution 返回值对齐；测试只消费受控选中态/回调）。 */
function executionFixture(overrides: Partial<UseTaskExecutionResult> = {}): UseTaskExecutionResult {
  return {
    submitting: false,
    error: null,
    lastTaskId: null,
    workflows: [],
    workflowsLoading: false,
    loadWorkflows: jest.fn().mockResolvedValue(undefined),
    selections: [],
    selectWorkflow: jest.fn(),
    selectDynamic: jest.fn(),
    dynamicTaskEnabled: true,
    dynamicTaskChecking: false,
    removeSelection: jest.fn(),
    clearSelection: jest.fn(),
    submit: jest.fn(),
    validate: jest.fn(() => null),
    ...overrides,
  };
}

describe('GroupChatComposer — 根因 4 inputRef 经 <Sender ref> 真绑定', () => {
  beforeEach(() => {
    mockInsertFileChips.mockClear();
    mockAddFiles.mockClear();
  });
  it('输入区宽度使用容器全宽，不受固定 max-width 限制', () => {
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    const sender = document.querySelector('[data-testid="sender"]');
    expect(sender).toHaveClass('w-full');
    expect(sender).not.toHaveClass('max-w-4xl');
  });

  it('会话就绪时输入区保持可用（不再随输出中状态禁用，排队由后端拥塞控制兜底）', () => {
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    expect(screen.getByTestId('sender')).toHaveAttribute('data-disabled', 'false');
  });

  it('无会话时输入区禁用', () => {
    render(
      <GroupChatComposer
        session={null}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    expect(screen.getByTestId('sender')).toHaveAttribute('data-disabled', 'true');
  });

  it('粘贴图片时将图片文件加入当前消息的待发送列表', () => {
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId('sender-paste-image'));

    expect(mockAddFiles).toHaveBeenCalledTimes(1);
    expect(mockAddFiles).toHaveBeenCalledWith([expect.objectContaining({ name: 'pasted.png', type: 'image/png' })]);
  });

  it('文件入口仍可打开上传窗口', () => {
    const inputRef = React.createRef<SenderRef>();
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
        inputRef={inputRef}
      />,
    );

    expect(screen.getByTestId('sender')).toHaveAttribute('data-file-chip', 'enabled');
    expect(screen.queryByTestId('upload-files-modal')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '上传文件' }));
    expect(screen.getByTestId('upload-files-modal')).toBeInTheDocument();
    expect(mockInsertFileChips).not.toHaveBeenCalled();
  });

  it('提交时把引用上下文拼入现有正文，不改变 onSend 参数结构', () => {
    const onSend = jest.fn();
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={onSend}
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
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
        inputRef={inputRef}
      />,
    );
    // 桩 Sender 经 useImperativeHandle 暴露 SenderRef 子集 → inputRef.current 非 null,BridgeInputRef.insert 可达。
    expect(inputRef.current).not.toBeNull();
    expect(typeof inputRef.current?.insert).toBe('function');
  });

  it('断连四态：输入区禁用（disabled）并注入灰卡 CSS 变量（附录 B 口径）', () => {
    const { rerender } = render(
      <GroupChatComposer
        session={session}
        connectionStatus="error"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    const sender = screen.getByTestId('sender');
    expect(sender).toHaveAttribute('data-disabled', 'true');
    expect(sender).toHaveClass('[--aix-color-bg:hsl(var(--muted))]');

    rerender(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    expect(screen.getByTestId('sender')).toHaveAttribute('data-disabled', 'false');
    expect(screen.getByTestId('sender')).not.toHaveClass('[--aix-color-bg:hsl(var(--muted))]');
  });

  it('群会话输入框提示以用户身份发送', () => {
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
      />,
    );
    // 群聊输入框仅支持 @ 提及成员，占位不得宣传 /file /link /skill 指令（那是单聊 ChatPanel 的能力）。
    expect(screen.getByTestId('sender')).toHaveAttribute('data-placeholder', '输入消息... (将以您的用户身份发送到群内)');
  });
});

describe('GroupChatComposer — Open 装配真实任务槽位（能力菜单 + 任务发送拦截）', () => {
  beforeEach(() => {
    mockInsertFileChips.mockClear();
    mockAddFiles.mockClear();
  });

  it('经槽位渲染真实能力菜单：任务/工作流任务在列，选中任务回调 selectDynamic', async () => {
    const user = userEvent.setup();
    const execution = executionFixture();
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
        execution={execution}
      />,
    );

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    // 菜单行在 Radix portal（role=dialog）内；fileChip 桩也有同名「上传文件」按钮，须按气泡作用域查询。
    const menu = await screen.findByRole('dialog');
    expect(within(menu).getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    expect(within(menu).getByRole('button', { name: '任务' })).toBeInTheDocument();
    expect(within(menu).getByRole('button', { name: '工作流任务' })).toBeInTheDocument();

    await user.click(within(menu).getByRole('button', { name: '任务' }));
    expect(execution.selectDynamic).toHaveBeenCalledTimes(1);
  });

  it('任务胶囊选中态提交：经真实 buildTaskInstructions 逐胶囊生成指令消息，并拦截普通发送', () => {
    const onSend = jest.fn();
    const onDraftChange = jest.fn();
    const execution = executionFixture({ selections: [{ kind: 'dynamic' }] });
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={onSend}
        draft=""
        onDraftChange={onDraftChange}
        execution={execution}
      />,
    );

    fireEvent.click(screen.getByTestId('sender-submit'));

    // 真实槽位 payload（registerOpenGroupTaskSlots）现行「/skill/task-intake」指令形态（taskMapper 真源）。
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('/skill/task-intake 追问内容', [], undefined);
    expect(execution.clearSelection).toHaveBeenCalledTimes(1);
    expect(onDraftChange).toHaveBeenCalledWith('');
  });
});
