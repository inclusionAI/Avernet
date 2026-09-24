/** @jest-environment jsdom */
import {
  collectActiveBotRuns,
  GroupChatActiveRuns,
} from '@/pages/Workspace/components/GroupChatPane/GroupChatActiveRuns';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const participants = [
  { actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' },
  { actorId: 'bot-b', kind: 'bot', name: '乙', role: 'member', mode: 'auto' },
] as never;

const messages = [
  {
    id: 'm1',
    role: 'assistant',
    content: 'one',
    status: 'streaming',
    createdAt: 1_000,
    extra: { botUuid: 'bot-a', runId: 'run-1' },
  },
  {
    id: 'm2',
    role: 'assistant',
    content: 'two',
    status: 'streaming',
    createdAt: 2_000,
    extra: { botUuid: 'bot-a', runId: 'run-2' },
  },
  {
    id: 'm3',
    role: 'assistant',
    content: 'three',
    status: 'streaming',
    createdAt: 3_000,
    extra: { botUuid: 'bot-b', runId: 'run-3' },
  },
] as never;

describe('GroupChatActiveRuns', () => {
  it('groups multiple runs by Bot in first-active-message order', () => {
    expect(collectActiveBotRuns(messages, participants)).toEqual([
      { botId: 'bot-a', botName: '甲', runCount: 2, startedAt: 1_000 },
      { botId: 'bot-b', botName: '乙', runCount: 1, startedAt: 3_000 },
    ]);
  });

  it('renders ordered borderless actions and aborts each Bot independently', async () => {
    const onAbortBot = jest.fn<() => Promise<void>>().mockResolvedValue(undefined);
    const { rerender, unmount } = render(
      <GroupChatActiveRuns
        messages={messages}
        participants={participants}
        abortingBotIds={new Set(['bot-a'])}
        onAbortBot={onAbortBot}
      />,
    );

    const strip = screen.getByLabelText('正在输出的 Bot');
    expect(strip).toHaveClass('overflow-x-auto');
    const buttons = screen.getAllByRole('button');
    expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual([
      '正在终止 甲 的输出',
      '终止 乙 的输出',
    ]);
    expect(screen.getByTestId('active-bot-run-bot-a')).toHaveClass('gap-3', 'rounded-lg', 'border-border');
    expect(screen.getByTestId('active-bot-run-bot-a')).not.toHaveClass('w-64', 'justify-between');
    expect(buttons[0]).toHaveClass('border-transparent', 'bg-transparent', 'hover:bg-destructive/10');
    expect(screen.getByText('甲')).toHaveClass('text-foreground');
    expect(screen.getByText('2 个输出中')).toBeInTheDocument();
    expect(screen.getByText('终止中…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '正在终止 甲 的输出' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '终止 乙 的输出' }));
    await waitFor(() => expect(onAbortBot).toHaveBeenCalledWith('bot-b'));
    expect(onAbortBot).not.toHaveBeenCalledWith('bot-a');

    rerender(
      <GroupChatActiveRuns
        messages={
          [
            {
              id: 'm1',
              role: 'assistant',
              content: 'one',
              status: 'done',
              extra: { botUuid: 'bot-a', runId: 'run-1' },
            },
            {
              id: 'm2',
              role: 'assistant',
              content: 'two',
              status: 'done',
              extra: { botUuid: 'bot-a', runId: 'run-2' },
            },
            {
              id: 'm3',
              role: 'assistant',
              content: 'three',
              status: 'streaming',
              extra: { botUuid: 'bot-b', runId: 'run-3' },
            },
          ] as never
        }
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={onAbortBot}
      />,
    );
    expect(screen.queryByTestId('active-bot-run-bot-a')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '终止 乙 的输出' })).toBeInTheDocument();
    unmount();
  });

  it('does not reserve space when no Bot is streaming', () => {
    const { container } = render(
      <GroupChatActiveRuns
        messages={
          [
            {
              id: 'm-done',
              role: 'assistant',
              content: 'done',
              status: 'done',
              extra: { botUuid: 'bot-a', runId: 'run-done' },
            },
          ] as never
        }
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={jest.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
