/** @jest-environment jsdom */
// @jest/globals 必须先行：sonner mock factory 会在 ChatPanelHeader 的 require 链中被执行，
// 若它先于 globals 被 require，factory 里的 jest.fn() 是 undefined（hoisting 坑，见项目备忘）。
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { toast } from 'sonner';
import { ChatPanelHeader } from '@/components/Workspace/ChatPanel/ChatPanelHeader';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';

// 只需 ChatLayout.Header 槽位结构（slotLeft/slotRight 分列），stub 掉 SDK 三槽布局。
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { createElement } = require('react');
  const ChatLayout = (props: Record<string, unknown>) => createElement('div', null, props.children);
  ChatLayout.Header = (props: Record<string, unknown>) =>
    createElement(
      'div',
      null,
      createElement('div', null, props.slotLeft),
      createElement('div', null, props.slotRight),
    );
  return { ChatLayout };
});

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const target = {
  id: 'bot-1',
  name: '数字助手',
  avatar: 'B',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '单聊',
} as ConversationTarget;

const sessionCode = 'sess-a1b2c3d4e5f6g7h8';

const clipboardWriteText = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  // jsdom 无 clipboard，注入能力探针用 stub（GroupHeader/MessageInteractions 同口径）。
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: clipboardWriteText },
    configurable: true,
  });
});

afterEach(() => {
  // 释放 navigator 剪贴板桩，避免跨用例泄漏。
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
});

describe('ChatPanelHeader 会话 ID 复制与窄屏让位', () => {
  it('点按会话 ID 胶囊复制到剪贴板并 toast 成功', async () => {
    clipboardWriteText.mockResolvedValue(undefined);
    render(<ChatPanelHeader target={target} sessionTitle="Q3 经营复盘" sessionCode={sessionCode} />);

    fireEvent.click(screen.getByRole('button', { name: `复制会话 ID ${sessionCode}` }));
    await waitFor(() => expect(clipboardWriteText).toHaveBeenCalledWith(sessionCode));
    expect(toast.success).toHaveBeenCalledWith('会话 ID 已复制');
  });

  it('长会话 ID 限宽 180px 且双倍让位（防止与右侧五图标重叠）', () => {
    render(<ChatPanelHeader target={target} sessionTitle="Q3 经营复盘" sessionCode={sessionCode} />);

    const pill = screen.getByRole('button', { name: `复制会话 ID ${sessionCode}` });
    expect(pill).toHaveClass('max-w-[180px]', 'shrink-[2]', 'min-w-0');
    expect(pill.querySelector('span')).toHaveClass('truncate');
    // 标题保底 112px（sm 以上 120px）后才 ellipsis，不被胶囊立即挤没。
    expect(screen.getByRole('heading', { name: 'Q3 经营复盘' })).toHaveClass('min-w-[112px]', 'truncate');
  });

  it('无 sessionCode 时不渲染复制胶囊', () => {
    render(<ChatPanelHeader target={target} />);
    expect(screen.queryByRole('button', { name: /复制会话 ID/ })).not.toBeInTheDocument();
  });

  it('剪贴板不可用（拒绝/缺失）时 toast 失败兜底', async () => {
    clipboardWriteText.mockRejectedValue(new Error('denied'));
    render(<ChatPanelHeader target={target} sessionCode={sessionCode} />);

    fireEvent.click(screen.getByRole('button', { name: `复制会话 ID ${sessionCode}` }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('复制失败，请手动复制。'));
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe('ChatPanelHeader 连接状态弱呈现', () => {
  it('已连接：绿点 + 弱灰文字，不换行不被挤占', () => {
    render(<ChatPanelHeader target={target} connectionLabel="已连接" connectionTone="success" />);

    const label = screen.getByText('已连接');
    expect(label).toHaveClass('shrink-0', 'whitespace-nowrap', 'text-muted-foreground');
    expect(label.querySelector('span')).toHaveClass('bg-success');
  });

  it('重连中/连接失败分别映射警示点/错误点', () => {
    const { rerender } = render(<ChatPanelHeader target={target} connectionLabel="重连中" connectionTone="warning" />);
    expect(screen.getByText('重连中').querySelector('span')).toHaveClass('bg-warning');

    rerender(<ChatPanelHeader target={target} connectionLabel="连接失败" connectionTone="error" />);
    expect(screen.getByText('连接失败').querySelector('span')).toHaveClass('bg-destructive');
  });
});
