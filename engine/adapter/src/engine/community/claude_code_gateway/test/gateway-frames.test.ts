import { strict as assert } from 'node:assert';
import type {
  AgentEventPayload,
  AgentLifecycleData,
  AgentToolData,
  AgentMessageData,
  AgentContentBlockData,
  AgentAssistantData,
  GatewayChatEvent,
  GatewayEventFrame,
  InteractionPhase,
  InteractionKind,
  InteractionRequestedEvent,
  InteractionResolvedEvent,
} from '../src/types.js';

/**
 * Unit tests for the new gateway data structures.
 * These verify that the new agent stream types (message, content_block,
 * enhanced lifecycle/tool/assistant) are correctly shaped and can be
 * serialized into GatewayEventFrame objects.
 */

describe('Gateway Data Structures', () => {
  const runId = 'run-test-001';
  const sessionKey = 'session:test:user:default';
  let connSeq = 0;

  function makeAgentEvent(stream: string, data: Record<string, unknown>): GatewayEventFrame {
    const runSeq = 0;
    const payload: AgentEventPayload = {
      runId,
      sessionKey,
      seq: runSeq + 1,
      stream,
      ts: Date.now(),
      data,
    };
    return {
      type: 'event',
      event: 'agent',
      payload,
      seq: ++connSeq,
    };
  }

  beforeEach(() => { connSeq = 0; });

  // ---- Lifecycle (enhanced with session_id, tools, cwd) ----

  describe('lifecycle stream', () => {
    it('should support start phase with sessionId, cwd, tools', () => {
      const data: AgentLifecycleData = {
        phase: 'start',
        sessionId: 'sess-abc-123',
        cwd: '/home/user/project',
        tools: [ 'Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep' ],
      };
      const frame = makeAgentEvent('lifecycle', data as unknown as Record<string, unknown>);

      assert.equal(frame.type, 'event');
      assert.equal(frame.event, 'agent');
      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.stream, 'lifecycle');
      assert.equal(payload.data.phase, 'start');
      assert.equal(payload.data.sessionId, 'sess-abc-123');
      assert.equal(payload.data.cwd, '/home/user/project');
      assert.deepEqual(payload.data.tools, [ 'Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep' ]);
    });

    it('should support end phase with stopReason', () => {
      const data: AgentLifecycleData = { phase: 'end', stopReason: 'end_turn' };
      const frame = makeAgentEvent('lifecycle', data as unknown as Record<string, unknown>);
      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.data.phase, 'end');
      assert.equal(payload.data.stopReason, 'end_turn');
    });
  });

  // ---- Message stream (new) ----

  describe('message stream', () => {
    it('should support start phase with model and usage', () => {
      const data: AgentMessageData = {
        phase: 'start',
        messageId: 'msg_01XYZ',
        model: 'claude-sonnet-4-5',
        usage: { inputTokens: 1250, outputTokens: 0 },
      };
      const frame = makeAgentEvent('message', data as unknown as Record<string, unknown>);

      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.stream, 'message');
      assert.equal(payload.data.phase, 'start');
      assert.equal(payload.data.messageId, 'msg_01XYZ');
      assert.equal(payload.data.model, 'claude-sonnet-4-5');
      assert.equal((payload.data.usage as any).inputTokens, 1250);
    });

    it('should support stop phase', () => {
      const data: AgentMessageData = { phase: 'stop' };
      const frame = makeAgentEvent('message', data as unknown as Record<string, unknown>);
      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.data.phase, 'stop');
    });
  });

  // ---- Content block stream (new) ----

  describe('content_block stream', () => {
    it('should support start phase for thinking block', () => {
      const data: AgentContentBlockData = {
        phase: 'start',
        index: 0,
        blockType: 'thinking',
      };
      const frame = makeAgentEvent('content_block', data as unknown as Record<string, unknown>);

      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.stream, 'content_block');
      assert.equal(payload.data.phase, 'start');
      assert.equal(payload.data.blockType, 'thinking');
      assert.equal(payload.data.index, 0);
    });

    it('should support start phase for tool_use block with toolCallId', () => {
      const data: AgentContentBlockData = {
        phase: 'start',
        index: 2,
        blockType: 'tool_use',
        toolCallId: 'toolu_01ABC',
        name: 'Read',
      };
      const frame = makeAgentEvent('content_block', data as unknown as Record<string, unknown>);
      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.data.blockType, 'tool_use');
      assert.equal(payload.data.toolCallId, 'toolu_01ABC');
      assert.equal(payload.data.name, 'Read');
    });

    it('should support stop phase', () => {
      const data: AgentContentBlockData = {
        phase: 'stop',
        index: 0,
        blockType: 'thinking',
      };
      const frame = makeAgentEvent('content_block', data as unknown as Record<string, unknown>);
      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.data.phase, 'stop');
    });
  });

  // ---- Tool stream (enhanced with update phase) ----

  describe('tool stream', () => {
    it('should support update phase with partialInput', () => {
      const data: AgentToolData = {
        type: 'update',
        toolCallId: 'toolu_01ABC',
        toolName: 'Read',
        partialInput: '{"file_path":"/src/',
      };
      const frame = makeAgentEvent('tool', data as unknown as Record<string, unknown>);

      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.stream, 'tool');
      assert.equal(payload.data.type, 'update');
      assert.equal(payload.data.partialInput, '{"file_path":"/src/');
    });
  });

  // ---- Assistant stream (enhanced with cost, duration, turns) ----

  describe('assistant stream', () => {
    it('should support costUsd, durationMs, and numTurns', () => {
      const data: AgentAssistantData = {
        usage: {
          inputTokens: 1250,
          outputTokens: 480,
          cacheReadTokens: 800,
          cacheCreationTokens: 200,
        },
        costUsd: 0.003,
        durationMs: 5200,
        numTurns: 1,
        model: 'claude-sonnet-4-5',
      };
      const frame = makeAgentEvent('assistant', data as unknown as Record<string, unknown>);

      const payload = frame.payload as AgentEventPayload;
      assert.equal(payload.stream, 'assistant');
      assert.equal(payload.data.costUsd, 0.003);
      assert.equal(payload.data.durationMs, 5200);
      assert.equal(payload.data.numTurns, 1);
      assert.equal(payload.data.model, 'claude-sonnet-4-5');
    });
  });

  // ---- Full event sequence simulation ----

  describe('full event sequence', () => {
    it('should produce a valid sequence for a complete Claude interaction', () => {
      const events: GatewayEventFrame[] = [];
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      let agentSeq = 0;

      const emit = (stream: string, data: Record<string, unknown>) => {
        events.push(makeAgentEvent(stream, data));
        agentSeq++;
      };

      // 1. lifecycle start (with session details)
      emit('lifecycle', { phase: 'start', sessionId: 'sess-1', cwd: '/tmp', tools: [ 'Bash', 'Read' ] } as unknown as Record<string, unknown>);

      // 2. message start
      emit('message', { phase: 'start', model: 'claude-sonnet-4-5', messageId: 'msg_01' } as unknown as Record<string, unknown>);

      // 3. thinking block
      emit('content_block', { phase: 'start', index: 0, blockType: 'thinking' } as unknown as Record<string, unknown>);
      emit('thinking', { text: 'Let me think...', delta: 'Let me think...' } as unknown as Record<string, unknown>);
      emit('content_block', { phase: 'stop', index: 0, blockType: 'thinking' } as unknown as Record<string, unknown>);

      // 4. text block
      emit('content_block', { phase: 'start', index: 1, blockType: 'text' } as unknown as Record<string, unknown>);
      emit('content_block', { phase: 'stop', index: 1, blockType: 'text' } as unknown as Record<string, unknown>);

      // 5. tool block
      emit('content_block', { phase: 'start', index: 2, blockType: 'tool_use', toolCallId: 'toolu_01', name: 'Read' } as unknown as Record<string, unknown>);
      emit('tool', { phase: 'start', toolCallId: 'toolu_01', name: 'Read', args: {} } as unknown as Record<string, unknown>);
      emit('tool', { phase: 'update', toolCallId: 'toolu_01', name: 'Read', partialArgs: '{"file_path":"/src/server.ts"}' } as unknown as Record<string, unknown>);
      emit('tool', { phase: 'result', toolCallId: 'toolu_01', name: 'Read', result: { file_path: '/src/server.ts' } } as unknown as Record<string, unknown>);
      emit('content_block', { phase: 'stop', index: 2, blockType: 'tool_use' } as unknown as Record<string, unknown>);
      emit('command_output', { toolCallId: 'toolu_01', phase: 'end', exitCode: 0, cwd: '/tmp' } as unknown as Record<string, unknown>);

      // 6. message stop
      emit('message', { phase: 'stop' } as unknown as Record<string, unknown>);

      // 7. assistant (usage + cost)
      emit('assistant', { usage: { inputTokens: 500, outputTokens: 200 }, costUsd: 0.001, durationMs: 3200, numTurns: 1 } as unknown as Record<string, unknown>);

      // 8. lifecycle end
      emit('lifecycle', { phase: 'end', stopReason: 'end_turn' } as unknown as Record<string, unknown>);

      // Verify the sequence
      assert.equal(events.length, 16);

      // All frames are valid JSON
      for (const frame of events) {
        const json = JSON.stringify(frame);
        assert.ok(json.length > 0, 'frame should serialize to JSON');
        const parsed = JSON.parse(json);
        assert.equal(parsed.type, 'event');
        assert.equal(parsed.event, 'agent');
      }

      // Verify sequence
      const streams = events.map(e => (e.payload as AgentEventPayload).stream);
      assert.deepEqual(streams, [
        'lifecycle', 'message',
        'content_block', 'thinking', 'content_block',
        'content_block', 'content_block',
        'content_block', 'tool', 'tool', 'tool', 'content_block', 'command_output',
        'message', 'assistant', 'lifecycle',
      ]);
    });
  });
});

describe('GatewayChatEvent', () => {
  it('should support delta state', () => {
    const evt: GatewayChatEvent = {
      runId: 'run-1',
      sessionKey: 'session:test',
      seq: 1,
      state: 'delta',
      message: {
        role: 'assistant',
        content: [{ type: 'text', text: 'Hello' }],
        timestamp: Date.now(),
      },
    };
    assert.equal(evt.state, 'delta');
    assert.equal(evt.message?.content?.[0]?.text, 'Hello');
  });
});

// ---- v2 Protocol: Interaction and Mode Transition ----

describe('Interaction Stream (v2 Protocol)', () => {
  const runId = 'run-interaction-001';
  const sessionKey = 'session:test:user:default';
  let connSeq = 0;

  function makeAgentEvent(stream: string, data: Record<string, unknown>): GatewayEventFrame {
    const payload: AgentEventPayload = {
      runId,
      sessionKey,
      seq: 1,
      stream,
      ts: Date.now(),
      data,
    };
    return {
      type: 'event',
      event: 'agent',
      payload,
      seq: ++connSeq,
    };
  }

  beforeEach(() => { connSeq = 0; });

  it('should support interaction:requested phase', () => {
    const data = {
      phase: 'requested' as InteractionPhase,
      interactionId: 'int:uuid-123',
      kind: 'ask_user',
      prompt: 'Which database do you want to use?',
      questions: [
        {
          question: 'Which database?',
          header: 'Database',
          options: [
            { label: 'PostgreSQL', description: 'Relational database' },
            { label: 'MongoDB', description: 'Document database' },
          ],
        },
      ],
      createdAtMs: Date.now(),
      expiresAtMs: Date.now() + 300000,
    };
    const frame = makeAgentEvent('interaction', data as unknown as Record<string, unknown>);

    assert.equal(frame.type, 'event');
    assert.equal(frame.event, 'agent');
    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.stream, 'interaction');
    assert.equal(payload.data.phase, 'requested');
    assert.equal(payload.data.kind, 'ask_user');
    assert.equal((payload.data.questions as Array<unknown>)?.length, 1);
  });

  it('should support interaction:answered phase', () => {
    const data = {
      phase: 'answered' as InteractionPhase,
      interactionId: 'int:uuid-123',
      answer: 'PostgreSQL',
      resolvedBy: 'operator',
    };
    const frame = makeAgentEvent('interaction', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.stream, 'interaction');
    assert.equal(payload.data.phase, 'answered');
    assert.equal(payload.data.answer, 'PostgreSQL');
    assert.equal(payload.data.resolvedBy, 'operator');
  });

  it('should support interaction:cancelled phase', () => {
    const data = {
      phase: 'cancelled' as InteractionPhase,
      interactionId: 'int:uuid-123',
      resolvedBy: 'operator',
    };
    const frame = makeAgentEvent('interaction', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.data.phase, 'cancelled');
  });

  it('should support interaction:expired phase', () => {
    const data = {
      phase: 'expired' as InteractionPhase,
      interactionId: 'int:uuid-123',
      resolvedBy: 'system',
    };
    const frame = makeAgentEvent('interaction', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.data.phase, 'expired');
    assert.equal(payload.data.resolvedBy, 'system');
  });
});

describe('Mode Transition Stream (v2 Protocol)', () => {
  const runId = 'run-transition-001';
  const sessionKey = 'session:test:user:default';
  let connSeq = 0;

  function makeAgentEvent(stream: string, data: Record<string, unknown>): GatewayEventFrame {
    const payload: AgentEventPayload = {
      runId,
      sessionKey,
      seq: 1,
      stream,
      ts: Date.now(),
      data,
    };
    return {
      type: 'event',
      event: 'agent',
      payload,
      seq: ++connSeq,
    };
  }

  beforeEach(() => { connSeq = 0; });

  it('should support mode_transition:requested phase (deprecated, use interaction.*)', () => {
    const data = {
      phase: 'requested' as InteractionPhase,
      transitionId: 'mode:uuid-456',
      kind: 'exit_plan_mode',
      fromMode: 'plan',
      toMode: 'execute',
      summary: 'Planning complete, ready to execute.',
      createdAtMs: Date.now(),
      expiresAtMs: Date.now() + 300000,
    };
    const frame = makeAgentEvent('mode_transition', data as unknown as Record<string, unknown>);

    assert.equal(frame.type, 'event');
    assert.equal(frame.event, 'agent');
    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.stream, 'mode_transition');
    assert.equal(payload.data.phase, 'requested');
    assert.equal(payload.data.kind, 'exit_plan_mode');
    assert.equal(payload.data.fromMode, 'plan');
    assert.equal(payload.data.toMode, 'execute');
  });

  it('should support mode_transition:resolved phase with proceed decision (deprecated)', () => {
    const data = {
      phase: 'resolved' as InteractionPhase,
      transitionId: 'mode:uuid-456',
      decision: 'proceed',
      resolvedBy: 'operator',
    };
    const frame = makeAgentEvent('mode_transition', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.stream, 'mode_transition');
    assert.equal(payload.data.phase, 'resolved');
    assert.equal(payload.data.decision, 'proceed');
  });

  it('should support mode_transition:resolved phase with stay decision (deprecated)', () => {
    const data = {
      phase: 'resolved' as InteractionPhase,
      transitionId: 'mode:uuid-456',
      decision: 'stay',
      resolvedBy: 'operator',
    };
    const frame = makeAgentEvent('mode_transition', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.data.decision, 'stay');
  });

  it('should support mode_transition:expired phase (deprecated)', () => {
    const data = {
      phase: 'resolved' as InteractionPhase,
      transitionId: 'mode:uuid-456',
      decision: 'expired',
      resolvedBy: 'system',
    };
    const frame = makeAgentEvent('mode_transition', data as unknown as Record<string, unknown>);

    const payload = frame.payload as AgentEventPayload;
    assert.equal(payload.data.decision, 'expired');
    assert.equal(payload.data.resolvedBy, 'system');
  });
});

// ---- v2 Protocol: Top-level Interaction Events ----

describe('Top-level Interaction Events (v2 Protocol)', () => {
  const runId = 'run-interaction-top-001';
  const sessionKey = 'session:test:user:default';
  let connSeq = 0;

  function makeInteractionRequestedEvent(payload: InteractionRequestedEvent): GatewayEventFrame {
    return {
      type: 'event',
      event: 'interaction.requested',
      payload,
      seq: ++connSeq,
    };
  }

  function makeInteractionResolvedEvent(payload: InteractionResolvedEvent): GatewayEventFrame {
    return {
      type: 'event',
      event: 'interaction.resolved',
      payload,
      seq: ++connSeq,
    };
  }

  beforeEach(() => { connSeq = 0; });

  it('should support interaction.requested top-level event', () => {
    const payload: InteractionRequestedEvent = {
      runId,
      interactionId: 'int:uuid-123',
      sessionKey,
      kind: 'ask_user',
      prompt: 'Which database do you want to use?',
      questions: [
        {
          question: 'Which database?',
          header: 'Database',
          options: [
            { label: 'PostgreSQL', description: 'Relational database' },
            { label: 'MongoDB', description: 'Document database' },
          ],
        },
      ],
      createdAtMs: Date.now(),
      expiresAtMs: Date.now() + 300000,
    };
    const frame = makeInteractionRequestedEvent(payload);

    assert.equal(frame.type, 'event');
    assert.equal(frame.event, 'interaction.requested');
    assert.equal((frame.payload as InteractionRequestedEvent).runId, runId);
    assert.equal((frame.payload as InteractionRequestedEvent).interactionId, 'int:uuid-123');
    assert.equal((frame.payload as InteractionRequestedEvent).sessionKey, sessionKey);
    assert.equal((frame.payload as InteractionRequestedEvent).kind, 'ask_user');
    assert.equal(((frame.payload as InteractionRequestedEvent).questions as Array<unknown>)?.length, 1);
  });

  it('should support interaction.resolved (answered) top-level event', () => {
    const payload: InteractionResolvedEvent = {
      interactionId: 'int:uuid-123',
      runId,
      sessionKey,
      kind: 'ask_user' as InteractionKind,
      phase: 'answered',
      decision: 'submit',
      answer: 'PostgreSQL',
      resolvedBy: 'operator',
      resolvedAtMs: Date.now(),
    };
    const frame = makeInteractionResolvedEvent(payload);

    assert.equal(frame.type, 'event');
    assert.equal(frame.event, 'interaction.resolved');
    assert.equal((frame.payload as InteractionResolvedEvent).phase, 'answered');
    assert.equal((frame.payload as InteractionResolvedEvent).answer, 'PostgreSQL');
    assert.equal((frame.payload as InteractionResolvedEvent).resolvedBy, 'operator');
  });

  it('should support interaction.resolved (cancelled) top-level event', () => {
    const payload: InteractionResolvedEvent = {
      interactionId: 'int:uuid-123',
      runId,
      sessionKey,
      kind: 'ask_user' as InteractionKind,
      phase: 'cancelled',
      decision: 'cancel',
      resolvedBy: 'operator',
      resolvedAtMs: Date.now(),
    };
    const frame = makeInteractionResolvedEvent(payload);

    assert.equal((frame.payload as InteractionResolvedEvent).phase, 'cancelled');
    assert.equal((frame.payload as InteractionResolvedEvent).resolvedBy, 'operator');
  });

  it('should support interaction.resolved (expired) top-level event', () => {
    const payload: InteractionResolvedEvent = {
      interactionId: 'int:uuid-123',
      runId,
      sessionKey,
      kind: 'ask_user' as InteractionKind,
      phase: 'expired',
      decision: 'cancel',
      resolvedBy: 'system',
      resolvedAtMs: Date.now(),
    };
    const frame = makeInteractionResolvedEvent(payload);

    assert.equal((frame.payload as InteractionResolvedEvent).phase, 'expired');
    assert.equal((frame.payload as InteractionResolvedEvent).resolvedBy, 'system');
  });
});
