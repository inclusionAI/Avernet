/** @jest-environment jsdom */
import type { GroupView, ParticipantView, SessionView } from '@/domain/collaboration';
import { GroupChatEmptyState } from '@/pages/Workspace/components/GroupChatPane/GroupChatEmptyState';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 协作群空态：成员头像簇 + 主文案 + 群类型副文案 + 点名卡（真实装配才渲染）。
const buildParticipant = (overrides: Partial<ParticipantView>): ParticipantView =>
  ({
    actorId: 'actor-1',
    kind: 'bot',
    name: '成员',
    role: 'member',
    mode: 'auto',
    ...overrides,
  }) as ParticipantView;

const participants: ParticipantView[] = [
  buildParticipant({ actorId: 'driver', name: '掌舵', role: 'driver' }),
  buildParticipant({ actorId: 'bot-2', name: '雄兵' }),
  buildParticipant({ actorId: 'human-1', name: '拓界', kind: 'human', mode: 'present' }),
  buildParticipant({ actorId: 'bot-3', name: '夏泓' }),
  buildParticipant({ actorId: 'bot-4', name: '孟长' }),
];

const session = { sessionId: 'session-1', participants, participantCount: 5 } as unknown as SessionView;

const group = (kind: GroupView['kind']) => ({ groupId: 'group-1', name: '测试群', kind } as unknown as GroupView);

describe('GroupChatEmptyState', () => {
  it('副文案按群类型分流：free_chat 引用真实 Driver 成员名', () => {
    render(<GroupChatEmptyState group={group('free_chat')} session={session} viewerKind="user" />);

    expect(screen.getByText('发送一条消息开始协作群对话')).toBeInTheDocument();
    expect(screen.getByText('掌舵 会理解群聊目标，并协调成员共同回应')).toBeInTheDocument();
  });

  it('task_master_slave 副文案引用 Manager 成员名', () => {
    const managerParticipants = participants.map((participant) =>
      participant.actorId === 'driver' ? { ...participant, role: 'manager' as const } : participant,
    );
    render(
      <GroupChatEmptyState
        group={group('task_master_slave')}
        session={{
          ...session,
          participants: managerParticipants,
        }}
        viewerKind="user"
      />,
    );

    expect(screen.getByText('掌舵 会理解群聊目标，并调度成员分工推进')).toBeInTheDocument();
  });

  it('头像簇 Bot 优先、≤3 枚，其余并入「+n」计数', () => {
    render(<GroupChatEmptyState group={group('free_chat')} session={session} viewerKind="user" />);

    // 会话 5 人（4 Bot + 1 human）：展示 3 枚 Bot 头像 + 「+2」计数 chip（头像簇 aria-hidden，按文本断言）。
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.queryByText('+3')).not.toBeInTheDocument();
  });

  it('Bot 视角不引导发送：主文案述现状，副文案说明按发言模式参与', () => {
    render(<GroupChatEmptyState group={group('free_chat')} session={session} viewerKind="bot" />);

    expect(screen.getByText('协作群对话尚未开始')).toBeInTheDocument();
    expect(screen.getByText('群内出现相关消息后，Bot 将按各自的发言模式参与回应')).toBeInTheDocument();
    expect(screen.queryByText('试试这样开场')).not.toBeInTheDocument();
  });

  it('点名卡按装配取舍：canMention + onInsertDraft 命中时点击写入 "@"', () => {
    const onInsertDraft = jest.fn();
    const { rerender } = render(
      <GroupChatEmptyState
        group={group('free_chat')}
        session={session}
        viewerKind="user"
        canMention
        onInsertDraft={onInsertDraft}
      />,
    );

    const mentionCard = screen.getByRole('button', { name: /点名成员/ });
    expect(screen.getByText('指定参与的成员，或用 ALL-Bots 点名全部 Bot')).toBeInTheDocument();
    fireEvent.click(mentionCard);
    expect(onInsertDraft).toHaveBeenCalledWith('@');

    // 未装配 @ 面板（bot 视角 / mentionConfig 缺失）或不可交互时不渲染行动区。
    rerender(
      <GroupChatEmptyState group={group('free_chat')} session={session} viewerKind="user" canMention={false} />,
    );
    expect(screen.queryByRole('button', { name: /点名成员/ })).not.toBeInTheDocument();
    expect(screen.queryByText('试试这样开场')).not.toBeInTheDocument();
    expect(screen.queryByText(/也可以直接发送/)).not.toBeInTheDocument();
  });
});