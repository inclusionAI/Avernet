import assert from 'node:assert/strict';
import test from 'node:test';
import { Context } from '@deepseek-ai/cordis';
import type { Agent } from '@deepseek-ai/dsh-agent';
import { ToolCallId } from '@deepseek-ai/dsh-llm/brand';
import { BcnInteractions, type InteractionRun } from '../src/interactions.js';
import type { BcnWsClient } from '../src/ws-client.js';

function fixture() {
  const ctx = new Context();
  const owned = { id: 'owned' } as unknown as Agent;
  const other = { id: 'web-session' } as unknown as Agent;
  const run: InteractionRun = { runId: 'run', bcsSessionId: 'group:session', toolArguments: new Map([
    ['call', { command: 'top -l 2' }],
  ]) };
  const frames: Array<{ event: string; payload: Record<string, any> }> = [];
  let legacyCalls = 0;
  // Match DSH api-remotes: an earlier Web handler owns the waterfall unless
  // an agent-scoped answerer explicitly takes precedence.
  ctx.on('approval/request', async () => { legacyCalls++; return 'rejected' as const; });
  ctx.on('user-questions/request', async () => { legacyCalls++; return { answers: [] }; });
  const client = { sendEvent(event: string, payload: Record<string, any>) { frames.push({ event, payload }); },
    sendResponse() {} } as unknown as BcnWsClient;
  const interactions = new BcnInteractions(client, agent => agent === owned ? run : undefined);
  interactions.mount(ctx, owned);
  return { ctx, owned, other, interactions, frames, legacyCalls: () => legacyCalls };
}

test('BCN approval precedes the existing Web answerer and waits for an authenticated decision', async () => {
  const f = fixture();
  const outcome = f.ctx.waterfall('approval/request', {
    agent: f.owned, toolName: 'bash', callId: ToolCallId('call'), reason: 'escalate sandbox',
  }, async () => 'unavailable' as const);
  assert.equal(f.legacyCalls(), 0);
  const request = f.frames[0]!.payload;
  assert.equal(request.kind, 'exec');
  assert.equal(request.command, 'top -l 2');
  assert.equal(request.phase, 'requested');
  await f.interactions.resolve({ type: 'req', id: 'resolve', method: 'interaction.resolve', params: {
    runId: 'run', interactionId: request.interactionId, kind: 'exec', idempotencyKey: 'key', decision: 'deny',
  } });
  assert.equal(await outcome, 'rejected');
  assert.equal(f.frames.at(-1)!.payload.phase, 'resolved');
  f.interactions.dispose();
});

test('unrelated Web agents keep their original approval handler', async () => {
  const f = fixture();
  const outcome = await f.ctx.waterfall('approval/request', {
    agent: f.other, toolName: 'bash',
  }, async () => 'unavailable' as const);
  assert.equal(outcome, 'rejected');
  assert.equal(f.legacyCalls(), 1);
  assert.equal(f.frames.length, 0);
  f.interactions.dispose();
});

test('BCN questions also precede the Web answerer without granting execution permission', async () => {
  const f = fixture();
  const outcome = f.ctx.waterfall('user-questions/request', { agent: f.owned,
    questions: [{ id: 'q', question: 'Which environment?' }],
  }, async () => ({ answers: [] }));
  assert.equal(f.legacyCalls(), 0);
  const request = f.frames[0]!.payload;
  assert.equal(request.kind, 'ask_user');
  await f.interactions.resolve({ type: 'req', id: 'answer', method: 'interaction.resolve', params: {
    runId: 'run', interactionId: request.interactionId, kind: 'ask_user', idempotencyKey: 'key',
    action: 'submit', answers: { q: { values: ['pre'] } },
  } });
  assert.deepEqual(await outcome, { answers: [{ id: 'q', selected: [], custom: 'pre' }] });
  f.interactions.dispose();
});
