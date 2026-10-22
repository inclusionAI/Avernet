/** @jest-environment jsdom */
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import type { SessionView } from '@/domain/collaboration';
import { createBotChatProvider } from '@/services/workspace/botChatProvider';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

jest.mock('@tc-chat/ui/es/Sender', () => ({
  Sender: (props: {
    value: string;
    onChange: (value: string) => void;
    onSubmit: (
      value: string,
      context?: { mentions: Array<{ id: string; name: string }>; commands: never[] },
    ) => void;
    mention?: { categories?: Array<{ items?: Array<{ id: string; name: string }> }> };
    disabled?: boolean;
    loading?: boolean;
    placeholder?: string;
  }) => {
    const mentionItems = props.mention?.categories?.flatMap((category) => category.items ?? []) ?? [];
    const botMention = mentionItems.find((item) => item.id === 'bot-2:u-1');
    return (
      <div>
        <input
          aria-label="任务会话输入框"
          data-mention-ids={mentionItems.map((item) => item.id).join(',')}
          value={props.value}
          onChange={(event) => props.onChange(event.target.value)}
          disabled={props.disabled}
          placeholder={props.placeholder}
        />
        <button
          type="button"
          aria-label="发送任务会话消息"
          disabled={props.disabled || props.loading}
          onClick={() => props.onSubmit(props.value)}
        >
          发送
        </button>
        <button
          type="button"
          aria-label="发送带提及的任务会话消息"
          disabled={props.disabled || props.loading || !botMention}
          onClick={() =>
            botMention && props.onSubmit(props.value, { mentions: [botMention], commands: [] })
          }
        >
          发送带提及
        </button>
      </div>
    );
  },
}));

jest.mock('@/services/workspace/botChatProvider');

jest.mock('@/services/workspace/botSessionService');

jest.mock('@/services/workspace/groupChatProvider');

jest.mock('@/services/workspace/groupChatProviderHelpers');

jest.mock('@/services/workspace/sessionService');

jest.mock('sonner');

interface MockLinkedProvider {
  connect: jest.Mock;
  disconnect: jest.Mock;
  request: jest.Mock;
  enterLiveMode?: jest.Mock;
  onMessage?: (message: never) => void;
  onComplete?: (messages: never[]) => void;
  onError?: (error: Error) => void;
}

const onClose = jest.fn();
const mockCreateBotChatProvider = createBotChatProvider as jest.MockedFunction<typeof createBotChatProvider>;
const mockCreateGroupChatProvider = createGroupChatProvider as jest.MockedFunction<typeof createGroupChatProvider>;
const mockListOwnedBots = botSessionService.listOwnedBots as jest.MockedFunction<
  typeof botSessionService.listOwnedBots
>;
const mockGetSessionDetail = sessionService.getSessionDetail as jest.MockedFunction<
  typeof sessionService.getSessionDetail
>;

function createMockProvider(group = false): MockLinkedProvider {
  return {
    connect: jest.fn(() => Promise.resolve()),
    disconnect: jest.fn(),
    request: jest.fn(() => Promise.resolve()),
    ...(group ? { enterLiveMode: jest.fn() } : {}),
  };
}

function makeSession(mode: 'present' | 'absent' = 'present'): SessionView {
  return {
    sessionId: 'bcs_grp_902:round:2',
    groupId: 'g-902',
    title: '任务会话',
    kind: 'chat',
    status: 'running',
    participants: [
      {
        actorId: 'human_u-1',
        kind: 'human',
        name: '用户一',
        role: 'member',
        mode,
      },
      {
        actorId: 'bot-2:u-1',
        kind: 'bot',
        name: '分析 Bot',
        role: 'worker',
        mode: 'auto',
      },
    ],
    lastMessageAt: 0,
    createdAt: 0,
    favorite: false,
  };
}

const ownedBot: ChatBotView = {
  botId: 'bot-9:u-1',
  realBotId: 'bot-9',
  ownerId: 'u-1',
  displayName: 'Bot 9',
  online: true,
  chatable: true,
};

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  (global as unknown as { fetch: unknown }).fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({ code: 0, message: 'ok', data: null, request_id: 'r' }),
    }),
  );
  mockGetSessionDetail.mockResolvedValue({ ok: true, data: makeSession() });
  mockListOwnedBots.mockResolvedValue({ ok: true, data: [ownedBot] });
});

describe('TaskLinkedSession(任务节点会话联动视图)', () => {
  it('群目标展示群会话并拉取指定 collaboration session', async () => {
    mockCreateGroupChatProvider.mockReturnValue(createMockProvider(true) as never);

    render(
      <TaskLinkedSession
        target={{
          sessionId: 'bcs_grp_902:round:2',
          kind: 'group',
          groupId: 'g-902',
          botId: null,
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    expect(screen.getByText('任务联动 · 群会话')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('群成员（0）')).toBeInTheDocument());

    const urls = ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).map((call) =>
      String(call[0]),
    );
    expect(urls).toContain('/openapi/v1/collaboration/groups/g-902');
    expect(
      urls.some((url) => url.includes('/openapi/v1/collaboration/sessions/bcs_grp_902%3Around%3A2/messages?')),
    ).toBe(true);
  });

  it('群任务节点使用目标 session 的独立 Provider 发送消息', async () => {
    const provider = createMockProvider(true);
    mockCreateGroupChatProvider.mockReturnValue(provider as never);

    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    const input = screen.getByRole('textbox', { name: '任务会话输入框' });
    await waitFor(() => expect(input).toBeEnabled());
    expect(provider.enterLiveMode).toHaveBeenCalledTimes(1);
    expect(mockCreateGroupChatProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: 'bcs_grp_902:round:2',
        groupId: 'g-902',
        identityId: 'u-1',
        liveEnabled: true,
      }),
    );

    fireEvent.change(input, { target: { value: '群消息' } });
    fireEvent.click(screen.getByRole('button', { name: '发送任务会话消息' }));

    await waitFor(() =>
      expect(provider.request).toHaveBeenCalledWith({
        content: '群消息',
        sessionId: 'bcs_grp_902:round:2',
      }),
    );
    await waitFor(() => expect(input).toHaveValue(''));
  });

  it('群任务节点支持 @ 会话角色并把 mention id 传给 Provider', async () => {
    const provider = createMockProvider(true);
    mockCreateGroupChatProvider.mockReturnValue(provider as never);

    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    const input = screen.getByRole('textbox', { name: '任务会话输入框' });
    await waitFor(() => expect(input).toBeEnabled());
    expect(input).toHaveAttribute('data-mention-ids', 'ALL-Bots,bot-2:u-1');

    fireEvent.change(input, { target: { value: '@分析 Bot 请处理' } });
    fireEvent.click(screen.getByRole('button', { name: '发送带提及的任务会话消息' }));

    await waitFor(() =>
      expect(provider.request).toHaveBeenCalledWith({
        content: '@分析 Bot 请处理',
        sessionId: 'bcs_grp_902:round:2',
        mentions: ['bot-2:u-1'],
      }),
    );
  });

  it('Bot 目标展示单聊并使用目标 bot session 发送消息', async () => {
    const provider = createMockProvider();
    mockCreateBotChatProvider.mockReturnValue(provider as never);

    render(
      <TaskLinkedSession
        target={{
          sessionId: 'agent:main:session:s-1:user:u-1',
          kind: 'bot',
          groupId: null,
          botId: 'bot-9:u-1',
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    expect(screen.getByText('任务联动 · Bot 会话')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('执行者（1）')).toBeInTheDocument());
    const input = screen.getByRole('textbox', { name: '任务会话输入框' });
    await waitFor(() => expect(input).toBeEnabled());
    expect(mockCreateBotChatProvider).toHaveBeenCalledWith({
      bot: ownedBot,
      userId: 'u-1',
      sessionId: 'agent:main:session:s-1:user:u-1',
    });

    fireEvent.change(input, { target: { value: 'Bot 消息' } });
    fireEvent.click(screen.getByRole('button', { name: '发送任务会话消息' }));

    await waitFor(() =>
      expect(provider.request).toHaveBeenCalledWith({
        content: 'Bot 消息',
        sessionId: 'agent:main:session:s-1:user:u-1',
      }),
    );

    const urls = ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).map((call) =>
      String(call[0]),
    );
    expect(
      urls.some(
        (url) =>
          url.includes('/openapi/v1/bots/bot-9/sessions/agent%3Amain%3Asession%3As-1%3Auser%3Au-1/messages?') &&
          url.includes('user_id=u-1') &&
          url.includes('owner_id=u-1'),
      ),
    ).toBe(true);
  });

  it('当前账号没有目标 Bot 权限时保留输入框但禁止发送', async () => {
    mockListOwnedBots.mockResolvedValue({ ok: true, data: [] });

    render(
      <TaskLinkedSession
        target={{
          sessionId: 'agent:main:session:s-1:user:u-2',
          kind: 'bot',
          groupId: null,
          botId: 'bot-9:u-2',
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    const input = screen.getByRole('textbox', { name: '任务会话输入框' });
    await waitFor(() => expect(screen.getByText('当前账号没有该 Bot 会话的发送权限。')).toBeInTheDocument());
    expect(input).toBeDisabled();
    expect(mockCreateBotChatProvider).not.toHaveBeenCalled();
  });

  it('Human 已退出目标群会话时保留输入框但禁止发送', async () => {
    mockGetSessionDetail.mockResolvedValue({ ok: true, data: makeSession('absent') });

    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    const input = screen.getByRole('textbox', { name: '任务会话输入框' });
    await waitFor(() => expect(screen.getByText('您尚未加入该任务会话，无法发送消息。')).toBeInTheDocument());
    expect(input).toBeDisabled();
    expect(mockCreateGroupChatProvider).not.toHaveBeenCalled();
  });

  it('组件卸载时断开任务节点专属 Provider', async () => {
    const provider = createMockProvider(true);
    mockCreateGroupChatProvider.mockReturnValue(provider as never);
    const view = render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    await waitFor(() => expect(screen.getByRole('textbox', { name: '任务会话输入框' })).toBeEnabled());
    view.unmount();

    expect(provider.disconnect).toHaveBeenCalled();
  });

  it('Provider 完成回复后刷新目标会话内容', async () => {
    const provider = createMockProvider(true);
    mockCreateGroupChatProvider.mockReturnValue(provider as never);
    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902', botId: null }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    await waitFor(() => expect(screen.getByRole('textbox', { name: '任务会话输入框' })).toBeEnabled());
    const messageUrl = '/openapi/v1/collaboration/sessions/bcs_grp_902%3Around%3A2/messages?';
    const messageFetchCount = () =>
      ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).filter((call) =>
        String(call[0]).includes(messageUrl),
      ).length;
    await waitFor(() => expect(messageFetchCount()).toBeGreaterThan(0));
    const memberToggle = screen.getByRole('button', { name: /群成员/ });
    fireEvent.click(memberToggle);
    expect(memberToggle).toHaveAttribute('aria-expanded', 'true');
    const before = messageFetchCount();

    act(() => provider.onComplete?.([]));

    await waitFor(() => expect(messageFetchCount()).toBeGreaterThan(before));
    expect(screen.queryByText('正在加载执行信息…')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /群成员/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('Bot 回复事件触发延迟收敛刷新，避免历史接口短暂返回旧快照', async () => {
    const provider = createMockProvider();
    mockCreateBotChatProvider.mockReturnValue(provider as never);
    render(
      <TaskLinkedSession
        target={{
          sessionId: 'agent:main:session:s-1:user:u-1',
          kind: 'bot',
          groupId: null,
          botId: 'bot-9:u-1',
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    await waitFor(() => expect(screen.getByRole('textbox', { name: '任务会话输入框' })).toBeEnabled());
    const messageUrl = '/openapi/v1/bots/bot-9/sessions/agent%3Amain%3Asession%3As-1%3Auser%3Au-1/messages?';
    const messageFetchCount = () =>
      ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).filter((call) =>
        String(call[0]).includes(messageUrl),
      ).length;
    await waitFor(() => expect(messageFetchCount()).toBeGreaterThan(0));
    const memberToggle = screen.getByRole('button', { name: /执行者/ });
    fireEvent.click(memberToggle);
    expect(memberToggle).toHaveAttribute('aria-expanded', 'true');
    const before = messageFetchCount();

    jest.useFakeTimers();
    try {
      act(() => provider.onComplete?.([]));
      await act(async () => {});
      expect(messageFetchCount()).toBeGreaterThan(before);
      expect(screen.queryByText('正在加载执行信息…')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /执行者/ })).toHaveAttribute('aria-expanded', 'true');
      const afterImmediateRefresh = messageFetchCount();

      act(() => jest.advanceTimersByTime(200));
      act(() => provider.onMessage?.(undefined as never));
      await act(async () => {
        jest.advanceTimersByTime(200);
      });
      expect(messageFetchCount()).toBeGreaterThan(afterImmediateRefresh);
      const afterFirstConvergenceRefresh = messageFetchCount();

      await act(async () => {
        jest.advanceTimersByTime(1_200);
      });
      expect(messageFetchCount()).toBeGreaterThan(afterFirstConvergenceRefresh);
    } finally {
      jest.useRealTimers();
    }
  });

  it('返回当前会话按钮调用 onClose', async () => {
    mockCreateGroupChatProvider.mockReturnValue(createMockProvider(true) as never);

    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902' }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    await waitFor(() => expect(screen.getByRole('textbox', { name: '任务会话输入框' })).toBeEnabled());
    await waitFor(() => expect(screen.getByText('群成员（0）')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '返回当前会话' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
