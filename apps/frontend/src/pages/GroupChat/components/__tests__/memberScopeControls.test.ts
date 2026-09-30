import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import GroupMembersSection from '../GroupSettings/GroupMembersSection';
import SessionMembersSection from '../SessionSettings/SessionMembersSection';

let mockUserId = 'alice';
jest.mock('@/stores/userStore', () => ({
  useUserStore: (selector: any) => selector({ userId: mockUserId }),
}));
jest.mock('@/pages/GroupChat/hooks/useGroupMembers', () => ({
  useGroupMembers: () => ({}),
}));
jest.mock('@/pages/GroupChat/hooks/useSessionMembers', () => ({
  useSessionMembers: () => ({}),
}));
jest.mock('@/components/BotAvatar', () => () => null);

const participants = [
  { id: 'human_alice', name: 'Alice', actorKind: 'human' },
  { id: 'human_bob', name: 'Bob', actorKind: 'human' },
  { id: 'bot_1', name: 'Bot', actorKind: 'bot' },
];

describe.each(['group', 'session'])('%s member scope controls', (level) => {
  function render(isOwner = false) {
    const group = { id: 'group_1', participants };
    const props = { group, isOwner, onClickAddMember: jest.fn() };
    return renderToStaticMarkup(
      level === 'group'
        ? React.createElement(GroupMembersSection, props as any)
        : React.createElement(SessionMembersSection, {
            ...props,
            session: {
              sessionId: 'session_1',
              members: participants.map((p) => ({ ...p, actorId: p.id })),
            },
          } as any),
    );
  }

  beforeEach(() => { mockUserId = 'alice'; });

  it('lets a non-owner change only their own human scope', () => {
    const html = render();
    expect(html.match(/<select /g)).toHaveLength(1);
    expect(html).toContain('aria-label="设置 Alice');
    expect(html).not.toContain('aria-label="设置 Bob');
    expect(html).toContain('value="participant"');
    expect(html).not.toContain('添加成员');
  });

  it('keeps owner controls for human members only', () => {
    expect(render(true).match(/<select /g)).toHaveLength(2);
  });

  it('does not expose self controls without a logged-in user', () => {
    mockUserId = '';
    expect(render()).not.toContain('<select ');
  });
});
