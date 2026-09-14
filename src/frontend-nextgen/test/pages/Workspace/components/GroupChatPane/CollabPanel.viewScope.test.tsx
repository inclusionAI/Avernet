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
    humanAbsentOnly: false,
    botActorId: null,
    botMode: null,
    botName: 'Alpha',
    human: {
      actorId: 'human_1',
      kind: 'human',
      name: '章梧',
      role: 'member',
      mode: 'present',
      messageViewScope: 'full',
    },
    humanJoined: true,
    humanName: '章梧',
    humanAvatarUrl: undefined,
    canSwitchToHuman: true,
    switchingBotMode: false,
    joining: false,
    humanViewScope: 'full',
    switchingViewScope: false,
    setBotMode: jest.fn<any>().mockResolvedValue(undefined),
    joinSession: jest.fn<any>().mockResolvedValue(true),
    leaveSession: jest.fn<any>().mockResolvedValue(true),
    switchToHuman: jest.fn(),
    setViewScope: jest.fn<any>().mockResolvedValue(true),
    ...overrides,
  };
}

describe('隐身条视角切换按钮', () => {
  it('human present 且回显 full：渲染「切换到参与者视角」按钮，点击切到 participant', () => {
    const panel = makePanel();
    render(<CollabPanel panel={panel} />);
    expect(screen.getByText('在会话中隐身')).toBeInTheDocument();
    const btn = screen.getByRole('button', { name: /切换到参与者视角/ });
    fireEvent.click(btn);
    expect(panel.setViewScope).toHaveBeenCalledWith('participant');
  });

  it('回显 participant：渲染「切换到完整视角」按钮，点击切回 full；标题行出现「参与者视角」Badge', () => {
    const panel = makePanel({ humanViewScope: 'participant' });
    render(<CollabPanel panel={panel} />);
    const btn = screen.getByRole('button', { name: /切换到完整视角/ });
    fireEvent.click(btn);
    expect(panel.setViewScope).toHaveBeenCalledWith('full');
    expect(screen.getByText('参与者视角')).toBeInTheDocument();
  });

  it('回显 full：标题行出现「完整视角」Badge', () => {
    render(<CollabPanel panel={makePanel()} />);
    expect(screen.getByText('完整视角')).toBeInTheDocument();
  });

  it('switchingViewScope 进行中切换按钮禁用', () => {
    render(<CollabPanel panel={makePanel({ switchingViewScope: true })} />);
    expect(screen.getByRole('button', { name: /切换到参与者视角/ })).toBeDisabled();
  });

  it('scope 回显缺失（humanViewScope=null）不渲染切换按钮与视角 Badge', () => {
    render(<CollabPanel panel={makePanel({ humanViewScope: null })} />);
    expect(screen.getByText('在会话中隐身')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /切换到/ })).not.toBeInTheDocument();
    expect(screen.queryByText('完整视角')).not.toBeInTheDocument();
    expect(screen.queryByText('参与者视角')).not.toBeInTheDocument();
  });

  it('bot 视角面板不渲染切换按钮', () => {
    render(<CollabPanel panel={makePanel({ botActorId: 'b:1', botMode: 'auto' })} />);
    expect(screen.queryByRole('button', { name: /切换到/ })).not.toBeInTheDocument();
  });
});
