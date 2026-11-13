import { broadcastAuthReturned, listenAuthReturned } from '@/utils/reauthBroadcast';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

/**
 * 重登回流广播接缝(ace-gateway-popup-reauth D4 快路径):SPA boot 无条件广播,等待重登的原页
 * 受理后立即探活。BroadcastChannel 在 jsdom 下不存在,以最小 stub 覆盖特性分支;不存在时全部静默 no-op。
 */

interface ChannelMessageEvent {
  data: unknown;
}

class StubBroadcastChannel {
  static instances: StubBroadcastChannel[] = [];
  onmessage: ((event: ChannelMessageEvent) => void) | null = null;
  posted: unknown[] = [];
  closed = false;

  constructor(public name: string) {
    StubBroadcastChannel.instances.push(this);
  }

  postMessage(data: unknown): void {
    this.posted.push(data);
  }

  close(): void {
    this.closed = true;
  }
}

const originalBroadcastChannel = (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel;

beforeEach(() => {
  StubBroadcastChannel.instances = [];
  (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel = StubBroadcastChannel;
});

afterEach(() => {
  (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel = originalBroadcastChannel;
});

describe('reauthBroadcast(BroadcastChannel 可用)', () => {
  it('broadcastAuthReturned 在专用频道广播启动消息并关闭频道', () => {
    broadcastAuthReturned();

    expect(StubBroadcastChannel.instances).toHaveLength(1);
    const [channel] = StubBroadcastChannel.instances;
    expect(channel.name).toBe('teamclaw-reauth');
    expect(channel.posted).toEqual(['auth-returned']);
    expect(channel.closed).toBe(true);
  });

  it('listenAuthReturned 收到启动消息触发 handler,退订后关闭频道', () => {
    const handler = jest.fn();
    const unsubscribe = listenAuthReturned(handler);

    expect(StubBroadcastChannel.instances).toHaveLength(1);
    const [channel] = StubBroadcastChannel.instances;

    channel.onmessage?.({ data: 'auth-returned' });
    expect(handler).toHaveBeenCalledTimes(1);

    // 非目标消息不触发。
    channel.onmessage?.({ data: 'something-else' });
    expect(handler).toHaveBeenCalledTimes(1);

    unsubscribe();
    expect(channel.closed).toBe(true);
  });
});

describe('reauthBroadcast(BroadcastChannel 不可用降级)', () => {
  it('广播与监听均静默 no-op,不影响主流程(轮询主干兜底)', () => {
    (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel = undefined;

    expect(() => broadcastAuthReturned()).not.toThrow();
    const unsubscribe = listenAuthReturned(() => {});
    expect(() => unsubscribe()).not.toThrow();
    expect(StubBroadcastChannel.instances).toHaveLength(0);
  });
});
