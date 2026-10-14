import { createHash, randomUUID } from 'node:crypto';
import type { Context } from '@deepseek-ai/cordis';
import type { Agent } from '@deepseek-ai/dsh-agent';
import type { ApprovalOutcome, ApprovalRequestEvent } from '@deepseek-ai/dsh-user-approval/types';
import type { AskUserQuestionAnswer, AskUserQuestionRequestEvent } from '@deepseek-ai/dsh-user-questions/types';
import type {} from '@deepseek-ai/dsh-user-questions';
import { asNonEmptyString, asRecord, type RequestFrame } from './protocol.js';
import type { BcnWsClient } from './ws-client.js';

export interface InteractionRun {
  runId: string;
  bcsSessionId: string;
  toolArguments: ReadonlyMap<string, unknown>;
}

interface PendingInteraction {
  run: InteractionRun;
  kind: 'exec' | 'ask_user';
  validate(params: Record<string, unknown>): unknown;
  settle(value: unknown): void;
  cancel(): void;
}

/** Agent-scoped answerers; only authenticated BCN downlink can settle a wait. */
export class BcnInteractions {
  private pending = new Map<string, PendingInteraction>();
  private completed = new Map<string, { runId: string; kind: string; resolution: string }>();

  constructor(
    private client: BcnWsClient,
    private activeRun: (agent: Agent) => InteractionRun | undefined,
  ) {}

  mount(ctx: Context, agent: Agent): void {
    ctx.on('approval/request', (req, next) => {
      const run = this.activeRun(agent);
      return run && req.agent === agent ? this.approve(run, req) : next();
    });
    ctx.on('user-questions/request', (req, next) => {
      const run = this.activeRun(agent);
      return run && req.agent === agent ? this.ask(ctx, agent, run, req) : next();
    });
  }

  cancelRun(runId: string): void {
    for (const pending of this.pending.values()) {
      if (pending.run.runId === runId) pending.cancel();
    }
  }

  dispose(): void {
    for (const pending of this.pending.values()) pending.cancel();
    this.completed.clear();
  }

  async resolve(frame: RequestFrame): Promise<void> {
    const params = asRecord(frame.params);
    const id = asNonEmptyString(params?.interactionId);
    const runId = asNonEmptyString(params?.runId);
    const kind = asNonEmptyString(params?.kind);
    if (!params || !id || !runId || !kind || !asNonEmptyString(params.idempotencyKey)) {
      this.reject(frame, 'INVALID_REQUEST', 'interaction.resolve requires runId, interactionId, kind and idempotencyKey');
      return;
    }
    const pending = this.pending.get(id);
    if (!pending) {
      const completed = this.completed.get(id);
      if (completed?.runId === runId && completed.kind === kind && completed.resolution === resolutionKey(params)) {
        this.client.sendResponse(frame.id, true, {});
      } else this.reject(frame, 'INTERACTION_NOT_FOUND', 'Unknown or already settled interaction');
      return;
    }
    if (pending.run.runId !== runId || pending.kind !== kind) {
      this.reject(frame, 'SCOPE_MISMATCH', 'Interaction does not belong to this run and kind');
      return;
    }
    try {
      const value = pending.validate(params);
      this.completed.set(id, { runId, kind, resolution: resolutionKey(params) });
      while (this.completed.size > 2_000) this.completed.delete(this.completed.keys().next().value!);
      pending.settle(value);
      this.client.sendResponse(frame.id, true, {});
    } catch {
      this.reject(frame, 'INVALID_REQUEST', 'Invalid interaction resolution');
    }
  }

  private approve(run: InteractionRun, req: ApprovalRequestEvent): Promise<ApprovalOutcome> {
    const args = req.callId ? asRecord(run.toolArguments.get(String(req.callId))) : undefined;
    const command = asNonEmptyString(args?.command);
    return this.wait(run, 'exec', {
      toolName: req.toolName,
      ...(command ? { command } : {}),
      ...(req.callId ? { toolCallId: req.callId } : {}),
      ...(req.reason ? { reason: req.reason } : {}),
      options: [
        { decision: 'allow_once', label: 'Allow once' },
        { decision: 'deny', label: 'Deny' },
      ],
    }, req.signal, params => {
      if (params.decision === 'allow_once') return 'allowed-once';
      if (params.decision === 'deny') return 'rejected';
      throw new Error('Unsupported approval decision');
    }, () => 'cancelled');
  }

  private async ask(ctx: Context, agent: Agent, run: InteractionRun, req: AskUserQuestionRequestEvent): Promise<AskUserQuestionAnswer> {
    const questions = req.questions;
    if (!questions.length || questions.length > 4 || new Set(questions.map(q => q.id)).size !== questions.length
      || questions.some(q => !q.id.trim() || !q.question.trim() || (q.options && (
        !q.options.length || q.options.length > 4 || q.options.some(o => !o.label.trim())
        || new Set(q.options.map(o => o.label)).size !== q.options.length)))) {
      throw new Error('Questions exceed the BCN interaction contract');
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let claim: AsyncIterator<{ remainingMs: number }> | undefined;
    if (req.wait?.timed) {
      claim = ctx.userQuestions.attachWait(agent, req.wait.callId, controller.signal)[Symbol.asyncIterator]();
      const first = await claim.next();
      if (first.done) throw new Error('Question wait has already ended');
      timer = setTimeout(() => controller.abort(Object.assign(new Error('Question timed out'), { code: 'ASK_TIMED_OUT' })), first.value.remainingMs);
    }
    const signal = req.signal ? AbortSignal.any([req.signal, controller.signal]) : controller.signal;
    try {
      return await this.wait(run, 'ask_user', {
        questions: questions.map(q => ({
          questionId: q.id,
          question: q.detail ? `${q.question}\n\n${q.detail}` : q.question,
          ...(q.header ? { header: q.header } : {}),
          multiSelect: q.multiSelect === true,
          ...(q.options ? { options: q.options.map(o => ({ value: o.label, label: o.label,
            ...(o.description ? { description: o.description } : {}) })), allowOther: true } : {}),
        })),
      }, signal, params => {
        if (params.action === 'cancel') throw new Error('Use cancel settlement');
        const answers = asRecord(params.answers);
        if (params.action !== 'submit' || !answers || Object.keys(answers).length !== questions.length) throw new Error('Invalid answer batch');
        return { answers: questions.map(q => {
          const values = asRecord(answers[q.id])?.values;
          if (!Array.isArray(values) || values.some(v => typeof v !== 'string' || !v.trim())
            || new Set(values).size !== values.length || (!q.multiSelect && values.length > 1)) throw new Error('Invalid answer');
          const offered = new Set(q.options?.map(o => o.label) ?? []);
          const selected: string[] = values.filter((v: string) => offered.has(v));
          const custom = values.filter((v: string) => !offered.has(v)).join('\n');
          return { id: q.id, selected, ...(custom ? { custom } : {}) };
        }) };
      }, () => { throw signal.reason ?? new Error('Question cancelled'); });
    } finally {
      clearTimeout(timer);
      controller.abort();
      await claim?.return?.();
    }
  }

  private wait<T>(
    run: InteractionRun, kind: PendingInteraction['kind'], fields: Record<string, unknown>,
    signal: AbortSignal | undefined, validate: (params: Record<string, unknown>) => T, cancelled: () => T,
  ): Promise<T> {
    if (signal?.aborted) return Promise.resolve().then(cancelled);
    const id = randomUUID();
    return new Promise<T>((resolve, reject) => {
      const finish = (value: unknown, cancelledRequest = false) => {
        if (!this.pending.delete(id)) return;
        signal?.removeEventListener('abort', cancel);
        try {
          this.client.sendEvent('interaction', {
          runId: run.runId, sessionId: run.bcsSessionId, ts: Date.now(),
          interactionId: id, kind, phase: 'resolved',
          ...(kind === 'exec' ? { decision: value === 'allowed-once' ? 'allow_once' : 'deny' }
            : { action: cancelledRequest ? 'cancel' : 'submit' }),
          });
        } catch (error) {
          reject(error);
          return;
        }
        if (cancelledRequest) {
          try { resolve(cancelled()); } catch (error) { reject(error); }
        } else resolve(value as T);
      };
      const cancel = () => finish(undefined, true);
      this.pending.set(id, {
        run, kind,
        validate: params => kind === 'ask_user' && params.action === 'cancel' ? undefined : validate(params),
        settle: value => finish(value, value === undefined),
        cancel,
      });
      signal?.addEventListener('abort', cancel, { once: true });
      try {
        this.client.sendEvent('interaction', {
          runId: run.runId, sessionId: run.bcsSessionId, ts: Date.now(),
          interactionId: id, kind, phase: 'requested', ...fields,
        });
      } catch (error) {
        this.pending.delete(id);
        signal?.removeEventListener('abort', cancel);
        reject(error);
      }
    });
  }

  private reject(frame: RequestFrame, code: string, message: string): void {
    this.client.sendResponse(frame.id, false, undefined, { code, message, retryable: false });
  }
}

function resolutionKey(params: Record<string, unknown>): string {
  return createHash('sha256').update(JSON.stringify({
    decision: params.decision, action: params.action, answers: params.answers,
  })).digest('hex');
}
