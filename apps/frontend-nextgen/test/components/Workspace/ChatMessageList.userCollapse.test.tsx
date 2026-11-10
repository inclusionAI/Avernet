/** @jest-environment jsdom */
// MessageList × 用户消息折叠 —— 集成测试:
// 用真实 SDK Bubble/MessageRenderer(不 mock)渲染完整 renderText 委托链,
// 验证「user 单一 text block」消息经 CollapsibleUserText 出折叠操作行,
// 含图片 block 的 user 消息与回复期间的最新 user 消息不进折叠路径。
// 见 OpenSpec change `add-long-user-message-collapse`(design D2–D4)。
import type { Block, ChatMessage } from '@tc-chat/core';
import React from 'react';
import { defaultCapabilities, extendCapabilities } from '@/capabilities';

// 部分mock:保留真实 Bubble/MessageRenderer/Button 链,桩掉列表壳与重依赖。
// 桩函数都在工厂内 jest.fn(),断言侧 import 回被模模块取同实例。
jest.mock('@/components/ui', () => {
  const React = require('react');
  return {
    Button: ({ children, ...rest }: { children?: React.ReactNode }) =>
      React.createElement('button', { type: 'button', ...rest }, children),
  };
});

// MarkdownRenderer 桩:mock 注册按「解析后路径」(mapper 已把 es/* 映到 lib/*),
// 会同时替换 Bubble/MessageRenderer 内部相对 require 的同一物理模块 —— 必须
// requireActual 展开保持真实导出(plugins/resolveMarkdown 依赖),只把 MarkdownRenderer
// 组件本身换成轻量桩;真 markdown 保真不属本套职责(Bubble→renderText 链仍为真实实现)。
// 注意 MessageRenderer 模块完全不 mock:任何部分替换都会让 Bubble 内部的
// MessageRenderer 解析成 undefined 触发 "Element type is invalid"。
jest.mock('@tc-chat/ui/es/MarkdownRender', () => {
  const React = require('react');
  const actual = jest.requireActual('@tc-chat/ui/es/MarkdownRender');
  return {
    ...actual,
    MarkdownRenderer: ({ content }: { content: string }) =>
      React.createElement('div', { 'data-testid': 'md-stub' }, content),
  };
});

jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const React = require('react');
  return {
    ChatLayout: {
      List: ({
        messages,
        renderItem,
        emptyPlaceholder,
        children,
      }: {
        messages: ChatMessage[];
        renderItem: (message: ChatMessage, index: number) => React.ReactNode;
        emptyPlaceholder?: React.ReactNode;
        children?: React.ReactNode;
      }) =>
        React.createElement(
          'div',
          null,
          messages.length === 0 && emptyPlaceholder ? emptyPlaceholder : null,
          ...messages.map((message, index) =>
            React.createElement('div', { key: message.id }, renderItem(message, index)),
          ),
          children,
        ),
    },
  };
});

jest.mock('@/components/Workspace/ChatPanel/ChatMessageSkeleton', () => ({
  ChatMessageSkeleton: () => null,
}));
jest.mock('@/components/Workspace/MessageInteractionToolbar', () => ({
  MessageCopyAction: ({ onCopy, onEdit }: { onCopy: () => void; onEdit: () => void }) => {
    return <div>
      <button type="button" onClick={onCopy}>copy</button>
      <button type="button" onClick={onEdit}>edit</button>
    </div>;
  },
  MessageInteractionToolbar: () => null,
  MessageSelectionToolbar: () => null,
}));
jest.mock('@/components/Workspace/MessageSenderMeta', () => {
  const React = require('react');
  return {
    MessageSenderLayout: ({ children }: { children?: React.ReactNode }) =>
      React.createElement('div', null, children),
    MessageSenderMeta: () => null,
  };
});
jest.mock('@/pages/Workspace/hooks/useHistoryPrependAnchor', () => ({
  useHistoryPrependAnchor: (_messages: unknown, _ref: unknown, onLoadMoreHistory?: () => void) =>
    onLoadMoreHistory,
}));
jest.mock('@/pages/Workspace/hooks/useStickToBottom', () => ({
  useStickToBottom: () => undefined,
}));

import { fireEvent, render, screen } from '@testing-library/react';
import { ChatMessageList } from '@/components/Workspace/ChatPanel/ChatMessageList';

const LONG_MSG =
  '超长参数消息\n' +
  Array.from({ length: 120 }, (_, i) => `第${i + 1}行 配置=值${i}`).join('\n');

function buildInteractions() {
  return {
    rootRef: { current: null },
    selection: null,
    unreadCount: 0,
    copyText: jest.fn(),
    markRead: jest.fn(),
    setSelection: jest.fn(),
    setAtBottom: jest.fn(),
    quoteMessage: jest.fn(),
    clearQuote: jest.fn(),
  };
}

function renderList(messages: ChatMessage[], isRequesting = false) {
  const interactions = buildInteractions();
  const edit = jest.fn();
  render(
    <ChatMessageList
      messages={messages}
      isRequesting={isRequesting}
      isLoadingMessages={false}
      interactions={interactions as never}
      onStop={jest.fn()}
      onEditMessage={edit}
      onQuoteSelected={jest.fn()}
      onExplainSelected={jest.fn()}
      resolveSender={() => ({ name: '我', avatar: <span /> })}
      getMessageTime={() => undefined}
      getMessageBlocks={(message: ChatMessage) => (message.blocks ?? []) as Block[]}
    />,
  );
  return { interactions, edit };
}

/** jsdom 无真实布局:把量高节点的 scrollHeight 垫到阈值以上。 */
function mockScrollHeight(px: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get: () => px,
  });
}

describe('ChatMessageList × 用户消息折叠集成', () => {
  afterEach(() => {
    extendCapabilities({ getUserMessagePresentation: defaultCapabilities.getUserMessagePresentation });
    delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight;
  });

  it('user 纯文本超阈值消息 → 出现「展开全部」操作行(真实 SDK Bubble·renderText 委托链)', () => {
    mockScrollHeight(2600);
    renderList([
      {
        id: 'u1',
        role: 'user',
        content: LONG_MSG,
        blocks: [{ type: 'text', content: LONG_MSG }],
      } as unknown as ChatMessage,
    ]);
    expect(screen.getByText(/展开全部 · 共 \d+ 行/)).toBeTruthy();
  });

  it('回复进行中的最新 user 消息 → forceExpanded 延迟折叠,零折叠 UI', () => {
    mockScrollHeight(2600);
    renderList(
      [
        {
          id: 'u1',
          role: 'user',
          content: LONG_MSG,
          blocks: [{ type: 'text', content: LONG_MSG }],
        } as unknown as ChatMessage,
      ],
      true,
    );
    expect(screen.queryByText(/展开全部/)).toBeNull();
  });

  it('user 消息含图片 block → 不进折叠路径,零折叠 UI', () => {
    mockScrollHeight(2600);
    renderList([
      {
        id: 'u2',
        role: 'user',
        content: LONG_MSG,
        blocks: [
          { type: 'image', data: 'data:image/png;base64,x', name: '图.png', mimeType: 'image/png' },
          { type: 'text', content: LONG_MSG },
        ],
      } as unknown as ChatMessage,
    ]);
    expect(screen.queryByText(/展开全部/)).toBeNull();
  });

  it('超阈值 assistant 消息不受影响 → 零折叠 UI(折叠仅限 user 分支)', () => {
    mockScrollHeight(2600);
    renderList([
      {
        id: 'a1',
        role: 'assistant',
        content: LONG_MSG,
        blocks: [{ type: 'text', content: LONG_MSG }],
      } as unknown as ChatMessage,
    ]);
    expect(screen.queryByText(/展开全部/)).toBeNull();
  });
});


describe('user reference presentation through the real SDK Bubble', () => {
  afterEach(() => extendCapabilities({ getUserMessagePresentation: defaultCapabilities.getUserMessagePresentation }));
  it.each([false, true])('presents single/multimodal user text, preserving copy/edit originals (multimodal=%s)', (multimodal) => {
    const raw = 'reference template\nquestion';
    extendCapabilities({ getUserMessagePresentation: () => ({ status: 'available', value: () => ({
      content: 'question', header: <span>reference card</span>,
    }) }) });
    const message = { id: 'presented', role: 'user', content: raw, blocks: [
      ...(multimodal ? [{ type: 'image', data: 'data:image/png;base64,x', name: '图.png', mimeType: 'image/png' }] : []),
      { type: 'text', content: raw },
    ] } as unknown as ChatMessage;
    const { interactions, edit } = renderList([message]);
    expect(screen.getByText('reference card')).toBeTruthy();
    expect(screen.getByText('question')).toBeTruthy();
    expect(screen.queryByText(raw)).toBeNull();
    fireEvent.click(screen.getByText('copy'));
    expect(interactions.copyText).toHaveBeenCalledWith(raw);
    fireEvent.click(screen.getByText('edit'));
    expect(edit).toHaveBeenCalledWith(message);
    expect(message.content).toBe(raw);
  });
  it('never projects assistant text', () => {
    const presenter = jest.fn(() => ({ content: 'wrong', header: <span>wrong</span> }));
    extendCapabilities({ getUserMessagePresentation: () => ({ status: 'available', value: presenter }) });
    renderList([{ id: 'assistant', role: 'assistant', content: 'answer', blocks: [{ type: 'text', content: 'answer' }] } as unknown as ChatMessage]);
    expect(presenter).not.toHaveBeenCalled();
    expect(screen.getByText('answer')).toBeTruthy();
  });
});
