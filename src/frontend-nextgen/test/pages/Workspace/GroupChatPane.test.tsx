/** @jest-environment jsdom */
import { getMessageSpacingClass } from '@/components/Workspace/messagePresentation';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupChatPane, resolveSender } from '@/pages/Workspace/components/GroupChatPane';
import { GroupChatBubble, ThinkingBubble } from '@/pages/Workspace/components/GroupChatPane/GroupChatBubble';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import { describe, expect, it, jest } from '@jest/globals';
import type { PanelHandle } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

// Stub SDK UI primitives: tests focus on GroupChatPane header/empty dispatch + Sender presence,
// not on SDK bubble/markdown rendering. Stubs avoid pulling ESM @tc-chat/ui into jsdom.
let mockBubbleRenders: Array<Record<string, unknown>> = [];
jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: (props: Record<string, unknown>) => {
    mockBubbleRenders.push(props);
    const blocks = props.blocks as Array<{ content?: string }> | undefined;
    return (
      <div>
        {blocks
          ?.map((block) => block.content)
          .filter(Boolean)
          .join('')}
      </div>
    );
  },
}));
jest.mock('@tc-chat/ui/es/BubbleList', () => ({
  BubbleList: ({ emptyPlaceholder, footer }: { emptyPlaceholder?: React.ReactNode; footer?: React.ReactNode }) => (
    <div data-testid="bubble-list">
      {emptyPlaceholder}
      {footer}
    </div>
  ),
}));
jest.mock('@tc-chat/ui/es/Sender', () => {
  const MockButton = ({ onClick, children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
    <div role="button" tabIndex={0} onClick={onClick} {...props}>
      {children}
    </div>
  );

  return {
    Sender: (props: {
      placeholder?: string;
      onSubmit?: (message: string, context: { mentions: Array<{ id: string; name: string }> }) => void;
    }) => (
      <div>
        <input data-testid="sender" placeholder={props.placeholder} readOnly />
        <MockButton
          data-testid="sender-submit"
          onClick={() => props.onSubmit?.('@ALL 你们呢', { mentions: [{ id: 'ALL', name: 'ALL' }] })}
        >
          submit
        </MockButton>
      </div>
    ),
    ToolbarButton: () => null,
  };
});
jest.mock('@tc-chat/ui/es/Sender/hooks/useImageUpload', () => ({
  useImageUpload: () => ({
    images: [],
    isProcessing: false,
    addImages: jest.fn(),
    removeImage: jest.fn(),
    clearImages: jest.fn(),
    getAttachments: jest.fn(),
    canAddMore: true,
  }),
}));
jest.mock('@tc-chat/ui/es/SystemNotice', () => ({
  SystemNotice: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// stub @tc-chat/adapters ESM transitive（GroupChatPane→services/workspace→botChatProvider），
// 与 test/services/admin/* 同款写法：仅满足模块解析，不触发真实 Provider。
jest.mock('@tc-chat/adapters', () => ({}));
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const ChatLayout = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  ChatLayout.Panel = () => null;
  return { ChatLayout };
});
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {}, fileRefPlugin: {} }));
jest.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const supportState: GroupChatState = { phase: 'ready', error: null };

const group: GroupView = {
  groupId: 'g1',
  name: '主站协作群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  participantCount: 0,
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '主会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 1,
  createdAt: 1,
  favorite: false,
};

function makeChat(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    messages: [],
    isRequesting: false,
    isDefaultMessagesRequesting: false,
    connectionStatus: 'connected',
    retryCount: 0,
    onRequest: () => {},
    abort: () => {},
    reconnect: () => {},
    ...overrides,
  } as never;
}

function renderPane(overrides: Partial<React.ComponentProps<typeof GroupChatPane>> = {}) {
  const { panelRef, ...rest } = overrides;
  return render(
    <MemoryRouter>
      <GroupChatPane
        group={group}
        session={session}
        chat={makeChat()}
        supportState={supportState}
        connectionStatus="connected"
        send={() => {}}
        submitPanelMessage={() => {}}
        stop={() => {}}
        reconnect={() => {}}
        reloadHistory={() => {}}
        canManageGroup={{ allowed: false }}
        activePanel="none"
        onTogglePanel={() => {}}
        onRequestDissolve={() => {}}
        onRequestShareGroup={() => Promise.resolve({ ok: true, data: { invitationUrl: 'https://example.test/g' } })}
        onRequestShareSession={() => Promise.resolve({ ok: true, data: { invitationUrl: 'https://example.test/s' } })}
        panelRef={panelRef ?? React.createRef<PanelHandle>()}
        {...rest}
      />
    </MemoryRouter>,
  );
}

describe('GroupChatPane', () => {
  it('renders group name and session title in header when group/session provided', () => {
    renderPane();
    expect(screen.getByText(/主站协作群/)).toBeInTheDocument();
    expect(screen.getByText(/主会话/)).toBeInTheDocument();
  });

  it('shows Driver processing for a free-chat group independently from user request state', () => {
    renderPane({
      groupBootstrapProcessing: true,
      supportState: { phase: 'preparing', error: null },
      chat: makeChat({ isRequesting: false }),
    });
    expect(screen.getByText('Driver 正在理解群聊目标…')).toBeInTheDocument();
  });

  it('keeps the Manager label for a manager-worker group', () => {
    renderPane({
      group: { ...group, kind: 'task_master_slave' },
      groupBootstrapProcessing: true,
      supportState: { phase: 'preparing', error: null },
      chat: makeChat({ isRequesting: false }),
    });
    expect(screen.getByText('Manager 正在理解群聊目标…')).toBeInTheDocument();
  });

  it('renders empty placeholder when no group is selected', () => {
    renderPane({ group: null, session: null, connectionStatus: 'disconnected' });
    expect(screen.getByText(/选择一个协作群/)).toBeInTheDocument();
  });

  it('group selected without session shows selection prompt and hides composer', () => {
    const humanIdentity = { id: 'human_1', kind: 'user' as const, displayName: '章梧', online: true };
    renderPane({ session: null, activeIdentity: humanIdentity });
    expect(screen.getByText(/请选择或创建一个会话/)).toBeInTheDocument();
    expect(screen.queryByTestId('sender')).not.toBeInTheDocument();
  });

  it('bot 视角不渲染聊天输入框(由协作面板控制 Bot 发言)', () => {
    const botIdentity = { id: 'b:1', kind: 'bot' as const, displayName: 'Alpha', online: true };
    renderPane({ activeIdentity: botIdentity });
    expect(screen.queryByTestId('sender')).not.toBeInTheDocument();
  });

  it('human 视角已加入会话(present)时渲染聊天输入框', () => {
    const humanIdentity = { id: 'human_1', kind: 'user' as const, displayName: '章梧', online: true };
    const humanPresentSession: SessionView = {
      ...session,
      participants: [{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }],
    };
    renderPane({ session: humanPresentSession, activeIdentity: humanIdentity });
    expect(screen.getByTestId('sender')).toBeInTheDocument();
  });

  it('human 提交 @ALL 时展开为全部 bot ids 并传给 send', () => {
    const humanIdentity = { id: 'human_1', kind: 'user' as const, displayName: '章梧', online: true };
    const humanPresentSession: SessionView = {
      ...session,
      participants: [
        { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' },
        { actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' },
        { actorId: 'bot-b', kind: 'bot', name: '乙', role: 'member', mode: 'auto' },
      ],
    };
    const groupWithBots: GroupView = {
      ...group,
      participants: [],
    };
    const send = jest.fn();
    renderPane({ group: groupWithBots, session: humanPresentSession, activeIdentity: humanIdentity, send });
    fireEvent.click(screen.getByTestId('sender-submit'));
    expect(send).toHaveBeenCalledWith('@ALL 你们呢', ['bot-a', 'bot-b'], undefined);
  });
});

describe('message spacing', () => {
  it('同一发送者消息间距为 16px，发送者切换间距为 24px', () => {
    const messages = [
      { id: 'u1', role: 'user', content: '第一条', status: 'history' },
      { id: 'u2', role: 'user', content: '第二条', status: 'history' },
      { id: 'a1', role: 'assistant', content: '回复', status: 'history', extra: { botUuid: 'bot-a' } },
    ] as never;
    expect(getMessageSpacingClass(messages, 0)).toBe('mb-4');
    expect(getMessageSpacingClass(messages, 1)).toBe('mb-6');
    expect(getMessageSpacingClass(messages, 2)).toBe('mb-0');
  });
});

describe('GroupChatBubble', () => {
  it('消息正文使用设计规范的 14px 字号变量，用户浅色背景且 Bot 不加气泡', () => {
    mockBubbleRenders.length = 0;
    render(
      <GroupChatBubble
        message={{ id: 'm1', role: 'assistant', content: '协作结果', status: 'history' } as never}
        isLastMessage
        isRequesting={false}
        group={group}
        participants={[]}
      />,
    );
    render(<ThinkingBubble />);

    expect(mockBubbleRenders).toHaveLength(2);
    expect(mockBubbleRenders.map((props) => props.className)).toEqual([
      'message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]',
      'message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]',
    ]);
    expect((mockBubbleRenders[0].sender as { align?: string; bubbleColor?: string }).align).toBe('left');
    expect((mockBubbleRenders[0].sender as { bubbleColor?: string }).bubbleColor).toBeUndefined();
    expect((mockBubbleRenders[1].sender as { align?: string }).align).toBe('left');
    expect((mockBubbleRenders[0].markdown as { extensions?: unknown[] }).extensions).toEqual([{}, {}]);
  });
});

describe('GroupChatBubble alignment and participant identity', () => {
  it('human 消息右对齐并使用浅色背景，Bot 消息左对齐且不传背景色', () => {
    mockBubbleRenders.length = 0;
    const participants = [
      {
        actorId: 'human_123123',
        kind: 'human' as const,
        name: '李四',
        role: 'member' as const,
        mode: 'present' as const,
      },
      { actorId: 'bot-a', kind: 'bot' as const, name: 'Bot甲', role: 'member' as const, mode: 'auto' as const },
    ];
    render(
      <>
        <GroupChatBubble
          message={
            {
              id: 'm-human',
              role: 'user',
              content: '问题',
              status: 'history',
              extra: { senderId: '123123', displayTime: '10:31' },
            } as never
          }
          isLastMessage={false}
          isRequesting={false}
          group={group}
          participants={participants}
          userIdentityId="human_999"
        />
        <GroupChatBubble
          message={
            {
              id: 'm-bot',
              role: 'assistant',
              content: '回复',
              status: 'history',
              extra: { senderId: 'bot-a', displayTime: '10:32' },
            } as never
          }
          isLastMessage
          isRequesting={false}
          group={group}
          participants={participants}
        />
      </>,
    );
    expect((mockBubbleRenders[0].sender as { align?: string }).align).toBe('right');
    expect((mockBubbleRenders[0].sender as { bubbleColor?: string }).bubbleColor).toBe('hsl(var(--primary) / 0.1)');
    expect((mockBubbleRenders[1].sender as { align?: string }).align).toBe('left');
    expect((mockBubbleRenders[1].sender as { bubbleColor?: string }).bubbleColor).toBeUndefined();
    const messageMeta = screen.getAllByTestId('message-sender-meta');
    expect(messageMeta).toHaveLength(2);
    expect(messageMeta[0]).toHaveTextContent('李四');
    expect(messageMeta[0]).toHaveTextContent('10:31');
    expect(messageMeta[0]).toHaveClass('justify-end', 'text-right');
    expect(messageMeta[1]).toHaveTextContent('Bot甲');
    expect(messageMeta[1]).toHaveTextContent('10:32');
    expect(messageMeta[1]).toHaveClass('justify-start', 'text-left');
    expect((mockBubbleRenders[0] as { timestamp?: string }).timestamp).toBeUndefined();
    expect((mockBubbleRenders[0].sender as { name?: string }).name).toBeUndefined();
    expect((mockBubbleRenders[1] as { timestamp?: string }).timestamp).toBeUndefined();
    expect((mockBubbleRenders[1].sender as { name?: string }).name).toBeUndefined();
  });
});

describe('GroupChatBubble edit action', () => {
  it('只在调用方标记的最近用户消息上显示底部编辑入口，并把回调接到操作按钮', () => {
    mockBubbleRenders.length = 0;
    const onEdit = jest.fn();
    render(
      <GroupChatBubble
        message={{ id: 'm-user', role: 'user', content: '需要修改的问题', status: 'history' } as never}
        isLastMessage
        isRequesting={false}
        group={group}
        participants={[]}
        isEditable
        onEdit={onEdit}
      />,
    );

    const actionArea = screen.getByTestId('message-copy-action-m-user');
    expect(actionArea).toHaveClass('justify-end', 'pr-11');
    expect(screen.getByRole('button', { name: '编辑消息' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '编辑消息' }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('Bot 消息不会显示编辑入口', () => {
    mockBubbleRenders.length = 0;
    render(
      <GroupChatBubble
        message={{ id: 'm-bot', role: 'assistant', content: 'Bot 回复', status: 'history' } as never}
        isLastMessage
        isRequesting={false}
        group={group}
        participants={[]}
        isEditable
        onEdit={jest.fn()}
      />,
    );

    const actions = mockBubbleRenders[0].actions as React.ReactElement;
    render(actions);
    expect(screen.queryByRole('button', { name: '编辑消息' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '引用消息' })).not.toBeInTheDocument();
  });
});

describe('GroupChatBubble permanent copy action', () => {
  it('在用户与 Bot 消息末尾显性展示复制入口，并按消息方向对齐', () => {
    mockBubbleRenders.length = 0;
    const onCopy = jest.fn();
    render(
      <>
        <GroupChatBubble
          message={{ id: 'm-user-copy', role: 'user', content: '用户问题', status: 'history' } as never}
          isLastMessage={false}
          isRequesting={false}
          group={group}
          participants={[]}
          onCopy={onCopy}
        />
        <GroupChatBubble
          message={{ id: 'm-bot-copy', role: 'assistant', content: 'Bot 回复', status: 'history' } as never}
          isLastMessage
          isRequesting={false}
          group={group}
          participants={[]}
          onCopy={onCopy}
        />
      </>,
    );

    expect(screen.getByTestId('message-copy-action-m-user-copy')).toHaveClass('justify-end', 'pr-11');
    expect(screen.getByTestId('message-copy-action-m-bot-copy')).toHaveClass('justify-start', 'pl-11');
    expect(screen.getAllByRole('button', { name: '复制整条消息' })).toHaveLength(2);

    fireEvent.click(screen.getByTestId('message-copy-action-m-user-copy').querySelector('button') as HTMLButtonElement);
    expect(onCopy).toHaveBeenCalledWith('用户问题');
  });
});

describe('GroupChatBubble copy feedback', () => {
  it('Bot 消息复制成功后在底部操作项显示已复制反馈', async () => {
    mockBubbleRenders.length = 0;
    const onCopy = jest.fn().mockResolvedValue(true);
    render(
      <GroupChatBubble
        message={{ id: 'm-bot-copy-feedback', role: 'assistant', content: 'Bot 回复', status: 'history' } as never}
        isLastMessage
        isRequesting={false}
        group={group}
        participants={[]}
        onCopy={onCopy}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制整条消息' }));
    expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument();
    expect(onCopy).toHaveBeenCalledWith('Bot 回复');
  });
});

describe('GroupChatBubble message actions', () => {
  it('流式 assistant 消息把停止生成动作接到现有 stop 回调', () => {
    mockBubbleRenders.length = 0;
    const onStop = jest.fn();
    render(
      <GroupChatBubble
        message={{ id: 'm-stream', role: 'assistant', content: '生成中', status: 'streaming' } as never}
        isLastMessage
        isRequesting
        group={group}
        participants={[]}
        onStop={onStop}
      />,
    );

    const actions = mockBubbleRenders[0].actions as React.ReactElement<{ onStop?: () => void }>;
    render(actions);
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});

describe('GroupChatBubble avatar wiring', () => {
  it('把顶栏用户头像传给用户消息，Bot 消息不复用该头像', () => {
    mockBubbleRenders.length = 0;
    render(
      <GroupChatBubble
        message={{ id: 'm-user', role: 'user', content: 'hi', status: 'history' } as never}
        isLastMessage
        isRequesting={false}
        group={group}
        participants={[]}
        userAvatarUrl="https://example.test/current-user.png"
      />,
    );

    expect(screen.getByRole('img', { name: '未命名成员' })).toHaveAttribute(
      'src',
      'https://example.test/current-user.png',
    );
    expect((mockBubbleRenders[0].sender as { avatar?: React.ReactNode }).avatar).toBeUndefined();
  });
});

describe('resolveSender', () => {
  const groupWithParticipants: GroupView = {
    ...group,
    participants: [
      { actorId: 'bot-a', kind: 'bot', name: 'Bot甲', avatarUrl: 'avatar-a', role: 'member', mode: 'auto' },
      { actorId: 'bot-b', kind: 'bot', name: 'Bot乙', role: 'member', mode: 'auto' },
    ],
  };

  it('user message resolves the human participant name instead of using ambiguous "你"', () => {
    const groupWithHuman = {
      ...groupWithParticipants,
      participants: [
        ...groupWithParticipants.participants,
        { actorId: 'user-1', kind: 'human' as const, name: '章梧', role: 'member' as const, mode: 'present' as const },
      ],
    };
    const sender = resolveSender(
      { id: 'm1', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'user-1' } } as never,
      groupWithHuman,
    );
    expect(sender?.name).toBe('章梧');
  });

  it('user message prefers local echo senderName and senderAvatarUrl', () => {
    const sender = resolveSender(
      {
        id: 'm1',
        role: 'user',
        content: 'hi',
        status: 'history',
        extra: { senderName: '当前身份', senderAvatarUrl: 'avatar-user' },
      } as never,
      groupWithParticipants,
    );
    expect(sender?.name).toBe('当前身份');
  });

  it('当前用户消息优先使用顶栏真实用户头像，其他 human 成员保留自身头像', () => {
    const userAvatarUrl = 'https://example.test/current-user.png';
    const groupWithHumans = {
      ...groupWithParticipants,
      participants: [
        ...groupWithParticipants.participants,
        {
          actorId: 'current-user',
          kind: 'human' as const,
          name: '当前身份',
          role: 'member' as const,
          mode: 'present' as const,
        },
        {
          actorId: 'other-user',
          kind: 'human' as const,
          name: '其他成员',
          avatarUrl: 'other-avatar',
          role: 'member' as const,
          mode: 'present' as const,
        },
      ],
    };
    const currentSender = resolveSender(
      { id: 'm-current', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'current-user' } } as never,
      groupWithHumans,
      undefined,
      userAvatarUrl,
      'current-user',
    );
    const otherSender = resolveSender(
      { id: 'm-other', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'other-user' } } as never,
      groupWithHumans,
      undefined,
      userAvatarUrl,
      'current-user',
    );
    const currentAvatar = render(<>{currentSender?.avatar}</>);
    const otherAvatar = render(<>{otherSender?.avatar}</>);

    expect(currentAvatar.container.querySelector('img')).toHaveAttribute('src', userAvatarUrl);
    expect(otherAvatar.container.querySelector('img')).toHaveAttribute('src', 'other-avatar');
  });

  it('human 成员优先使用成员头像，成员头像缺失时回退消息回显头像', () => {
    const sender = resolveSender(
      {
        id: 'm1',
        role: 'user',
        content: 'hi',
        status: 'history',
        extra: { senderId: 'human_123123', senderAvatarUrl: 'avatar-echo' },
      } as never,
      group,
      [{ actorId: '123123', kind: 'human' as const, name: '李四', role: 'member' as const, mode: 'present' as const }],
      undefined,
      '999999',
    );
    const avatar = render(<>{sender?.avatar}</>);
    expect(avatar.container.querySelector('img')).toHaveAttribute('src', 'avatar-echo');
  });

  it('human_ 前缀与 user_id 形式可匹配对应成员头像，缺头像时回退姓名首字母', () => {
    const participants = [
      {
        actorId: 'user_id:123123',
        kind: 'human' as const,
        name: '李四',
        avatarUrl: 'avatar-li',
        role: 'member' as const,
        mode: 'present' as const,
      },
      { actorId: '456456', kind: 'human' as const, name: '王五', role: 'member' as const, mode: 'present' as const },
    ];
    const matched = resolveSender(
      { id: 'm1', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'human_123123' } } as never,
      group,
      participants,
      'https://example.test/current-user.png',
      '999999',
    );
    const fallback = resolveSender(
      { id: 'm2', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'human_456456' } } as never,
      group,
      participants,
      'https://example.test/current-user.png',
      '999999',
    );
    const matchedAvatar = render(<>{matched?.avatar}</>);
    const fallbackAvatar = render(<>{fallback?.avatar}</>);
    expect(matchedAvatar.container.querySelector('img')).toHaveAttribute('src', 'avatar-li');
    expect(fallbackAvatar.container.querySelector('img')).toBeNull();
    expect(fallbackAvatar.container.textContent).toBe('王');
  });

  it('assistant message resolves name from participant by senderId, NOT group.name', () => {
    const sender = resolveSender(
      { id: 'm1', role: 'assistant', content: 'hi', status: 'history', extra: { senderId: 'bot-a' } } as never,
      groupWithParticipants,
    );
    expect(sender?.name).toBe('Bot甲');
    expect(sender?.name).not.toBe('主站协作群');
  });

  it('assistant message prefers extra.botName over participant lookup', () => {
    const sender = resolveSender(
      { id: 'm1', role: 'assistant', content: 'hi', status: 'history', extra: { botName: '波士顿龙虾' } } as never,
      groupWithParticipants,
    );
    expect(sender?.name).toBe('波士顿龙虾');
  });

  it('assistant message with no botName and null group falls back to default name', () => {
    const sender = resolveSender({ id: 'm1', role: 'assistant', content: 'hi', status: 'history' } as never, null);
    expect(sender?.name).toBe('Bot');
  });

  it('falls back to group.name only when no participant matches senderId', () => {
    const sender = resolveSender(
      { id: 'm1', role: 'assistant', content: 'hi', status: 'history', extra: { senderId: 'unknown-bot' } } as never,
      groupWithParticipants,
    );
    expect(sender?.name).toBe('主站协作群');
  });

  it('ws 消息 botName 退化为 botUuid 时,通过会话成员匹配真实 bot 名称', () => {
    const sessionParticipants: GroupView['participants'] = [
      { actorId: 'bot-c', kind: 'bot', name: '会话Bot', role: 'member', mode: 'auto' },
    ];
    const sender = resolveSender(
      {
        id: 'm1',
        role: 'assistant',
        content: '...',
        status: 'streaming',
        extra: { botUuid: 'bot-c', botName: 'bot-c' },
      } as never,
      groupWithParticipants,
      sessionParticipants,
    );
    expect(sender?.name).toBe('会话Bot');
  });

  it('bot 仍在回复(pending 占位消息)时不展示发送者名称,避免误用群名', () => {
    const sender = resolveSender(
      { id: 'm1', role: 'assistant', content: '', status: 'pending' } as never,
      groupWithParticipants,
    );
    expect(sender).toBeUndefined();
  });
});
