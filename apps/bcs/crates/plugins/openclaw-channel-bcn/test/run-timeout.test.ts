import { strict as assert } from 'node:assert';
import { mock } from 'node:test';
import {
  abortAllStreams,
  cleanupAgentEventsSubscription,
  handleChatSend,
  initAgentEventsSubscription,
} from '../src/inbound-handler.js';
import { setBcsRuntime } from '../src/runtime.js';

const THREE_HOURS_MS = 3 * 60 * 60 * 1000;

describe('BCN run completion timeout', () => {
  let onAgentEvent: (event: any) => boolean;
  let signals: Map<string, AbortSignal>;
  let releases: Map<string, () => void>;
  let pending: Promise<void>[];
  let events: Array<{ event: string; payload: any }>;

  beforeEach(() => {
    mock.timers.enable({ apis: [ 'setTimeout' ] });
    signals = new Map();
    releases = new Map();
    pending = [];
    events = [];
    setBcsRuntime({
      config: { async loadConfig() { return {}; } },
      events: {
        onAgentEvent(handler: typeof onAgentEvent) {
          onAgentEvent = event => handler({ ts: Date.now(), ...event });
          return () => true;
        },
      },
      channel: {
        routing: {
          resolveAgentRoute() {
            return { agentId: 'agent-1', sessionKey: 'bcs:shared-session' };
          },
        },
        reply: {
          finalizeInboundContext(ctx: unknown) { return ctx; },
          async dispatchReplyWithBufferedBlockDispatcher({ replyOptions }: any) {
            const { runId, abortSignal } = replyOptions;
            signals.set(runId, abortSignal);
            replyOptions.onAgentRunStart(runId);
            await new Promise<void>(resolve => {
              releases.set(runId, resolve);
              abortSignal.addEventListener('abort', () => {
                // Model synchronous SDK callbacks during cancellation.
                onAgentEvent({ runId, stream: 'lifecycle', data: { phase: 'error' } });
                resolve();
              }, { once: true });
            });
          },
        },
        session: {
          resolveStorePath() { return '/tmp/bcn-timeout-test'; },
          async recordInboundSession() { return undefined; },
        },
      },
    } as any);
    initAgentEventsSubscription();
  });

  afterEach(async () => {
    abortAllStreams();
    for (const release of releases.values()) release();
    await Promise.all(pending);
    cleanupAgentEventsSubscription();
    mock.timers.reset();
  });

  async function startRun(runId: string) {
    pending.push(handleChatSend({
      type: 'req',
      id: runId,
      method: 'chat.send',
      params: {
        idempotency_key: runId,
        bcs_group_id: 'group-1',
        bcs_session_id: 'group-1:session-1',
        channel: { source: 'api', user_id: 'user-1' },
        session_context: {},
        message: {
          role: 'user',
          content: [{ type: 'text', text: 'long-running task' }],
          timestamp: Date.now(),
        },
      },
    }, {
      sendResponse() {},
      sendEvent(event: string, payload: any) { events.push({ event, payload }); },
    } as any, { accountId: 'default', botId: 'bot-1' } as any));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(signals.has(runId), 'OpenClaw must receive the run abort signal');
  }

  it('aborts at three hours, emits one error, and ignores late completion', async () => {
    await startRun('timeout-run');
    mock.timers.tick(THREE_HOURS_MS - 1);
    assert.equal(signals.get('timeout-run')?.aborted, false);
    assert.equal(events.length, 0);

    mock.timers.tick(1);
    assert.equal(signals.get('timeout-run')?.aborted, true);
    await Promise.all(pending);
    onAgentEvent({ runId: 'timeout-run', stream: 'assistant', data: { delta: 'late reply' } });
    onAgentEvent({ runId: 'timeout-run', stream: 'lifecycle', data: { phase: 'end' } });
    assert.equal(events.length, 1);
    assert.equal(events[0].event, 'chat');
    assert.equal(events[0].payload.state, 'error');
    assert.equal(events[0].payload.content, 'Agent response timed out before completion.');
  });

  it('does not reset the deadline on output or cancel another run in the same Session', async () => {
    await startRun('older-run');
    mock.timers.tick(THREE_HOURS_MS / 2);
    await startRun('newer-run');
    onAgentEvent({ runId: 'older-run', stream: 'assistant', data: { delta: 'still working' } });
    mock.timers.tick(THREE_HOURS_MS / 2);
    assert.equal(signals.get('older-run')?.aborted, true);
    assert.equal(signals.get('newer-run')?.aborted, false);
    assert.deepEqual(events.filter(item => item.payload.state === 'error').map(item => item.payload.runId), [ 'older-run' ]);
  });

  it('clears the deadline after normal completion without aborting the run', async () => {
    await startRun('completed-run');
    onAgentEvent({ runId: 'completed-run', stream: 'assistant', data: { delta: 'done' } });
    onAgentEvent({ runId: 'completed-run', stream: 'lifecycle', data: { phase: 'end' } });
    releases.get('completed-run')!();
    await Promise.all(pending);
    const eventCount = events.length;
    mock.timers.tick(THREE_HOURS_MS);
    assert.equal(signals.get('completed-run')?.aborted, false);
    assert.equal(events.length, eventCount);
    assert.equal(events.filter(item => item.payload.state === 'final').length, 1);
  });
});
