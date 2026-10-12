/** @jest-environment jsdom */
import type { DeliveryStatusView } from '@/domain/collaboration/types';
import {
  collectActiveBotRuns,
  GroupChatActiveRuns,
} from '@/pages/Workspace/components/GroupChatPane/GroupChatActiveRuns';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

Element.prototype.scrollIntoView ??= () => undefined;
Element.prototype.hasPointerCapture ??= () => false;

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

describe('GroupChatActiveRuns — 按 Bot 合并的活动/队列模块', () => {
  const delivery = (botId: string, id: string, overrides: Partial<DeliveryStatusView> = {}): DeliveryStatusView => ({
    delivery_id: id,
    message_id: `m-${id}`,
    target_bot_id: botId,
    flow_kind: 'group',
    kind: 'send',
    status: 'queued',
    state_version: 1,
    run_id: null,
    wait_reason: 'prior_message_running',
    admission_error: null,
    content_preview: `内容-${id}`,
    ...overrides,
  });

  it('活跃+排队同 bot：单芯片同时含终止按钮与排队徽标，popover 只含该 bot 的排队消息', async () => {
    render(
      <GroupChatActiveRuns
        messages={messages}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={jest.fn()}
        queuedDeliveries={[delivery('bot-a', 'd1'), delivery('bot-b', 'd2')]}
        processingDeliveries={[delivery('bot-a', 'p1', { status: 'running' })]}
        cancellingDeliveryIds={new Set()}
        onCancelDelivery={jest.fn()}
      />,
    );

    // 全局聚合排队芯片不再存在
    expect(screen.queryByText(/\d+ 条排队中/)).not.toBeInTheDocument();

    // bot-a 芯片：终止按钮 + 排队徽标同在一个模块
    const chipA = screen.getByTestId('active-bot-run-bot-a');
    expect(chipA).toHaveTextContent('甲');
    expect(chipA).toHaveTextContent('1 条排队');
    expect(screen.getByRole('button', { name: '终止 甲 的输出' })).toBeInTheDocument();

    // 打开 bot-a 的 popover：处理中摘要 + 只含 bot-a 的排队消息
    fireEvent.click(screen.getByTestId('bot-queue-trigger-bot-a'));
    await waitFor(() => expect(screen.getByText('内容-d1')).toBeInTheDocument());
    expect(screen.getByText('1 条处理中')).toBeInTheDocument();
    expect(screen.getByText('等待前一条消息完成')).toBeInTheDocument();
    expect(screen.queryByText('内容-d2')).not.toBeInTheDocument();
  });

  it('终止按钮点击不冒泡（不展开 popover），仍触发 onAbortBot', async () => {
    const onAbortBot = jest.fn<() => Promise<void>>().mockResolvedValue(undefined);
    render(
      <GroupChatActiveRuns
        messages={messages}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={onAbortBot}
        queuedDeliveries={[delivery('bot-a', 'd1')]}
        cancellingDeliveryIds={new Set()}
        onCancelDelivery={jest.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '终止 甲 的输出' }));

    await waitFor(() => expect(onAbortBot).toHaveBeenCalledWith('bot-a'));
    expect(screen.queryByText('内容-d1')).not.toBeInTheDocument();
  });

  it('仅排队（无活跃输出）的 bot：芯片无终止按钮、有排队徽标，可展开取消', async () => {
    const onCancelDelivery = jest.fn<() => Promise<void>>().mockResolvedValue(undefined);
    render(
      <GroupChatActiveRuns
        messages={[]}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={jest.fn()}
        queuedDeliveries={[delivery('bot-c', 'd9')]}
        cancellingDeliveryIds={new Set()}
        onCancelDelivery={onCancelDelivery}
      />,
    );

    expect(screen.queryByRole('button', { name: /终止/ })).not.toBeInTheDocument();
    const trigger = screen.getByTestId('bot-queue-trigger-bot-c');
    expect(trigger).toHaveTextContent('Bot');
    expect(trigger).toHaveTextContent('1 条排队');

    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByText('内容-d9')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '取消发往 Bot 的排队消息' }));
    await waitFor(() => expect(onCancelDelivery).toHaveBeenCalledWith('m-d9', 'd9'));
  });
});
