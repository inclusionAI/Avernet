/** @jest-environment jsdom */
// Task 7 抽取回归测试:交互式(ChatPanel)主舞台从旧混合 Workspace 页迁出后,
// 既有能力仍齐备 —— 选中「我发起的」会话贯通 session ID、任务上下文用登录 Human ID、
// 文件/模型/任务控件保持可用;页面分支契约固化 origin=others 不渲染交互舞台。
import type { IdentityView } from '@/domain/collaboration/types';
import type { ConversationOrigin } from '@/domain/conversation/types';
import { ConversationInteractiveStage } from '@/pages/Workspace/Chat/ConversationInteractiveStage';
import type { UseConversationInteractiveChatOptions } from '@/pages/Workspace/Chat/hooks/useConversationInteractiveChat';
import { useConversationInteractiveChat } from '@/pages/Workspace/Chat/hooks/useConversationInteractiveChat';
import type { UseBotSessionFilesFeatureResult } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { SubmitContext } from '@tc-chat/ui/es/Sender';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type React from 'react';

// ─── 接收端探针(只替换叶子组件,不替换舞台/Hook 的真实装配逻辑) ───

interface StubBaseProps {
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  userAvatarUrl?: string;
  sessionTitle?: string;
  viewer?: unknown;
  target?: { id?: string } | null;
  mode?: string;
  interactive?: boolean;
  draft?: string;
  inputRef?: unknown;
  panelRef?: unknown;
  chatBridge?: unknown;
  historyPagination?: { hasMore: boolean; isLoading: boolean; onLoadMore: () => void };
  fileChip?: unknown;
  command?: unknown;
  fileToolbar?: unknown;
  senderRef?: unknown;
  onManageFiles?: () => void;
  onSend?: (content: string, context?: SubmitContext) => void;
  onDraftChange?: (draft: string) => void;
  onPanelAction?: (action: { type: string; content?: string }) => void;
  modelSelector?: React.ReactElement;
  taskComposer?: React.ReactElement;
}

let mockChatPanelProps: StubBaseProps | null = null;
let mockModelSelectorProps: {
  session?: { sessionId?: string };
  activeIdentityId?: string | null;
  chatBots?: ChatBotView[];
} | null = null;
let mockComposerMenuProps: { disabled?: boolean; disabledReason?: string | null } | null = null;
let mockAgentCodingOpen: jest.Mock = jest.fn();

const mockFileFeature: UseBotSessionFilesFeatureResult = {
  senderRef: { current: null },
  fileChip: { label: '文件', onPick: () => {}, onRemove: () => {} },
  command: { categories: [], onSelect: () => {}, format: () => '' },
  fileToolbar: null,
  featureNode: <div data-testid="file-feature-node" />,
  openFileDrawer: () => {},
  closeFileDrawer: () => {},
  fileDrawerOpen: false,
  openUpload: () => {},
};

const mockUseTaskExecution = jest.fn(
  (): Record<string, unknown> => ({
    submitting: false,
    error: null,
    lastTaskId: null,
    workflows: [],
    workflowsLoading: false,
    loadWorkflows: () => Promise.resolve(),
    selectedWorkflow: null,
    pendingDynamic: false,
    selectWorkflow: () => {},
    selectDynamic: () => {},
    clearSelection: () => {},
    submitFromComposer: () => Promise.resolve({ ok: false, reason: '' }),
    submit: () => Promise.resolve({ ok: false, reason: '' }),
    validate: () => null,
  }),
);

const mockUseTaskExecuteFromCard = jest.fn((): void => undefined);

let mockUseBotSessionFilesFeatureArgs: unknown[] = [];

jest.mock('@/hooks/useTaskExecution', () => ({
  useTaskExecution: (...args: unknown[]) => mockUseTaskExecution(...(args as [])),
}));
jest.mock('@/hooks/useTaskExecuteFromCard', () => ({
  useTaskExecuteFromCard: (...args: unknown[]) => mockUseTaskExecuteFromCard(...(args as [])),
}));
jest.mock('@/pages/Workspace/hooks/useBotSessionFilesFeature', () => ({
  useBotSessionFilesFeature: (...args: unknown[]) => {
    mockUseBotSessionFilesFeatureArgs = args;
    return mockFileFeature;
  },
}));
jest.mock('@/components/Workspace/ChatPanel', () => ({
  ChatPanel: (props: StubBaseProps) => {
    mockChatPanelProps = props;
    return (
      <div data-testid="chat-panel-stub" data-mode={props.mode} data-draft={props.draft ?? ''}>
        <button type="button" data-testid="stub-send" onClick={() => props.onSend?.('第一条消息')} />
        <button
          type="button"
          data-testid="stub-send-with-files"
          onClick={() =>
            props.onSend?.('带文件消息', {
              fileRefs: [{ resource_id: 'res-1', insert_id: 'ins-1', display_name: '报告.docx' }],
              resolvedContent: '带文件消息<file-ref insert_id="ins-1"/>',
            } as unknown as SubmitContext)
          }
        />
        <button type="button" data-testid="stub-draft" onClick={() => props.onDraftChange?.('草稿内容')} />
        <button
          type="button"
          data-testid="stub-panel-fill"
          onClick={() => props.onPanelAction?.({ type: 'fill_input', content: '填充草稿' })}
        />
        {props.modelSelector}
        {props.taskComposer}
      </div>
    );
  },
}));
jest.mock('@/components/Workspace/ChatPanel/BotModelSelector', () => ({
  BotModelSelectorContainer: (props: {
    session?: { sessionId?: string };
    activeIdentityId?: string | null;
    chatBots?: ChatBotView[];
  }) => {
    mockModelSelectorProps = props;
    return <div data-testid="model-selector" data-active-identity={props.activeIdentityId ?? ''} />;
  },
}));
jest.mock('@/components/Workspace/TaskComposerMenu', () => ({
  ComposerCapabilitiesMenu: (props: { disabled?: boolean; disabledReason?: string | null }) => {
    mockComposerMenuProps = props;
    return <div data-testid="composer-menu" data-disabled={String(Boolean(props.disabled))} />;
  },
}));
jest.mock('@/pages/Workspace/components/AgentCodingGuide', () => ({
  AgentCodingGuide: (props: { bot: { displayName: string }; onOpen: (bot: unknown) => void }) => (
    <button type="button" data-testid="agent-coding-guide" onClick={() => props.onOpen?.(props.bot)}>
      {props.bot.displayName}
    </button>
  ),
}));
jest.mock('@/pages/Workspace/components/ReadOnlyConversationPanel', () => ({
  ReadOnlyConversationPanel: () => <div data-testid="read-only-panel" />,
}));

// ─── 测试数据 ───

const chatBotOf = (overrides: Partial<ChatBotView> = {}): ChatBotView =>
  ({
    botId: 'bot-a:327325',
    realBotId: 'bot-a',
    ownerId: '327325',
    displayName: '皮皮虾',
    online: true,
    chatable: true,
    ...overrides,
  } as ChatBotView);

const sessionOf = (sessionId: string, botId = 'bot-a:327325'): BotChatSessionView =>
  ({
    sessionId,
    botId,
    title: `会话 ${sessionId}`,
    messageCount: 3,
    gmtCreate: '',
    gmtModified: '',
  } as BotChatSessionView);

const viewer: IdentityView = { id: 'human_user-101', kind: 'user', displayName: '旧身份名称', online: true };

const targetOf = (botId: string): ConversationTarget => ({
  id: botId,
  name: '皮皮虾',
  avatar: '皮',
  engine: 'OpenClaw',
  status: 'available',
  summary: '与 皮皮虾 单聊 · 3 条消息',
  kind: 'single',
});

const buildBotChatMock = () =>
  ({
    chat: {
      messages: [],
      isRequesting: false,
      isDefaultMessagesRequesting: false,
      retryCount: 0,
      setMessages: jest.fn(),
      setMessage: jest.fn(),
      onRequest: jest.fn(),
    },
    supportState: { phase: 'idle', error: null },
    connectionStatus: 'connected',
    send: jest.fn(),
    stop: jest.fn(),
    reconnect: jest.fn(() => Promise.resolve()),
    reloadHistory: jest.fn(() => Promise.resolve()),
    hasMoreHistory: true,
    isLoadingMoreHistory: false,
    loadMoreHistory: jest.fn(() => Promise.resolve()),
  } as unknown as ReturnType<typeof import('@/pages/Workspace/hooks/useBotChat').useBotChat>);

const buildOptions = (
  overrides: Partial<UseConversationInteractiveChatOptions> = {},
): UseConversationInteractiveChatOptions => {
  const defaultBot = chatBotOf();
  const bot = overrides.chatBots?.[0] ?? defaultBot;
  const session = 'selectedSession' in overrides && overrides.selectedSession === null ? null : sessionOf('session-1');
  return {
    target: targetOf(bot.botId),
    viewer,
    authenticatedUserId: 'user-101',
    authenticatedUserName: '认证用户',
    authenticatedUserAvatarUrl: 'https://example.test/avatar.png',
    requesterIdentityId: 'human_user-101',
    chatBots: [bot],
    selectedSession: session,
    botChat: buildBotChatMock(),
    panelRef: { current: null },
    inputRef: { current: null },
    selectedAgentCodingBot: null,
    sessions: {
      renameSessionOnFirstMessage: jest.fn(() => Promise.resolve(true)),
      clearContext: jest.fn(() => Promise.resolve(true)),
      updateSessionModel: jest.fn(),
    },
    ...overrides,
  };
};

function InteractiveStageHarness(props: { options: UseConversationInteractiveChatOptions }) {
  const model = useConversationInteractiveChat(props.options);
  return (
    <ConversationInteractiveStage
      model={model}
      onOpenSessionList={() => {}}
      onOpenAgentCodingBot={mockAgentCodingOpen}
    />
  );
}

function ReadOnlyConversationPanelStub() {
  return <div data-testid="read-only-panel" />;
}

/** 页面分支契约骨架(plan Task 7 Step 4):origin=others → 只读面板,永不进交互舞台。 */
function PageBranchHarness(props: {
  selectionOrigin: ConversationOrigin;
  options: UseConversationInteractiveChatOptions;
}) {
  const model = useConversationInteractiveChat(props.options);
  return props.selectionOrigin === 'others' ? (
    <div data-testid="read-only-branch">
      <ReadOnlyConversationPanelStub />
    </div>
  ) : (
    <ConversationInteractiveStage model={model} onOpenSessionList={() => {}} onOpenAgentCodingBot={() => {}} />
  );
}

beforeEach(() => {
  mockUseTaskExecution.mockClear();
  mockUseTaskExecuteFromCard.mockClear();
  mockUseBotSessionFilesFeatureArgs = [];
  mockChatPanelProps = null;
  mockModelSelectorProps = null;
  mockComposerMenuProps = null;
});

describe('交互式主舞台(mine / 好友 Bot 单聊)', () => {
  it('选中管理的「我发起」会话:session ID 贯通任务上下文,任务上下文使用登录 Human ID', () => {
    const options = buildOptions();
    render(<InteractiveStageHarness options={options} />);

    expect(screen.getByTestId('chat-panel-stub')).toHaveAttribute('data-mode', 'bot');
    const executionOptions = mockUseTaskExecution.mock.calls.at(-1)?.[0] as {
      context: { ownerUserId: string; ownerBotId: string; mainSessionId: string | null } | null;
    };
    expect(executionOptions.context).toEqual(
      expect.objectContaining({
        sourceType: 'bot',
        ownerUserId: 'user-101',
        ownerBotId: 'bot-a:327325',
        mainSessionId: 'session-1',
      }),
    );
    const fromCardOptions = mockUseTaskExecuteFromCard.mock.calls.at(-1)?.[0] as {
      context: { mainSessionId: string | null } | null;
    };
    expect(fromCardOptions.context?.mainSessionId).toBe('session-1');
    expect(mockModelSelectorProps?.session?.sessionId).toBe('session-1');
    expect(mockModelSelectorProps?.activeIdentityId).toBe('human_user-101');
    expect(mockUseBotSessionFilesFeatureArgs[2]).toBe('human_user-101');
  });

  it('文件/模型/任务/分页控件保持可用并接回既有回调', async () => {
    const options = buildOptions();
    render(<InteractiveStageHarness options={options} />);

    expect(mockChatPanelProps?.fileChip).toBe(mockFileFeature.fileChip);
    expect(mockChatPanelProps?.command).toBe(mockFileFeature.command);
    expect(mockChatPanelProps?.fileToolbar).toBe(mockFileFeature.fileToolbar);
    expect(mockChatPanelProps?.senderRef).toBe(mockFileFeature.senderRef);
    expect(mockChatPanelProps?.onManageFiles).toBe(mockFileFeature.openFileDrawer);
    expect(screen.getByTestId('file-feature-node')).toBeInTheDocument();
    expect(screen.getByTestId('composer-menu')).toBeInTheDocument();
    expect(screen.getByTestId('composer-menu')).toHaveAttribute('data-disabled', 'false');
    expect(screen.getByTestId('model-selector')).toBeInTheDocument();
    expect(mockChatPanelProps?.historyPagination).toEqual(expect.objectContaining({ hasMore: true, isLoading: false }));
    mockChatPanelProps?.historyPagination?.onLoadMore();
    expect(options.botChat.loadMoreHistory).toHaveBeenCalledTimes(1);
    const onClear = mockUseBotSessionFilesFeatureArgs[3] as () => Promise<void>;
    void onClear();
    await waitFor(() => {
      expect(options.sessions.clearContext).toHaveBeenCalledWith(options.chatBots[0], 'session-1');
      expect(options.botChat.reloadHistory).toHaveBeenCalledTimes(1);
    });
  });

  it('发送沿用既有链路:首条消息自动重命名 + 文件引用透传', async () => {
    const options = buildOptions();
    render(<InteractiveStageHarness options={options} />);

    fireEvent.click(screen.getByTestId('stub-send'));
    await waitFor(() => {
      expect(options.botChat.send).toHaveBeenCalledTimes(1);
    });
    expect(options.botChat.send).toHaveBeenCalledWith('第一条消息');
    const sendSession = options.selectedSession as BotChatSessionView;
    expect(options.sessions.renameSessionOnFirstMessage).toHaveBeenCalledWith(
      options.chatBots[0],
      sendSession,
      '第一条消息',
    );

    fireEvent.click(screen.getByTestId('stub-send-with-files'));
    await waitFor(() => {
      expect(options.botChat.send).toHaveBeenCalledTimes(2);
    });
    const sendCalls = (options.botChat.send as jest.Mock).mock.calls as unknown as Array<
      [string, { resourceReferences: unknown[]; promptFileRefs: unknown[]; fileRefDisplay: unknown[] }]
    >;
    const sendOptions = sendCalls[1]?.[1];
    expect(sendOptions.resourceReferences).toEqual([{ type: 'file', resource_id: 'res-1', insert_id: 'ins-1' }]);
    expect(sendOptions.promptFileRefs).toEqual([{ resource_id: 'res-1', insert_id: 'ins-1' }]);
    expect(sendOptions.fileRefDisplay).toEqual([{ insert_id: 'ins-1', name: '报告.docx' }]);
  });

  it('草稿与主→副面板 fill_input 动作回写输入框', () => {
    const options = buildOptions();
    render(<InteractiveStageHarness options={options} />);

    fireEvent.click(screen.getByTestId('stub-draft'));
    expect(screen.getByTestId('chat-panel-stub')).toHaveAttribute('data-draft', '草稿内容');

    fireEvent.click(screen.getByTestId('stub-panel-fill'));
    expect(screen.getByTestId('chat-panel-stub')).toHaveAttribute('data-draft', '填充草稿');
    expect(options.botChat.send).not.toHaveBeenCalled();
  });

  it('认证用户信息与查看者身份透传 ChatPanel', () => {
    const options = buildOptions();
    render(<InteractiveStageHarness options={options} />);

    expect(mockChatPanelProps?.target?.id).toBe('bot-a:327325');
    expect(mockChatPanelProps?.authenticatedUserId).toBe('user-101');
    expect(mockChatPanelProps?.authenticatedUserName).toBe('认证用户');
    expect(mockChatPanelProps?.viewer).toEqual(viewer);
    expect(mockChatPanelProps?.sessionTitle).toBe('会话 session-1');
    expect(mockChatPanelProps?.interactive).toBe(true);
    expect(mockChatPanelProps?.inputRef).toBe(options.inputRef);
    expect(mockChatPanelProps?.panelRef).toBe(options.panelRef);
  });

  it('Agent Coding 引导存在时替代 ChatPanel,并回调打开引导 Bot', () => {
    const guideBot = chatBotOf({ botId: 'ac-bot:327325', displayName: '编码 Bot' });
    const options = buildOptions({ selectedAgentCodingBot: guideBot });
    render(<InteractiveStageHarness options={options} />);

    expect(screen.queryByTestId('chat-panel-stub')).not.toBeInTheDocument();
    expect(screen.getByTestId('agent-coding-guide')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('agent-coding-guide'));
    expect(mockAgentCodingOpen).toHaveBeenCalledWith(guideBot);
  });

  it('无会话时任务菜单降级禁用', () => {
    const options = buildOptions({ selectedSession: null, target: null });
    render(<InteractiveStageHarness options={options} />);

    expect(screen.getByTestId('composer-menu')).toHaveAttribute('data-disabled', 'true');
    expect(mockComposerMenuProps?.disabledReason).toBe('请先选择一个 Bot 会话');
    expect(mockChatPanelProps?.sessionTitle).toBeUndefined();
  });
});

describe('页面分支契约(origin)', () => {
  it('origin=others 只渲染只读面板,不渲染交互舞台', () => {
    render(<PageBranchHarness selectionOrigin="others" options={buildOptions()} />);

    expect(screen.getByTestId('read-only-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-panel-stub')).not.toBeInTheDocument();
    expect(screen.queryByTestId('composer-menu')).not.toBeInTheDocument();
    expect(screen.queryByTestId('model-selector')).not.toBeInTheDocument();
  });

  it('origin=mine(缺省归属)渲染交互舞台', () => {
    render(<PageBranchHarness selectionOrigin="mine" options={buildOptions()} />);

    expect(screen.getByTestId('chat-panel-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('read-only-panel')).not.toBeInTheDocument();
  });
});
