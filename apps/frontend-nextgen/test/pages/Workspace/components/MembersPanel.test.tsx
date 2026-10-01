/** @jest-environment jsdom */
import type { GroupView, ParticipantView, SessionView } from '@/domain/collaboration';
import { MembersPanel } from '@/pages/Workspace/components/MembersPanel';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

const owner: ParticipantView = {
  actorId: 'u-owner',
  kind: 'human',
  name: '群主老张',
  role: 'owner',
  mode: 'auto',
  online: true,
};
const member: ParticipantView = {
  actorId: 'u-member',
  kind: 'human',
  name: '普通小李',
  role: 'member',
  mode: 'auto',
  online: true,
};
const bot: ParticipantView = {
  actorId: 'b-bot',
  kind: 'bot',
  name: '辅助Bot',
  role: 'driver',
  mode: 'auto',
  online: true,
};

const group: GroupView = {
  groupId: 'g1',
  name: '主站群',
  kind: 'free_chat',
  status: 'active',
  participants: [owner, member, bot],
  participantCount: 3,
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '会话一',
  kind: 'chat',
  status: 'running',
  participants: [owner, member, bot],
  lastMessageAt: 1,
  createdAt: 1,
  favorite: false,
};

function makeProps(partial: Partial<React.ComponentProps<typeof MembersPanel>> = {}) {
  return {
    group,
    session,
    canManage: { allowed: true } as { allowed: boolean; disabledReason?: string },
    onUpdateMode: jest.fn(),
    onRemoveParticipant: jest.fn(),
    onClose: jest.fn(),
    ...partial,
  };
}

describe('MembersPanel', () => {
  it('renders group participants with role tags', () => {
    render(<MembersPanel {...makeProps()} />);
    expect(screen.getByText('群主老张')).toBeInTheDocument();
    expect(screen.getByText('普通小李')).toBeInTheDocument();
    expect(screen.getByText('辅助Bot')).toBeInTheDocument();
    // role tags
    expect(screen.getByText('群主')).toBeInTheDocument();
    expect(screen.getByText('成员')).toBeInTheDocument();
  });

  it('owner row has no remove button', () => {
    render(<MembersPanel {...makeProps()} />);
    // Find row containing owner name; the remove button has aria-label "移除成员"
    const removeButtons = screen.queryAllByRole('button', { name: '移除成员' });
    // Should be 2 (member + bot driver), not the owner
    expect(removeButtons.length).toBe(2);
    removeButtons.forEach((btn) => {
      const row = btn.closest('[data-row]');
      expect(row?.textContent).not.toContain('群主老张');
    });
  });

  it('clicking remove opens ConfirmDialog then confirm calls onRemoveParticipant', () => {
    const onRemoveParticipant = jest.fn();
    render(<MembersPanel {...makeProps({ onRemoveParticipant })} />);
    // Click the remove button on the member row
    const removeButtons = screen.getAllByRole('button', { name: '移除成员' });
    // Pick the one for 'u-member' (普通小李)
    const memberRemove = removeButtons.find((btn) => {
      const row = btn.closest('[data-row]');
      return row?.textContent?.includes('普通小李');
    })!;
    fireEvent.click(memberRemove);
    // ConfirmDialog should be open with impactText
    expect(screen.getByText('移除后将无法参与当前协作群')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
    expect(onRemoveParticipant).toHaveBeenCalledWith('u-member');
  });

  it('close button calls onClose', () => {
    const onClose = jest.fn();
    render(<MembersPanel {...makeProps({ onClose })} />);
    fireEvent.click(screen.getByRole('button', { name: '关闭成员面板' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('mode control disabled when !canManage.allowed', () => {
    render(<MembersPanel {...makeProps({ canManage: { allowed: false, disabledReason: '仅群主可管理' } })} />);
    // segmented buttons — find by role button with mode labels
    const autoButtons = screen.getAllByRole('button', { name: '自动' });
    autoButtons.forEach((btn) => expect(btn).toBeDisabled());
  });

  it('mode change fires onUpdateMode when allowed', () => {
    const onUpdateMode = jest.fn();
    render(<MembersPanel {...makeProps({ onUpdateMode })} />);
    // Find the row for 普通小李 and click its 静音 mode button
    const mutedButtons = screen.getAllByRole('button', { name: '静音' });
    // pick the one in member row
    const memberMuted = mutedButtons.find((btn) => {
      const row = btn.closest('[data-row]');
      return row?.textContent?.includes('普通小李');
    })!;
    fireEvent.click(memberMuted);
    expect(onUpdateMode).toHaveBeenCalledWith('u-member', 'muted');
  });
});
