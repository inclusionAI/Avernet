import { strict as assert } from 'node:assert';
import type {
  AgentContext,
  AgentEventStream,
  AgentToolPhase,
  AgentToolData,
  AgentTaskData,
  AgentSystemData,
  SystemStatusChange,
  SystemApiRetry,
  SystemRateLimit,
  SystemCompactBoundary,
  SystemFilesPersisted,
  AgentMemoryData,
  InteractionRequestedEvent,
  NotificationEvent,
  NotificationPriority,
  PromptSuggestionEvent,
} from '../src/types.js';

/**
 * Tests for protocol enhancement types (Task 1).
 * Verifies that new types compile correctly and have the expected shape.
 */

describe('Protocol Enhancement Types', () => {

  // ---- AgentContext ----

  describe('AgentContext', () => {
    it('should accept an AgentContext with parentToolUseId', () => {
      const ctx: AgentContext = {
        parentToolUseId: 'toolu_01ABC123',
      };
      assert.equal(ctx.parentToolUseId, 'toolu_01ABC123');
    });

    it('should accept a fully populated AgentContext', () => {
      const ctx: AgentContext = {
        parentToolUseId: 'toolu_01ABC123',
        taskId: 'task-456',
        agentId: 'agent-789',
        agentType: 'code-reviewer',
        agentName: 'Code Review Agent',
      };
      assert.equal(ctx.parentToolUseId, 'toolu_01ABC123');
      assert.equal(ctx.taskId, 'task-456');
      assert.equal(ctx.agentId, 'agent-789');
      assert.equal(ctx.agentType, 'code-reviewer');
      assert.equal(ctx.agentName, 'Code Review Agent');
    });

    it('should allow empty AgentContext (all fields optional)', () => {
      const ctx: AgentContext = {};
      assert.equal(ctx.parentToolUseId, undefined);
    });
  });

  // ---- AgentEventStream includes 'system' and 'memory' ----

  describe('AgentEventStream', () => {
    it('should accept system as a valid stream value', () => {
      const stream: AgentEventStream = 'system';
      assert.equal(stream, 'system');
    });

    it('should accept memory as a valid stream value', () => {
      const stream: AgentEventStream = 'memory';
      assert.equal(stream, 'memory');
    });

    it('should still accept existing stream values', () => {
      const streams: AgentEventStream[] = [
        'lifecycle', 'tool', 'assistant', 'thinking',
        'command_output', 'interaction', 'mode_transition',
        'message', 'content_block', 'todo', 'task',
      ];
      assert.equal(streams.length, 11);
    });
  });

  // ---- AgentToolPhase includes 'progress' and 'summary' ----

  describe('AgentToolPhase', () => {
    it('should include progress phase', () => {
      const phase: AgentToolPhase = 'progress';
      assert.equal(phase, 'progress');
    });

    it('should include summary phase', () => {
      const phase: AgentToolPhase = 'summary';
      assert.equal(phase, 'summary');
    });

    it('should include all existing phases', () => {
      const phases: AgentToolPhase[] = [ 'start', 'update', 'progress', 'result', 'summary', 'task' ];
      assert.equal(phases.length, 6);
    });
  });

  // ---- AgentToolData new fields ----

  describe('AgentToolData new fields', () => {
    it('should accept agentContext on AgentToolData', () => {
      const data: AgentToolData = {
        type: 'start',
        toolCallId: 'tc-001',
        toolName: 'Read',
        agentContext: {
          parentToolUseId: 'toolu_01ABC123',
          agentType: 'general-purpose',
        },
      };
      assert.equal(data.agentContext?.parentToolUseId, 'toolu_01ABC123');
      assert.equal(data.agentContext?.agentType, 'general-purpose');
    });

    it('should accept progress field on AgentToolData', () => {
      const data: AgentToolData = {
        type: 'progress',
        toolCallId: 'tc-002',
        toolName: 'Bash',
        progress: { elapsedSeconds: 42 },
      };
      assert.equal(data.progress?.elapsedSeconds, 42);
    });

    it('should accept summary fields on AgentToolData', () => {
      const data: AgentToolData = {
        type: 'summary',
        toolCallId: 'tc-003',
        toolName: 'Task',
        precedingToolUseIds: [ 'toolu_prev1', 'toolu_prev2' ],
        summary: 'Task completed successfully',
      };
      assert.deepEqual(data.precedingToolUseIds, [ 'toolu_prev1', 'toolu_prev2' ]);
      assert.equal(data.summary, 'Task completed successfully');
    });

    it('should accept subagentTools on AgentToolData', () => {
      const data: AgentToolData = {
        type: 'result',
        toolCallId: 'tc-004',
        toolName: 'Task',
        subagentTools: [
          {
            toolId: 'toolu_sub1',
            toolName: 'Read',
            toolInput: { file_path: '/tmp/a.txt' },
            toolResult: { content: 'file contents', isError: false },
            timestamp: 1714500000000,
          },
          {
            toolId: 'toolu_sub2',
            toolName: 'Write',
            toolInput: { file_path: '/tmp/b.txt', content: 'hello' },
            toolResult: null,
            timestamp: 1714500001000,
          },
        ],
      };
      assert.equal(data.subagentTools?.length, 2);
      assert.equal(data.subagentTools?.[0]?.toolName, 'Read');
      assert.equal(data.subagentTools?.[1]?.toolResult, null);
    });
  });

  // ---- SystemStatusChange ----

  describe('SystemStatusChange', () => {
    it('should support compacting status with compactResult', () => {
      const event: SystemStatusChange = {
        type: 'status_change',
        status: 'compacting',
        compactResult: 'success',
      };
      assert.equal(event.type, 'status_change');
      assert.equal(event.status, 'compacting');
      assert.equal(event.compactResult, 'success');
    });

    it('should support failed compaction with error', () => {
      const event: SystemStatusChange = {
        type: 'status_change',
        status: null,
        compactResult: 'failed',
        compactError: 'Context window exceeded',
      };
      assert.equal(event.compactResult, 'failed');
      assert.equal(event.compactError, 'Context window exceeded');
    });

    it('should support requesting status', () => {
      const event: SystemStatusChange = {
        type: 'status_change',
        status: 'requesting',
      };
      assert.equal(event.status, 'requesting');
    });
  });

  // ---- SystemApiRetry ----

  describe('SystemApiRetry', () => {
    it('should capture retry attempt details', () => {
      const event: SystemApiRetry = {
        type: 'api_retry',
        attempt: 2,
        maxRetries: 5,
        retryDelayMs: 3000,
        errorStatus: 429,
        error: 'Rate limited',
      };
      assert.equal(event.type, 'api_retry');
      assert.equal(event.attempt, 2);
      assert.equal(event.maxRetries, 5);
      assert.equal(event.retryDelayMs, 3000);
      assert.equal(event.errorStatus, 429);
      assert.equal(event.error, 'Rate limited');
    });

    it('should allow null errorStatus', () => {
      const event: SystemApiRetry = {
        type: 'api_retry',
        attempt: 1,
        maxRetries: 3,
        retryDelayMs: 1000,
        errorStatus: null,
        error: 'Network timeout',
      };
      assert.equal(event.errorStatus, null);
    });
  });

  // ---- SystemRateLimit ----

  describe('SystemRateLimit', () => {
    it('should match SDK rate limit structure', () => {
      const event: SystemRateLimit = {
        type: 'rate_limit',
        status: 'allowed_warning',
        rateLimitType: 'five_hour',
        utilization: 0.85,
        resetsAt: 1714510000,
      };
      assert.equal(event.type, 'rate_limit');
      assert.equal(event.status, 'allowed_warning');
      assert.equal(event.rateLimitType, 'five_hour');
      assert.equal(event.utilization, 0.85);
      assert.equal(event.resetsAt, 1714510000);
    });

    it('should support rejected status with overage fields', () => {
      const event: SystemRateLimit = {
        type: 'rate_limit',
        status: 'rejected',
        rateLimitType: 'overage',
        overageStatus: 'rejected',
        overageResetsAt: 1714520000,
      };
      assert.equal(event.status, 'rejected');
      assert.equal(event.overageStatus, 'rejected');
      assert.equal(event.overageResetsAt, 1714520000);
    });

    it('should support seven_day_opus and seven_day_sonnet rate limit types', () => {
      const opus: SystemRateLimit = {
        type: 'rate_limit',
        status: 'allowed',
        rateLimitType: 'seven_day_opus',
      };
      const sonnet: SystemRateLimit = {
        type: 'rate_limit',
        status: 'allowed',
        rateLimitType: 'seven_day_sonnet',
      };
      assert.equal(opus.rateLimitType, 'seven_day_opus');
      assert.equal(sonnet.rateLimitType, 'seven_day_sonnet');
    });
  });

  // ---- SystemCompactBoundary ----

  describe('SystemCompactBoundary', () => {
    it('should capture trigger and token counts', () => {
      const event: SystemCompactBoundary = {
        type: 'compact_boundary',
        trigger: 'auto',
        preTokens: 150000,
        postTokens: 30000,
        durationMs: 1200,
        compactedTurns: 8,
      };
      assert.equal(event.type, 'compact_boundary');
      assert.equal(event.trigger, 'auto');
      assert.equal(event.preTokens, 150000);
      assert.equal(event.postTokens, 30000);
      assert.equal(event.compactedTurns, 8);
    });

    it('should support manual trigger', () => {
      const event: SystemCompactBoundary = {
        type: 'compact_boundary',
        trigger: 'manual',
        preTokens: 180000,
        compactedTurns: 12,
      };
      assert.equal(event.trigger, 'manual');
      assert.equal(event.postTokens, undefined);
      assert.equal(event.durationMs, undefined);
    });
  });

  // ---- SystemFilesPersisted ----

  describe('SystemFilesPersisted', () => {
    it('should include both succeeded and failed arrays', () => {
      const event: SystemFilesPersisted = {
        type: 'files_persisted',
        files: [
          { filename: 'src/index.ts', fileId: 'fid-001' },
          { filename: 'README.md', fileId: 'fid-002' },
        ],
        failed: [
          { filename: 'denied.log', error: 'Permission denied' },
        ],
        processedAt: '2025-04-30T12:00:00Z',
      };
      assert.equal(event.type, 'files_persisted');
      assert.equal(event.files.length, 2);
      assert.equal(event.failed.length, 1);
      assert.equal(event.files[0].filename, 'src/index.ts');
      assert.equal(event.failed[0].error, 'Permission denied');
      assert.equal(event.processedAt, '2025-04-30T12:00:00Z');
    });

    it('should allow empty failed array', () => {
      const event: SystemFilesPersisted = {
        type: 'files_persisted',
        files: [{ filename: 'a.ts', fileId: 'fid-003' }],
        failed: [],
        processedAt: '2025-04-30T12:00:00Z',
      };
      assert.equal(event.failed.length, 0);
    });
  });

  // ---- AgentSystemData union ----

  describe('AgentSystemData union', () => {
    it('should accept SystemStatusChange', () => {
      const sysData: AgentSystemData = {
        type: 'status_change',
        status: 'compacting',
      };
      assert.equal(sysData.type, 'status_change');
    });

    it('should accept SystemApiRetry', () => {
      const sysData: AgentSystemData = {
        type: 'api_retry',
        attempt: 1,
        maxRetries: 3,
        retryDelayMs: 1000,
        error: 'timeout',
      };
      assert.equal(sysData.type, 'api_retry');
    });

    it('should accept SystemRateLimit', () => {
      const sysData: AgentSystemData = {
        type: 'rate_limit',
        status: 'allowed',
      };
      assert.equal(sysData.type, 'rate_limit');
    });

    it('should accept SystemCompactBoundary', () => {
      const sysData: AgentSystemData = {
        type: 'compact_boundary',
        trigger: 'auto',
        preTokens: 100000,
        compactedTurns: 5,
      };
      assert.equal(sysData.type, 'compact_boundary');
    });

    it('should accept SystemFilesPersisted', () => {
      const sysData: AgentSystemData = {
        type: 'files_persisted',
        files: [],
        failed: [],
        processedAt: '2025-01-01T00:00:00Z',
      };
      assert.equal(sysData.type, 'files_persisted');
    });
  });

  // ---- AgentMemoryData ----

  describe('AgentMemoryData', () => {
    it('should use content field (not synthesis)', () => {
      const data: AgentMemoryData = {
        type: 'recall',
        mode: 'select',
        memories: [
          { path: '/memory/personal/code-style.md', scope: 'personal', content: 'Prefer functional style' },
          { path: '/memory/team/conventions.md', scope: 'team', content: null },
        ],
      };
      assert.equal(data.type, 'recall');
      assert.equal(data.mode, 'select');
      assert.equal(data.memories.length, 2);
      assert.equal(data.memories[0]?.content, 'Prefer functional style');
      assert.equal(data.memories[1]?.content, null);
    });

    it('should support synthesize mode', () => {
      const data: AgentMemoryData = {
        type: 'recall',
        mode: 'synthesize',
        memories: [
          { path: '/memory/personal/prefs.md', scope: 'personal' },
        ],
      };
      assert.equal(data.mode, 'synthesize');
      assert.equal(data.memories[0]?.content, undefined);
    });
  });

  // ---- AgentTaskData extended fields ----

  describe('AgentTaskData extended fields', () => {
    it('should accept taskType, workflowName, prompt, lastToolName', () => {
      const started: AgentTaskData = {
        type: 'task_started',
        taskId: 'task-001',
        toolUseId: 'toolu_001',
        taskType: 'agent',
        workflowName: 'code-review',
        prompt: 'Review the changes in src/types.ts',
      };
      assert.equal(started.taskType, 'agent');
      assert.equal(started.workflowName, 'code-review');
      assert.equal(started.prompt, 'Review the changes in src/types.ts');
    });

    it('should accept lastToolName for task_progress', () => {
      const progress: AgentTaskData = {
        type: 'task_progress',
        taskId: 'task-001',
        lastToolName: 'Read',
        status: 'running',
      };
      assert.equal(progress.lastToolName, 'Read');
    });

    it('should allow existing fields without new ones', () => {
      const data: AgentTaskData = {
        type: 'task_notification',
        taskId: 'task-002',
        description: 'Sub-agent progress update',
      };
      assert.equal(data.taskType, undefined);
      assert.equal(data.workflowName, undefined);
      assert.equal(data.prompt, undefined);
      assert.equal(data.lastToolName, undefined);
    });
  });

  // ---- InteractionRequestedEvent with agentContext ----

  describe('InteractionRequestedEvent with agentContext', () => {
    it('should accept agentContext on InteractionRequestedEvent', () => {
      const event: InteractionRequestedEvent = {
        interactionId: 'int-001',
        runId: 'run-001',
        sessionKey: 'session:test:user:default',
        kind: 'exec',
        command: 'rm -rf /tmp/test',
        agentContext: {
          parentToolUseId: 'toolu_sub_agent',
          agentType: 'general-purpose',
          agentName: 'Sub Agent',
        },
        createdAtMs: 1714500000000,
        expiresAtMs: 1714503600000,
      };
      assert.equal(event.agentContext?.parentToolUseId, 'toolu_sub_agent');
      assert.equal(event.agentContext?.agentType, 'general-purpose');
      assert.equal(event.agentContext?.agentName, 'Sub Agent');
    });

    it('should allow InteractionRequestedEvent without agentContext', () => {
      const event: InteractionRequestedEvent = {
        interactionId: 'int-002',
        runId: 'run-002',
        sessionKey: 'session:test:user:default',
        kind: 'ask_user',
        createdAtMs: 1714500000000,
        expiresAtMs: 1714503600000,
      };
      assert.equal(event.agentContext, undefined);
    });
  });

  // ---- NotificationEvent ----

  describe('NotificationEvent', () => {
    it('should carry required fields', () => {
      const event: NotificationEvent = {
        key: 'rate-limit-warning',
        text: 'You are approaching your rate limit',
        priority: 'high',
      };
      assert.equal(event.key, 'rate-limit-warning');
      assert.equal(event.text, 'You are approaching your rate limit');
      assert.equal(event.priority, 'high');
    });

    it('should support all optional fields', () => {
      const event: NotificationEvent = {
        key: 'compaction-done',
        text: 'Context compaction completed',
        priority: 'low',
        color: 'green',
        timeoutMs: 5000,
        sessionKey: 'session:test:user:default',
        runId: 'run-003',
      };
      assert.equal(event.color, 'green');
      assert.equal(event.timeoutMs, 5000);
      assert.equal(event.sessionKey, 'session:test:user:default');
      assert.equal(event.runId, 'run-003');
    });

    it('should accept all NotificationPriority values', () => {
      const priorities: NotificationPriority[] = [ 'low', 'medium', 'high', 'immediate' ];
      assert.equal(priorities.length, 4);
    });
  });

  // ---- PromptSuggestionEvent ----

  describe('PromptSuggestionEvent', () => {
    it('should carry text-only suggestions', () => {
      const event: PromptSuggestionEvent = {
        runId: 'run-004',
        sessionKey: 'session:test:user:default',
        suggestions: [
          { text: 'Fix the failing tests' },
          { text: 'Add error handling' },
          { text: 'Refactor the module' },
        ],
      };
      assert.equal(event.runId, 'run-004');
      assert.equal(event.sessionKey, 'session:test:user:default');
      assert.equal(event.suggestions.length, 3);
      assert.equal(event.suggestions[0]?.text, 'Fix the failing tests');
    });

    it('should allow empty suggestions array', () => {
      const event: PromptSuggestionEvent = {
        runId: 'run-005',
        sessionKey: 'session:test:user:default',
        suggestions: [],
      };
      assert.equal(event.suggestions.length, 0);
    });
  });

  // ---- Cross-type integration ----

  describe('Cross-type integration', () => {
    it('AgentToolData can carry system-relevant fields alongside tool data', () => {
      const toolData: AgentToolData = {
        type: 'result',
        toolCallId: 'tc-combined',
        toolName: 'Task',
        agentContext: {
          parentToolUseId: 'toolu_parent',
          taskId: 'task-integration',
        },
        summary: 'All sub-tasks completed',
        precedingToolUseIds: [ 'toolu_a', 'toolu_b' ],
        subagentTools: [
          {
            toolId: 'toolu_sub1',
            toolName: 'Bash',
            toolInput: { command: 'ls -la' },
            toolResult: { content: 'file list', isError: false },
            timestamp: 1714500000000,
          },
        ],
      };
      assert.equal(toolData.agentContext?.parentToolUseId, 'toolu_parent');
      assert.equal(toolData.summary, 'All sub-tasks completed');
      assert.deepEqual(toolData.precedingToolUseIds, [ 'toolu_a', 'toolu_b' ]);
      assert.equal(toolData.subagentTools?.length, 1);
    });

    it('can distinguish AgentSystemData variants by type discriminant', () => {
      const events: AgentSystemData[] = [
        { type: 'status_change', status: 'compacting' },
        { type: 'api_retry', attempt: 1, maxRetries: 3, retryDelayMs: 2000, error: 'timeout' },
        { type: 'rate_limit', status: 'allowed' },
        { type: 'compact_boundary', trigger: 'auto', preTokens: 100000, compactedTurns: 5 },
        { type: 'files_persisted', files: [], failed: [], processedAt: '2025-01-01T00:00:00Z' },
      ];
      const types = events.map(e => e.type);
      assert.deepEqual(types, [
        'status_change', 'api_retry', 'rate_limit', 'compact_boundary', 'files_persisted',
      ]);
    });
  });

});
