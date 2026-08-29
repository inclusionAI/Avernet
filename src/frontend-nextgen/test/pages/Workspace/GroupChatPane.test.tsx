/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupChatPane, resolveSender } from '@/pages/Workspace/components/GroupChatPane';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import { describe, expect, it, jest } from '@jest/globals';
import type { PanelHandle } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

// Stub SDK UI primitives: tests focus on GroupChatPane header/empty dispatch + Sender presence,
// not on SDK bubble/markdown rendering. Stubs avoid pulling ESM @tc-chat/ui into jsdom.
jest.mock('@tc-chat/ui/es/Bubble', () => ({ Bubble: () => null }));
jest.mock('@tc-chat/ui/es/BubbleList', () => ({
  BubbleList: ({ emptyPlaceholder }: { emptyPlaceholder?: React.ReactNode }) => (
    <div data-testid="bubble-list">{emptyPlaceholder}</div>
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
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {} }));

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
    />,
  );
}

describe('GroupChatPane', () => {
  it('renders group name and session title in header when group/session provided', () => {
    renderPane();
    expect(screen.getByText(/主站协作群/)).toBeInTheDocument();
    expect(screen.getByText(/主会话/)).toBeInTheDocument();
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

describe('resolveSender', () => {
  const groupWithParticipants: GroupView = {
    ...group,
    participants: [
      { actorId: 'bot-a', kind: 'bot', name: 'Bot甲', avatarUrl: 'avatar-a', role: 'member', mode: 'auto' },
      { actorId: 'bot-b', kind: 'bot', name: 'Bot乙', role: 'member', mode: 'auto' },
    ],
  };

  it('user message returns undefined (right-aligned "me")', () => {
    const sender = resolveSender(
      { id: 'm1', role: 'user', content: 'hi', status: 'history', extra: { senderId: 'user-1' } } as never,
      groupWithParticipants,
    );
    expect(sender).toBeUndefined();
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
