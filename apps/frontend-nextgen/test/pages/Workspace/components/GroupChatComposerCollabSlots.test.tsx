/** @jest-environment jsdom */
// collab 形态任务栈回归（独立文件 = 独立模块注册表）：先注册全 null 槽位再静态 import
// composer（其模块作用域常量按求值一次性绑定 null），验证「任务菜单/任务发送分支整体消失」
// 后，任务无关附件菜单（上传文件/添加图片）仍是有效入口。
// 不放回 GroupChatComposer.test.tsx：同文件内 resetModules+require 会让 composer 落到与
// @testing-library(react-dom) 不同的另一份 react 实例（Invalid hook call），独立文件只靠
// 声明序即可实现「null 注册先于模块求值」，零纠缠。
import '../../../helpers/registerCollabNullSlots';
import type { SessionView } from '@/domain/collaboration';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import { GroupChatComposer } from '@/pages/Workspace/components/GroupChatPane/GroupChatComposer';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

const mockInsertFileChips = jest.fn();
const mockAddFiles = jest.fn();

// 桩 Sender 与 GroupChatComposer.test.tsx 同构（forwardRef + useImperativeHandle 证明
// inputRef 真绑定；渲染 toolbar.left 供工具栏断言），去掉 fileChip 按钮避免与附件菜单
// 「上传文件」行同名冲突——本套件的附件入口锚点就是 composer 自身的菜单行。
jest.mock('@tc-chat/ui/es/Sender', () => {
  const { forwardRef, useImperativeHandle } = require('react');
  return {
    Sender: forwardRef(
      (
        _props: {
          className?: string;
          disabled?: boolean;
          placeholder?: string;
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
            data-placeholder={_props.placeholder}
          >
            <div data-testid="sender-toolbar">{_props.toolbar?.left}</div>
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
  UploadFilesModal: ({ open }: { open: boolean }) => (open ? <div data-testid="upload-files-modal" /> : null),
}));
jest.mock('@/pages/Workspace/components/GroupChatPane/mentionHelpers', () => ({
  expandMentionIds: () => [],
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

/** 任务执行装配夹具（结构对齐 useTaskExecution 返回值；本套件用于驱动「槽位 null 优先于装配」降级）。 */
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

function renderComposer(
  execution: UseTaskExecutionResult | null,
  onSend = () => {},
  connectionStatus: ProviderConnectionStatus = 'connected',
) {
  return render(
    <GroupChatComposer
      session={session}
      connectionStatus={connectionStatus}
      mentionConfig={undefined}
      onSend={onSend}
      draft=""
      onDraftChange={() => {}}
      execution={execution}
    />,
  );
}

describe('GroupChatComposer — collab 空槽位（任务栈整体消失，附件入口保留）', () => {
  beforeEach(() => {
    mockInsertFileChips.mockClear();
    mockAddFiles.mockClear();
  });

  it('添加附件 → 添加图片 打开隐藏文件选择器，选中的 PNG 进入图片上传链路', async () => {
    const user = userEvent.setup();
    renderComposer(null);

    const imageInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(imageInput).not.toBeNull();
    const pickerClick = jest.spyOn(imageInput as HTMLInputElement, 'click');

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    await user.click(await screen.findByRole('button', { name: '添加图片' }));
    expect(pickerClick).toHaveBeenCalledTimes(1);

    // 文件选择完成后经 change 事件回流:image hook(addFiles) 收到该 PNG 文件。
    const png = new File(['png-bytes'], 'shot.png', { type: 'image/png' });
    Object.defineProperty(imageInput, 'files', { value: [png] });
    fireEvent.change(imageInput as HTMLInputElement, {});

    expect(mockAddFiles).toHaveBeenCalledTimes(1);
    expect(mockAddFiles.mock.calls[0][0]).toEqual([expect.objectContaining({ name: 'shot.png', type: 'image/png' })]);
  });

  it('添加附件 → 上传文件 打开上传窗口（staging/上传链路保留）', async () => {
    const user = userEvent.setup();
    renderComposer(null);

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.queryByTestId('upload-files-modal')).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: '上传文件' }));
    expect(screen.getByTestId('upload-files-modal')).toBeInTheDocument();
  });

  it('会话在但断连（connectionStatus disconnected）时，添加附件入口灰化（对齐 Sender 断连灰卡语义）', () => {
    renderComposer(null, () => {}, 'disconnected');
    expect(screen.getByRole('button', { name: '添加附件' })).toBeDisabled();
  });

  it('粘贴图片仍进入图片链路；菜单展开后无任务/工作流任务入口', async () => {
    const user = userEvent.setup();
    renderComposer(null);

    fireEvent.click(screen.getByTestId('sender-paste-image'));
    expect(mockAddFiles).toHaveBeenCalledTimes(1);
    expect(mockAddFiles.mock.calls[0][0]).toEqual([
      expect.objectContaining({ name: 'pasted.png', type: 'image/png' }),
    ]);

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加图片' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '任务' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '工作流任务' })).not.toBeInTheDocument();
  });

  it('槽位 null 优先于上层装配：有任务执行也不渲染任务菜单，选中态提交降级为普通消息（不再拦截）', async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();
    const execution = executionFixture({ selections: [{ kind: 'dynamic' }] });
    renderComposer(execution, onSend);

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加图片' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '任务' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '工作流任务' })).not.toBeInTheDocument();

    // buildInstructions 为 null 的 conjunct 兜底：任务指令构造消失，普通发送不被拦截。
    fireEvent.click(screen.getByTestId('sender-submit'));
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('追问内容', [], undefined);
    expect(execution.clearSelection).not.toHaveBeenCalled();
  });

  it('inputRef 仍经 <Sender ref> 真绑定（槽位 null 不影响根因 4 链路）', () => {
    const inputRef = React.createRef<SenderRef>();
    render(
      <GroupChatComposer
        session={session}
        connectionStatus="connected"
        mentionConfig={undefined}
        onSend={() => {}}
        draft=""
        onDraftChange={() => {}}
        execution={null}
        inputRef={inputRef}
      />,
    );
    expect(inputRef.current).not.toBeNull();
    expect(typeof inputRef.current?.insert).toBe('function');
  });
});
