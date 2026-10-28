/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { ConversationComposerProps } from '@/capabilities/conversationComposer';
import { ConversationComposerHost } from '@/components/Workspace/ConversationComposerHost';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { useState } from 'react';
const bot = { realBotId: 'b', botId: 'b:owner', ownerId: 'owner', botType: 'personal' } as ChatBotView;
const session = { sessionId: 's', botId: bot.botId } as BotChatSessionView;
const send = jest.fn();
afterEach(() => extendCapabilities({ getConversationComposer: defaultCapabilities.getConversationComposer }));
it('public default passes commands and sending through without an extension', () => {
  const command = { categories: [] };
  const child = jest.fn((value) => (
    <button type="button" onClick={() => value.onSend('original')}>
      send
    </button>
  ));
  render(
    <ConversationComposerHost bot={bot} session={session} command={command} onSend={send}>
      {child}
    </ConversationComposerHost>,
  );
  expect(child.mock.calls[0][0]).toEqual({ command, onSend: send });
});
it('keys extension state by bot, owner, session and runtime context', () => {
  function Extension({ children, context, ...value }: ConversationComposerProps) {
    const [initial] = useState(context.sessionId);
    return (
      <>
        {children(value)}
        <span>
          {context.botId}/{context.ownerId}/{initial}
        </span>
      </>
    );
  }
  extendCapabilities({ getConversationComposer: () => ({ status: 'available', value: Extension }) });
  const child = () => <span>normal input</span>;
  const { rerender } = render(
    <ConversationComposerHost bot={bot} session={session} onSend={send}>
      {child}
    </ConversationComposerHost>,
  );
  expect(screen.getByText('b/owner/s')).toBeVisible();
  rerender(
    <ConversationComposerHost
      bot={{ ...bot, ownerId: 'other' }}
      session={{ ...session, sessionId: 'new' }}
      onSend={send}
    >
      {child}
    </ConversationComposerHost>,
  );
  expect(screen.getByText('b/other/new')).toBeVisible();
});
it.each([null, { ...bot, ownerId: undefined }, { ...bot, botType: 'desktop' }])(
  'does not guess identity or decorate unsupported target %j',
  (target) => {
    const Extension = jest.fn(() => <span>extension</span>);
    extendCapabilities({ getConversationComposer: () => ({ status: 'available', value: Extension }) });
    render(
      <ConversationComposerHost bot={target} session={session} onSend={send}>
        {() => <span>normal input</span>}
      </ConversationComposerHost>,
    );
    expect(Extension).not.toHaveBeenCalled();
    expect(screen.getByText('normal input')).toBeVisible();
  },
);
