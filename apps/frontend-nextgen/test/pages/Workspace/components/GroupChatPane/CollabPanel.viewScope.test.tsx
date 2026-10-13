/** @jest-environment jsdom */
import { ComposerStatusBar } from '@/pages/Workspace/components/GroupChatPane/ComposerStatusBar';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

function makeProps(overrides: Record<string, unknown> = {}) {
  return {
    connectionStatus: 'connected' as const,
    humanName: '章梧',
    viewScope: 'full' as const,
    switchingViewScope: false,
    onViewScopeChange: jest.fn(),
    onLeave: jest.fn().mockResolvedValue(undefined),
    onReconnect: jest.fn(),
    ...overrides,
  };
}

/** 视角切换钮随发言模式条迁入 ComposerStatusBar 帽条（design 决策 7/8；CollabPanel 不再渲染该行）。 */
describe('帽条视角切换与退出（ComposerStatusBar）', () => {
  it('连接正常渲染「用户发言模式」与「退出会话」，点击退出调用 leaveSession', () => {
    const onLeave = jest.fn().mockResolvedValue(undefined);
    render(<ComposerStatusBar {...makeProps({ onLeave })} />);
    expect(screen.getByText('用户发言模式')).toBeInTheDocument();
    expect(screen.getByText('当前将以用户身份发送到会话')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出会话' }));
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it('回显 full：渲染「切换到参与者视角」钮，点击切到 participant', () => {
    const onViewScopeChange = jest.fn();
    render(<ComposerStatusBar {...makeProps({ onViewScopeChange })} />);
    const btn = screen.getByRole('button', { name: /切换到参与者视角/ });
    fireEvent.click(btn);
    expect(onViewScopeChange).toHaveBeenCalledWith('participant');
  });

  it('回显 participant：渲染「切换到完整视角」钮，点击切回 full', () => {
    const onViewScopeChange = jest.fn();
    render(<ComposerStatusBar {...makeProps({ viewScope: 'participant', onViewScopeChange })} />);
    fireEvent.click(screen.getByRole('button', { name: /切换到完整视角/ }));
    expect(onViewScopeChange).toHaveBeenCalledWith('full');
  });

  it('switchingViewScope 进行中切换按钮禁用', () => {
    render(<ComposerStatusBar {...makeProps({ switchingViewScope: true })} />);
    expect(screen.getByRole('button', { name: /切换到参与者视角/ })).toBeDisabled();
  });

  it('scope 回显缺失（viewScope=null）不渲染切换按钮，退出会话保留', () => {
    render(<ComposerStatusBar {...makeProps({ viewScope: null })} />);
    expect(screen.getByRole('button', { name: '退出会话' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /切换到/ })).not.toBeInTheDocument();
  });

  it('连接失败：左侧变为状态名+说明，退出会话之左出现「重新连接」', () => {
    const onReconnect = jest.fn();
    render(<ComposerStatusBar {...makeProps({ connectionStatus: 'error', onReconnect })} />);
    expect(screen.getByText('连接失败')).toBeInTheDocument();
    expect(screen.getByText('暂时无法连接，点击「重新连接」重试')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重新连接/ }));
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it('断开态：状态文案呈现，右侧组顺序 重新连接 → 退出会话', () => {
    render(<ComposerStatusBar {...makeProps({ connectionStatus: 'disconnected' })} />);
    expect(screen.getByText('已断开')).toBeInTheDocument();
    expect(screen.getByText('会话连接已断开，请重新连接后继续对话')).toBeInTheDocument();
    const reconnect = screen.getByRole('button', { name: /重新连接/ });
    const leave = screen.getByRole('button', { name: '退出会话' });
    expect(reconnect.compareDocumentPosition(leave) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('连接中/重连中不出现「重新连接」按钮（不可人工干预）', () => {
    const { rerender } = render(<ComposerStatusBar {...makeProps({ connectionStatus: 'connecting' })} />);
    expect(screen.getByText('连接中')).toBeInTheDocument();
    expect(screen.getByText('正在连接并加载会话与消息，请稍候…')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重新连接/ })).not.toBeInTheDocument();
    rerender(<ComposerStatusBar {...makeProps({ connectionStatus: 'reconnecting' })} />);
    expect(screen.getByText('重连中')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重新连接/ })).not.toBeInTheDocument();
  });
});