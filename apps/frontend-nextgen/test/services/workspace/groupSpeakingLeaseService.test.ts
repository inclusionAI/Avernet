/**
 * 发言权 service 契约测试:帧形状(与 bcs-ws wire 契约对齐)、req-id 路由、
 * payload 归一化(后端 expiresAt_ms 为刻意的混合拼写)、协议六态 → UI 四态映射、
 * 发布/释放闭环、chat.send 注入参数的临期判定。
 */
import {
  LEASE_COMMAND_TIMEOUT_MS,
  LeaseCommandError,
  getChatSendLeaseParams,
  parseConnectSpeakingMode,
  type SpeakingLease,
} from '@/domain/collaboration/speakingLease';
import {
  GroupSpeakingLeaseService,
  type SpeakingLeaseSnapshot,
} from '@/services/workspace/groupSpeakingLeaseService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

class FakeTransport {
  readonly frames: Array<Record<string, unknown>> = [];
  readonly responders: Array<(frame: Record<string, unknown>) => unknown> = [];
  connected = true;

  send = async (frame: Record<string, unknown>): Promise<unknown> => {
    this.frames.push(frame);
    // 用最近注册的应答器异步回帧;帧 id 原样回显,与真实服务端一致。
    const respond = this.responders[this.responders.length - 1];
    if (respond) await respond(frame);
    return undefined;
  };

  respondWith(handler: (frame: Record<string, unknown>) => unknown): void {
    this.responders.push(handler);
  }
}

function makeService(transport: FakeTransport, humanActorId = 'human_1') {
  return new GroupSpeakingLeaseService({
    groupId: 'group-1',
    humanActorId,
    send: (frame) => transport.send(frame as Record<string, unknown>),
    isConnected: () => transport.connected,
  });
}

function grantedPayload(holderActorId = 'human_1', expiresAtMs = Date.now() + 60_000) {
  return {
    status: 'granted',
    leaseId: 'lease-2',
    epoch: 7,
    expiresAt_ms: expiresAtMs,
    holderActorId,
  };
}

let transport: FakeTransport;
let service: GroupSpeakingLeaseService;
let snapshots: SpeakingLeaseSnapshot[];

beforeEach(() => {
  transport = new FakeTransport();
  service = makeService(transport);
  snapshots = [];
  service.subscribe((snapshot) => snapshots.push(snapshot));
});

describe('domain pure helpers', () => {
  const lease = (expiresAtMs: number): SpeakingLease => ({
    leaseId: 'lease-1',
    epoch: 3,
    expiresAtMs,
    holderActorId: 'human_12345',
  });

  it('maps a usable lease to the chat.send wire fields', () => {
    expect(getChatSendLeaseParams(lease(110_000), 100_000)).toEqual({
      leaseId: 'lease-1',
      leaseEpoch: 3,
    });
  });

  it('drops expired or nearly-expired leases rather than sending stale claims', () => {
    expect(getChatSendLeaseParams(lease(100_000), 100_000)).toBeUndefined();
    expect(getChatSendLeaseParams(lease(102_000), 100_000)).toBeUndefined();
    expect(getChatSendLeaseParams(undefined, 100_000)).toBeUndefined();
  });

  it('normalizes connect speaking_mode: only lease_required survives verbatim', () => {
    expect(parseConnectSpeakingMode({ speaking_mode: 'lease_required' })).toBe('lease_required');
    expect(parseConnectSpeakingMode({ speaking_mode: 'plain' })).toBe('plain');
    expect(parseConnectSpeakingMode({})).toBe('plain');
    expect(parseConnectSpeakingMode(undefined)).toBe('plain');
  });
});

describe('GroupSpeakingLeaseService', () => {
  it('订阅即回放 unheld 初始快照;connect 应答设置 speakingMode', () => {
    expect(snapshots[0]).toEqual({
      speakingMode: undefined,
      lease: undefined,
      status: 'unheld',
      requesting: false,
    });
    service.setSpeakingMode('lease_required');
    expect(snapshots.at(-1)!.speakingMode).toBe('lease_required');
  });

  it('sends lease.acquire and normalizes the server payload (expiresAt_ms 混拼)', async () => {
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: grantedPayload('human_1', 180_000),
      }),
    );

    await expect(service.acquire()).resolves.toBe(true);
    expect(transport.frames).toHaveLength(1);
    expect(transport.frames[0]).toMatchObject({
      type: 'req',
      method: 'lease.acquire',
      params: { groupId: 'group-1' },
    });
    expect(snapshots.at(-1)).toMatchObject({
      status: 'held',
      lease: { leaseId: 'lease-2', epoch: 7, expiresAtMs: 180_000, holderActorId: 'human_1' },
    });
    // chat.send 注入参数:held 且未临期
    expect(service.usableLeaseParams(0)).toEqual({ leaseId: 'lease-2', leaseEpoch: 7 });
    // 收敛回 unheld,停轮询/到期定时器
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: { status: 'none' },
      }),
    );
    await service.refresh();
    expect(snapshots.at(-1)!.status).toBe('unheld');
    expect(service.usableLeaseParams(0)).toBeUndefined();
  });

  it('maps occupied/current-held-by-other to the occupied UI state', async () => {
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: { ...grantedPayload('human_2'), status: 'occupied' },
      }),
    );
    await expect(service.acquire()).resolves.toBe(false);
    expect(snapshots.at(-1)!.status).toBe('occupied');

    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: grantedPayload('human_2'),
      }),
    );
    await service.refresh();
    // lease.current 的 held 表示「有持有者」——非本人同样落 occupied
    expect(snapshots.at(-1)!.status).toBe('occupied');

    // 收敛回 unheld，停掉轮询（避免用例结束后定时器继续驱动）
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: { status: 'none' },
      }),
    );
    await service.refresh();
    expect(snapshots.at(-1)!.status).toBe('unheld');
  });

  it('routes responses by request id and rejects server refusals', async () => {
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: false,
        error: { code: 'lease_unavailable', message: 'unavailable' },
      }),
    );
    await expect(service.acquire(true)).rejects.toThrow('lease_unavailable');
    expect(transport.frames[0]).toMatchObject({
      method: 'lease.preempt',
      params: { groupId: 'group-1' },
    });
    // 指令失败落 error 态等待重试
    expect(snapshots.at(-1)!.status).toBe('error');
  });

  it('times out unmatched lease commands with the local lease_timeout code', async () => {
    jest.useFakeTimers();
    try {
      const pending = service.acquire();
      await Promise.resolve(); // flush 出站 send 的微任务
      jest.advanceTimersByTime(LEASE_COMMAND_TIMEOUT_MS);
      await expect(pending).rejects.toThrow('lease request timeout');
      expect(snapshots.at(-1)!.status).toBe('error');
    } finally {
      jest.useRealTimers();
    }
  });

  it('unrelated res frames pass through untouched', () => {
    expect(
      service.handleFrame({ type: 'res', id: 'not-pending', ok: true, payload: {} }),
    ).toBe(false);
    expect(service.handleFrame({ type: 'event', id: 'x', ok: true, payload: {} })).toBe(false);
    expect(service.handleFrame(null)).toBe(false);
  });

  it('releases the held lease with the server-granted leaseId/epoch', async () => {
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: grantedPayload('human_1'),
      }),
    );
    await expect(service.acquire()).resolves.toBe(true);

    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: { status: 'released' },
      }),
    );
    await expect(service.release()).resolves.toBeUndefined();
    const releaseFrame = transport.frames.at(-1)!;
    expect(releaseFrame).toMatchObject({
      method: 'lease.release',
      params: { groupId: 'group-1', leaseId: 'lease-2', leaseEpoch: 7 },
    });
    expect(snapshots.at(-1)!.status).toBe('unheld');
  });

  it('release failures surface as LeaseCommandError and keep the held UI state', async () => {
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: grantedPayload('human_1'),
      }),
    );
    await expect(service.acquire()).resolves.toBe(true);

    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: false,
        error: { code: 'lease_not_held' },
      }),
    );
    await expect(service.release()).rejects.toBeInstanceOf(LeaseCommandError);
    expect(snapshots.at(-1)!.status).toBe('held');

    // 收敛：释放成功 -> unheld（停轮询与到期定时器）
    transport.respondWith((frame) =>
      service.handleFrame({
        type: 'res',
        id: frame.id as string,
        ok: true,
        payload: { status: 'released' },
      }),
    );
    await expect(service.release()).resolves.toBeUndefined();
    expect(snapshots.at(-1)!.status).toBe('unheld');
  });

  it('silently skips commands while disconnected', async () => {
    transport.connected = false;
    await expect(service.acquire()).resolves.toBe(false);
    await expect(service.refresh()).resolves.toBeUndefined();
    expect(transport.frames).toHaveLength(0);
  });
});
