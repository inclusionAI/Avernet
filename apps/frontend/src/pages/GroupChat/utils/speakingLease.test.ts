import {
  getChatSendLeaseParams,
  GroupChatLeaseClient,
  type LeaseResponseFrame,
  type SpeakingLease,
} from './speakingLease';

function lease(expiresAtMs: number): SpeakingLease {
  return {
    leaseId: 'lease-1',
    epoch: 3,
    expiresAtMs,
    holderActorId: 'human_12345',
  };
}

describe('group speaking lease protocol helper', () => {
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

  it('sends lease.acquire and resolves the normalized server payload', async () => {
    const frames: Array<Record<string, any>> = [];
    let client: GroupChatLeaseClient;
    const transport = {
      send: async (frame: Record<string, any>) => {
        frames.push(frame);
        const response: LeaseResponseFrame = {
          type: 'res',
          id: frame.id as string,
          ok: true,
          payload: {
            status: 'granted',
            leaseId: 'lease-2',
            epoch: 7,
            expiresAt_ms: 180_000,
          },
        };
        client.handleFrame(response);
      },
    };
    client = new GroupChatLeaseClient(transport, {
      generateId: () => 'request-1',
      timeoutMs: 100_000,
    });

    await expect(client.acquire('group-1')).resolves.toEqual({
      status: 'granted',
      lease: {
        leaseId: 'lease-2',
        epoch: 7,
        expiresAtMs: 180_000,
        holderActorId: undefined,
      },
    });
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({
      type: 'req',
      id: 'request-1',
      method: 'lease.acquire',
      params: { groupId: 'group-1' },
    });
  });

  it('routes responses by request id and rejects server refusals', async () => {
    const frames: Array<Record<string, any>> = [];
    let client: GroupChatLeaseClient;
    const transport = {
      send: async (frame: Record<string, any>) => {
        frames.push(frame);
        client.handleFrame({
          type: 'res',
          id: frame.id as string,
          ok: false,
          error: { code: 'lease_unavailable', message: 'unavailable' },
        });
      },
    };
    client = new GroupChatLeaseClient(transport, {
      generateId: () => 'request-2',
      timeoutMs: 100_000,
    });

    await expect(client.preempt('group-1')).rejects.toThrow(
      'lease_unavailable',
    );
    expect(frames[0]).toMatchObject({
      method: 'lease.preempt',
      params: { groupId: 'group-1' },
    });
  });
});
