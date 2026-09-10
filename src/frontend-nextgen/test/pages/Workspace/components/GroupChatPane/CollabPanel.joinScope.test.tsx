/** @jest-environment jsdom */
import { CollabPanel } from '@/pages/Workspace/components/GroupChatPane/CollabPanel';
import type { CollabPanelState } from '@/pages/Workspace/hooks/useCollabPanel';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

function makePanel(overrides: Partial<CollabPanelState> = {}): CollabPanelState {
  return {
    visible: true,
    humanAbsentOnly: true,
    botActorId: null,
    botMode: null,
    botName: 'Alpha',
    human: { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'absent' },
    humanJoined: false,
    humanName: '章梧',
    humanAvatarUrl: undefined,
    canSwitchToHuman: true,
    switchingBotMode: false,
    joining: false,
    humanViewScope: null,
    switchingViewScope: false,
    setBotMode: jest.fn<any>().mockResolvedValue(undefined),
    joinSession: jest.fn<any>().mockResolvedValue(true),
    leaveSession: jest.fn<any>().mockResolvedValue(true),
    switchToHuman: jest.fn(),
    setViewScope: jest.fn<any>().mockResolvedValue(true),
    ...overrides,
  };
}

describe('加入当前会话确认弹窗携带 message_view_scope', () => {
  it('勾选「只看公开及与我相关的消息」后确认 → joinSession("participant")', () => {
    const panel = makePanel();
    render(<CollabPanel panel={panel} />);
    fireEvent.click(screen.getByRole('button', { name: '加入当前会话' }));
    expect(screen.getByText('只看公开及与我相关的消息')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    expect(panel.joinSession).toHaveBeenCalledWith('participant');
  });

  it('不勾选确认 → joinSession("full")；弹窗每次打开重置为未勾选', () => {
    const panel = makePanel();
    const { unmount } = render(<CollabPanel panel={panel} />);
    fireEvent.click(screen.getByRole('button', { name: '加入当前会话' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '加入当前会话' }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    expect(panel.joinSession).toHaveBeenCalledWith('full');
    unmount();
  });

  it('弹窗展示「其他须知」提示盒', () => {
    render(<CollabPanel panel={makePanel()} />);
    fireEvent.click(screen.getByRole('button', { name: '加入当前会话' }));
    expect(screen.getByText('其他须知：')).toBeInTheDocument();
  });
});
