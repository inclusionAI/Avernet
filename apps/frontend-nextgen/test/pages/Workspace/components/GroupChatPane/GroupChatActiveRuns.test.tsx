/** @jest-environment jsdom */
import type { DeliveryStatusView } from '@/domain/collaboration/types';
import {
  collectActiveBotRuns,
  GroupChatActiveRuns,
} from '@/pages/Workspace/components/GroupChatPane/GroupChatActiveRuns';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

Element.prototype.scrollIntoView ??= () => undefined;
Element.prototype.hasPointerCapture ??= () => false;

const botIds = ['a', 'b', 'c', 'd', 'e'];
const names = ['甲', '乙', '丙', '丁', '戊'];
const participants = botIds.map((id, index) => ({
  actorId: `bot-${id}`,
  kind: 'bot',
  name: names[index],
  role: 'member',
  mode: 'auto',
})) as never;
const messages = botIds.map((id, index) => ({
  id: `reply-${id}`,
  role: 'assistant',
  content: id,
  status: 'streaming',
  createdAt: index + 1,
  extra: { botUuid: `bot-${id}`, runId: `run-${id}` },
}));
const delivery = (id: string, overrides: Partial<DeliveryStatusView> = {}): DeliveryStatusView => ({
  delivery_id: `delivery-${id}`,
  message_id: `message-${id}`,
  target_bot_id: `bot-${id}`,
  flow_kind: 'group',
  kind: 'send',
  status: 'queued',
  state_version: 1,
  run_id: null,
  wait_reason: 'prior_message_running',
  admission_error: null,
  content_preview: `排队内容-${id}`,
  ...overrides,
});
const onAbortBot = () => jest.fn<(botId: string) => Promise<void>>().mockResolvedValue(undefined);
const onCancelDelivery = () =>
  jest.fn<(messageId: string, deliveryId: string) => Promise<void>>().mockResolvedValue(undefined);
const openList = () => fireEvent.click(screen.getByRole('button', { name: '查看处理列表' }));

describe('GroupChatActiveRuns', () => {
  it('deduplicates streaming records by Bot without counting them as multiple Bots', () => {
    expect(
      collectActiveBotRuns(
        [messages[0], { ...messages[0], id: 'another-segment', createdAt: 9 }] as never,
        participants,
      ),
    ).toEqual([{ botId: 'bot-a', botName: '甲', runCount: 2, startedAt: 1 }]);
  });

  it.each([1, 2, 3, 5])('shows queues for %i active Bots without exposing stop actions until opened', async (count) => {
    const abort = onAbortBot();
    render(
      <GroupChatActiveRuns
        messages={messages.slice(0, count) as never}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={abort}
        queuedDeliveries={botIds.slice(0, count).map((id) => delivery(id))}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(`${count} 个 Bot 处理中 · ${count} 项待处理`);
    expect(screen.queryByLabelText('群聊处理列表')).not.toBeInTheDocument();
    openList();
    await screen.findByLabelText('群聊处理列表');
    expect(screen.getAllByRole('button', { name: /^停止 .+ 的当前回复$/ })).toHaveLength(count);
    expect(abort).not.toHaveBeenCalled();
    const visibleQueues = count < 3 ? count : 1;
    expect(screen.getAllByRole('list', { name: /的排队消息$/ })).toHaveLength(visibleQueues);
    expect(screen.queryByText('等待前一条消息完成')).not.toBeInTheDocument();
    expect(screen.queryByText('输出中')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收起处理列表' }));
    await waitFor(() => expect(screen.queryByLabelText('群聊处理列表')).not.toBeInTheDocument());
  });

  it('stops only the chosen Bot and disables an already pending stop', async () => {
    const abort = onAbortBot();
    render(
      <GroupChatActiveRuns
        messages={messages.slice(0, 2) as never}
        participants={participants}
        abortingBotIds={new Set(['bot-a'])}
        onAbortBot={abort}
        queuedDeliveries={[delivery('a'), delivery('b')]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '选择要停止回复的 Bot' }));
    expect(await screen.findByRole('button', { name: '正在停止 甲 的当前回复' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '停止 乙 的当前回复' }));
    expect(abort).toHaveBeenCalledTimes(1);
    expect(abort).toHaveBeenCalledWith('bot-b');
    expect(screen.getByText('排队内容-a')).toBeInTheDocument();
    expect(screen.getByText('排队内容-b')).toBeInTheDocument();
  });

  it('cancels an exact delivery without affecting another recipient of the same message', async () => {
    const cancel = onCancelDelivery(),
      abort = onAbortBot();
    render(
      <GroupChatActiveRuns
        messages={messages.slice(0, 2) as never}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={abort}
        queuedDeliveries={[
          delivery('a', { message_id: 'shared-message' }),
          delivery('b', { message_id: 'shared-message' }),
        ]}
        cancellingDeliveryIds={new Set(['delivery-b'])}
        onCancelDelivery={cancel}
      />,
    );
    openList();
    const queue = await screen.findByRole('list', { name: '甲 的排队消息' });
    fireEvent.click(within(queue).getByRole('button', { name: /取消发往 甲/ }));
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledWith('shared-message', 'delivery-a');
    expect(screen.getByRole('button', { name: /取消发往 乙/ })).toBeDisabled();
    expect(abort).not.toHaveBeenCalled();
    expect(screen.getByText('排队内容-a')).toBeInTheDocument();
  });

  it('switches the selected queue and falls back when that Bot leaves the activity list', async () => {
    const props = {
      participants,
      abortingBotIds: new Set<string>(),
      onAbortBot: onAbortBot(),
      onCancelDelivery: onCancelDelivery(),
    };
    const { rerender } = render(
      <GroupChatActiveRuns
        {...props}
        messages={messages as never}
        queuedDeliveries={botIds.map((id) => delivery(id))}
      />,
    );
    openList();
    fireEvent.click(await screen.findByRole('button', { name: '查看 戊 的排队消息' }));
    expect(screen.getByRole('button', { name: '查看 戊 的排队消息' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('排队内容-e')).toBeInTheDocument();
    expect(screen.queryByText('排队内容-a')).not.toBeInTheDocument();
    rerender(
      <GroupChatActiveRuns
        {...props}
        messages={messages.slice(0, 4) as never}
        queuedDeliveries={botIds.slice(0, 4).map((id) => delivery(id))}
      />,
    );
    expect(screen.getByRole('button', { name: '查看 甲 的排队消息' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('排队内容-a')).toBeInTheDocument();
    expect(screen.queryByText('排队内容-e')).not.toBeInTheDocument();
  });

  it('counts processing and streaming once, and can stop a processing Bot before its message arrives', async () => {
    const abort = onAbortBot();
    render(
      <GroupChatActiveRuns
        messages={messages.slice(0, 1) as never}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={abort}
        processingDeliveries={[delivery('a', { status: 'running' }), delivery('b', { status: 'running' })]}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('2 个 Bot 处理中 · 0 项待处理');
    openList();
    fireEvent.click(await screen.findByRole('button', { name: '停止 乙 的当前回复' }));
    expect(abort).toHaveBeenCalledWith('bot-b');
  });

  it('keeps a queued-only Bot cancellable without offering stop, with a safe preview fallback', async () => {
    const cancel = onCancelDelivery();
    render(
      <GroupChatActiveRuns
        messages={[]}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={onAbortBot()}
        queuedDeliveries={[delivery('unknown', { content_preview: null })]}
        onCancelDelivery={cancel}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('1 项待处理');
    expect(screen.queryByRole('button', { name: '选择要停止回复的 Bot' })).not.toBeInTheDocument();
    openList();
    fireEvent.click(await screen.findByRole('button', { name: '取消发往 Bot 的排队消息：消息内容暂不可用' }));
    expect(cancel).toHaveBeenCalledWith('message-unknown', 'delivery-unknown');
    expect(screen.queryByRole('img', { name: '正在回复' })).not.toBeInTheDocument();
  });

  it('does not reserve space when no Bot is active', () => {
    const { container } = render(
      <GroupChatActiveRuns
        messages={[]}
        participants={participants}
        abortingBotIds={new Set()}
        onAbortBot={onAbortBot()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('closes an emptied activity panel instead of reopening it for the next reply', async () => {
    const props = { participants, abortingBotIds: new Set<string>(), onAbortBot: onAbortBot() };
    const { rerender, container } = render(<GroupChatActiveRuns {...props} messages={messages.slice(0, 1) as never} />);
    openList();
    await screen.findByLabelText('群聊处理列表');
    rerender(<GroupChatActiveRuns {...props} messages={[]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<GroupChatActiveRuns {...props} messages={messages.slice(0, 1) as never} />);
    expect(screen.queryByLabelText('群聊处理列表')).not.toBeInTheDocument();
  });
});
