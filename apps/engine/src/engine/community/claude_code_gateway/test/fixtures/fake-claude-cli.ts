import { EventEmitter } from 'node:events';
import type {
  ClaudePromptHandlers,
  ClaudePromptResult,
  RunningClaudePrompt,
  ToolUseInfo,
} from '../../src/claude-cli-bridge.js';

/**
 * Scripted step for the fake runner — each step fires after a small microtask
 * delay so the orchestrator's listener has a chance to subscribe.
 */
export type FakeStep =
  | { kind: 'textDelta'; text: string }
  | { kind: 'thinkingDelta'; text: string }
  | { kind: 'toolStart'; id: string; name: string }
  | { kind: 'toolEnd'; id: string; name: string; input?: Record<string, unknown> }
  | { kind: 'lifecycle'; phase: 'start' | 'end' | 'error'; data?: Record<string, unknown> }
  | { kind: 'usage'; usage: { inputTokens?: number; outputTokens?: number } }
  | { kind: 'done'; text?: string; stopReason?: string; toolUses?: ToolUseInfo[]; sdkSessionId?: string }
  | { kind: 'error'; error: string; sdkSessionId?: string };

type FakeRunnerParams = {
  cwd: string;
  message: string;
  systemPrompt?: string;
  model?: string;
  permissionMode?: string;
  env?: Record<string, string>;
  additionalDirectories?: string[];
};

export type FakeController = {
  runner: (params: FakeRunnerParams, handlers?: ClaudePromptHandlers) => RunningClaudePrompt;
  /** All running prompts created by this controller, in creation order. */
  runs: Array<{ params: FakeRunnerParams; handlers?: ClaudePromptHandlers; aborted: boolean }>;
  events: EventEmitter;
};

/**
 * Build a fake runner that plays back `script` asynchronously. Tests can also
 * append more steps at runtime via the returned `enqueue` on each run.
 */
export function createFakeRunner(script: FakeStep[], opts?: { abortSdkSessionId?: string }): FakeController {
  const controller: FakeController = { runner: null as unknown as FakeController['runner'], runs: [], events: new EventEmitter() };

  controller.runner = (params, handlers) => {
    const run = { params, handlers, aborted: false };
    controller.runs.push(run);
    controller.events.emit('run', run);

    let streamedText = '';
    const toolUses: ToolUseInfo[] = [];
    let resolveFn: (result: ClaudePromptResult) => void = () => {};
    const completed = new Promise<ClaudePromptResult>(resolve => { resolveFn = resolve; });

    let cancelled = false;
    const play = async () => {
      for (const step of script) {
        await new Promise<void>(r => setImmediate(r));
        if (cancelled) {
          resolveFn({
            ok: false,
            // 真实 SDK abort 时仍会带回 sdkSessionId(R1 关键),测试通过此选项模拟。
            ...(opts?.abortSdkSessionId ? { sdkSessionId: opts.abortSdkSessionId } : {}),
            text: streamedText,
            rawEvents: [],
            toolUses,
            error: 'aborted by signal SIGTERM',
            stopReason: 'cancelled',
          });
          return;
        }
        switch (step.kind) {
          case 'textDelta':
            streamedText += step.text;
            handlers?.onTextDelta?.(streamedText, step.text);
            break;
          case 'thinkingDelta':
            handlers?.onThinkingDelta?.(step.text, step.text);
            break;
          case 'toolStart':
            handlers?.onToolStart?.({ id: step.id, name: step.name, input: {} });
            break;
          case 'toolEnd': {
            const tool: ToolUseInfo = { id: step.id, name: step.name, input: step.input ?? {} };
            toolUses.push(tool);
            handlers?.onToolEnd?.(tool);
            break;
          }
          case 'lifecycle':
            handlers?.onLifecycle?.(step.phase, step.data);
            break;
          case 'usage':
            handlers?.onUsage?.(step.usage);
            break;
          case 'done':
            resolveFn({
              ok: true,
              text: step.text ?? streamedText,
              rawEvents: [],
              toolUses: step.toolUses ?? toolUses,
              stopReason: step.stopReason ?? 'end_turn',
              sdkSessionId: step.sdkSessionId,
            });
            return;
          case 'error':
            resolveFn({
              ok: false,
              text: streamedText,
              rawEvents: [],
              toolUses,
              error: step.error,
              sdkSessionId: step.sdkSessionId,
            });
            return;
          default:
            break;
        }
      }
      resolveFn({
        ok: true,
        text: streamedText,
        rawEvents: [],
        toolUses,
        stopReason: 'end_turn',
      });
    };

    play().catch(err => {
      resolveFn({
        ok: false, text: streamedText, rawEvents: [], toolUses,
        error: err instanceof Error ? err.message : String(err),
      });
    });

    return {
      child: null as never,
      completed,
      abort: () => {
        run.aborted = true;
        cancelled = true;
      },
    };
  };

  return controller;
}
