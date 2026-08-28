/** @jest-environment jsdom */
import { ChatPanel } from '@/components/Workspace/ChatPanel/index';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { describe, expect, it, jest } from '@jest/globals';
import type { ChatBridge } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import React from 'react';

// 捕获 <ChatLayout.Panel> 收到的 props（断言 bridge 透传）。mock 前缀通过 jest.factory 检查。
let mockPanelRenders: Array<Record<string, unknown>> = [];

// Stub SDK UI primitives: tests focus on ChatPanel header/connection copy + Sender presence,
// not on SDK bubble/markdown rendering. Stubs avoid pulling ESM @tc-chat/ui into jsdom.
jest.mock('@tc-chat/ui/es/Bubble', () => ({ Bubble: () => null }));
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const ChatLayout = ({ children, className }: { children?: React.ReactNode; className?: string }) => (
    <div data-testid="chat-layout" className={className}>
      {children}
    </div>
  );
  const Header = ({ slotLeft, slotRight }: { slotLeft?: React.ReactNode; slotRight?: React.ReactNode }) => (
    <div>
      <div>{slotLeft}</div>
      <div>{slotRight}</div>
    </div>
  );
  const List = ({ emptyPlaceholder }: { emptyPlaceholder?: React.ReactNode }) => <div>{emptyPlaceholder}</div>;
  const Sender = (props: { placeholder?: string; disabled?: boolean }) => (
    <input data-testid="sender" placeholder={props.placeholder} disabled={props.disabled} readOnly />
  );
  const Panel = (props: Record<string, unknown>) => {
    mockPanelRenders.push(props);
    return null;
  };
  ChatLayout.Header = Header;
  ChatLayout.List = List;
  ChatLayout.Sender = Sender;
  ChatLayout.Panel = Panel;
  return { ChatLayout };
});
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  aixUiPlugin: () => ({}),
  fileRefPlugin: () => ({}),
}));
jest.mock('@tc-chat/ui/es/Sender', () => {
  // ChatPanel 单聊输入框用原生 <Sender ref={senderRef}>（forwardRef）替代 <ChatLayout.Sender>（根因 5）。
  // 桩渲染 testid="sender" 供存在性断言；经 require('react').forwardRef 接 ref 不告警（factory 不可引用外层 React）。
  const { forwardRef } = require('react');
  return {
    Sender: forwardRef((props: { placeholder?: string; disabled?: boolean }) => (
      <input data-testid="sender" placeholder={props.placeholder} disabled={props.disabled} readOnly />
    )),
    ToolbarButton: () => null,
  };
});
jest.mock('@tc-chat/ui/es/SystemNotice', () => ({
  SystemNotice: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const demoTarget: ConversationTarget = {
  id: 'octopus',
  name: '虾摸鱼',
  avatar: '🐙',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '',
};
const botModeTarget = {
  id: 'bot-1',
  name: 'Bot',
  avatar: 'B',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '单聊',
};

const baseProps = {
  draft: '',
  panelRef: { current: null } as React.RefObject<never>,
  onDraftChange: () => {},
  onOpenPanelDemo: () => {},
  onPanelAction: () => {},
};

describe('ChatPanel interactive', () => {
  it('demo 模式 Sender 禁用', () => {
    render(
      <ChatPanel
        target={demoTarget}
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        connectionStatus="disconnected"
        retryCount={0}
        supportState={{ phase: 'idle', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    expect(screen.getByTestId('sender')).toBeInTheDocument();
  });
  it('bot 模式(交互) connectionStatus=connected 文案为在线', () => {
    render(
      <ChatPanel
        target={botModeTarget as any}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    expect(screen.getByText('在线')).toBeInTheDocument();
  });
  it('把 chatBridge 透传给 ChatLayout.Panel（主→副通道接线）', () => {
    mockPanelRenders.length = 0;
    const bridge = {} as unknown as ChatBridge;
    render(
      <ChatPanel
        target={demoTarget}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        chatBridge={bridge}
        {...baseProps}
      />,
    );
    expect(mockPanelRenders.some((p) => p.bridge === bridge)).toBe(true);
  });
});
