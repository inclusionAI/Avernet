import type { OpenClawProvider } from '@tc-chat/adapters';
import type { ChatMessage } from '@tc-chat/core';

const FALLBACK_DELAY_MS = 2000;

interface MutableInner {
  _isRequesting?: boolean;
  getParseResult?: () => { content?: string; blocks?: unknown[]; sessionKey?: string };
  currentMessageId?: string;
  transport?: { onMessage?: (msg: unknown) => void };
}

/**
 * SDK OpenClawParser 仅在收 `chat` 事件 `state=final` 时触发 onComplete；
 * 部分 Bot 走 `agent` stream 响应，agent `phase=end` 后不一定跟随 `chat:final`，
 * 导致 Chat.isRequesting 永不归 false、UI 卡在等待动画。
 *
 * 此工具拦截 transport.onMessage 监听 agent `phase=end`，延迟 2s 后若
 * onComplete 仍未触发则手动补发 onComplete 解除 isRequesting。
 */
export function installCompleteFallback(
  inner: OpenClawProvider,
  fireOnComplete: (messages: ChatMessage[]) => void,
): () => void {
  const mutable = inner as unknown as MutableInner;
  const transport = mutable.transport;
  if (!transport?.onMessage) return () => {};

  let timer: ReturnType<typeof setTimeout> | null = null;
  let completed = false;

  const fire = () => {
    timer = null;
    if (completed) return;
    if (!mutable._isRequesting) return;
    const result = mutable.getParseResult?.();
    const msg: ChatMessage = {
      id: mutable.currentMessageId ?? '',
      role: 'assistant',
      content: result?.content ?? '',
      blocks: (result?.blocks ?? []) as ChatMessage['blocks'],
      status: 'done',
      sessionKey: result?.sessionKey,
    };
    completed = true;
    fireOnComplete([msg]);
  };

  const originalOnMessage = transport.onMessage.bind(transport);
  transport.onMessage = (msg: unknown) => {
    originalOnMessage(msg);
    const message = msg as { type?: string; event?: string; payload?: { data?: { phase?: string } } };
    if (message?.type === 'event' && message?.event === 'agent' && message?.payload?.data?.phase === 'end') {
      if (timer) clearTimeout(timer);
      completed = false;
      timer = setTimeout(fire, FALLBACK_DELAY_MS);
    }
  };

  return () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    completed = true;
  };
}
