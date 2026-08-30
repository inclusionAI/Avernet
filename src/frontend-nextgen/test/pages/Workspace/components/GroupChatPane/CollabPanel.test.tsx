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
    botActorId: 'b:1',
    botMode: 'auto',
    botName: 'Alpha',
    human: { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'absent' },
    humanJoined: false,
    humanName: '章梧',
    humanAvatarUrl: undefined,
    canSwitchToHuman: true,
    switchingBotMode: false,
    joining: false,
    setBotMode: jest.fn<any>().mockResolvedValue(undefined),
    joinSession: jest.fn<any>().mockResolvedValue(true),
    leaveSession: jest.fn<any>().mockResolvedValue(true),
    switchToHuman: jest.fn(),
    ...overrides,
  };
}

describe('CollabPanel', () => {
  it('visible=false 时不渲染', () => {
    const { container } = render(<CollabPanel panel={makePanel({ visible: false })} />);
    expect(container.firstChild).toBeNull();
  });

  it('bot 视角默认 Bot 控制 tab:展示自主发言状态与模式切换按钮', () => {
    render(<CollabPanel panel={makePanel()} />);
    expect(screen.getByText('Bot控制')).toBeInTheDocument();
    expect(screen.getByText('用户协作')).toBeInTheDocument();
    expect(screen.getByText('Bot 自主发言中')).toBeInTheDocument();
    expect(screen.getByText('自动模式')).toBeInTheDocument();
  });

  it('切换到禁言模式调用 setBotMode("muted")', () => {
    const panel = makePanel();
    render(<CollabPanel panel={panel} />);
    fireEvent.click(screen.getByRole('button', { name: '切换 Bot 发言模式' }));
    fireEvent.click(screen.getByText('禁言模式'));
    expect(panel.setBotMode).toHaveBeenCalledWith('muted');
  });

  it('用户协作 tab:human 未加入显示「未加入当前会话」与加入按钮', () => {
    render(<CollabPanel panel={makePanel()} />);
    fireEvent.click(screen.getByText('用户协作'));
    expect(screen.getByText('未加入当前会话')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '加入当前会话' })).toBeInTheDocument();
  });

  it('用户协作 tab:human 已加入显示用户名与「去发言」', () => {
    const panel = makePanel({
      human: { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' },
      humanJoined: true,
    });
    render(<CollabPanel panel={panel} />);
    fireEvent.click(screen.getByText('用户协作'));
    expect(screen.getByText('章梧')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去发言' }));
    expect(panel.switchToHuman).toHaveBeenCalled();
  });

  it('human 视角 absent:仅显示加入条,无 tab', () => {
    const panel = makePanel({ humanAbsentOnly: true });
    render(<CollabPanel panel={panel} />);
    expect(screen.getByText('未加入当前会话')).toBeInTheDocument();
    expect(screen.queryByText('Bot控制')).not.toBeInTheDocument();
    // 点击加入 → 二次确认 → 确认后调用 joinSession
    fireEvent.click(screen.getByRole('button', { name: '加入当前会话' }));
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    expect(panel.joinSession).toHaveBeenCalled();
  });
});
