import type { Context } from '@deepseek-ai/cordis';
import {
  installModelSelection,
  type Agent,
  type AgentHandle,
  type ModelSelection,
} from '@deepseek-ai/dsh-agent';
import { createUserMessage, type ContentBlock, type TokenUsage } from '@deepseek-ai/dsh-llm';
import type { JsonValue, Session, SessionEvent, SessionHeader, SessionId } from '@deepseek-ai/dsh-session';
import type { SessionPersistence } from '@deepseek-ai/dsh-session-persistence';
import type { Config } from './config.js';
import {
  asNonEmptyString,
  asRecord,
  extractMessageText,
  parseChatAbortParams,
  parseChatInjectParams,
  parseChatSendParams,
  type ChatAbortParams,
  type ChatEventRouting,
  type ChatInjectParams,
  type ChatSendParams,
  type PendingRouteIntent,
  type RequestFrame,
  type ResponseFrame,
  type RouteSelector,
} from './protocol.js';
import { createBcsRouteTool } from './route-tool.js';
import { dshSessionIdForV2, resolveBcnSessionIdentity } from './session-identity.js';
import {
  createBcsAssignTaskTool,
  createBcsSendTaskMessageTool,
  createBcsTaskCompleteTool,
} from './task-tools.js';
import type { BcnWsClient } from './ws-client.js';

type BcnToolProfile = 'route' | 'manager' | 'worker' | 'none';

interface ManagedAgent {
  sessionIdentity: string;
  sessionId: SessionId;
  agent: Agent;
  handle: AgentHandle;
  toolProfile: BcnToolProfile;
}

interface AgentDefaultModelService {
  currentSelection(): ModelSelection;
}

interface SystemPromptService {
  variable(name: string, provider: () => string | undefined): () => void;
}

interface AgentPresetsService {
  resolve(id?: string): Promise<{ id: string }>;
  mount(agentCtx: Context, id?: string): Promise<{ id: string }>;
}

type BcnAgentContext = Context & {
  agentDefaultModel: AgentDefaultModelService;
  agentPresets: AgentPresetsService;
  systemPrompt: SystemPromptService;
};

interface ToolResultSnapshot {
  callId: string;
  content: ContentBlock[];
  isError: boolean;
}

interface RunContext {
  runId: string;
  sessionKey: string;
  sessionIdentity: string;
  sessionId: SessionId;
  groupId: string;
  bcsSessionId?: string;
  turn?: number;
  text: string;
  stepText: Map<number, string>;
  terminal: boolean;
  usage: { input: number; output: number };
  toolNames: Map<string, string>;
  toolStarts: Set<string>;
  toolResults: Set<string>;
  pendingToolResults: Map<string, ToolResultSnapshot>;
  toolProfile: BcnToolProfile;
  route?: PendingRouteIntent;
}

export class BcnBridge {
  private managedAgents = new Map<string, Promise<ManagedAgent>>();
  private ownedAgents = new Set<AgentHandle>();
  private sessionIdentityBySessionId = new Map<string, string>();
  private runIdByMessageId = new Map<string, string>();
  private runIdByTurn = new Map<string, string>();
  private activeRunBySessionId = new Map<string, string>();
  private runs = new Map<string, RunContext>();
  private completedRunIds: string[] = [];
  private disposers: Array<() => unknown> = [];
  private disposed = false;

  constructor(
    private readonly ctx: Context,
    private readonly client: BcnWsClient,
    private readonly config: Config,
    private readonly log?: {
      info(message: string): void;
      warn(message: string): void;
      error(message: string): void;
      debug?(message: string): void;
    },
  ) {}

  start(): void {
    this.disposers.push(
      this.client.onRequest('chat.send', frame => this.handleChatSend(frame)),
      this.client.onRequest('chat.inject', frame => this.handleChatInject(frame)),
      this.client.onRequest('chat.abort', frame => this.handleChatAbortRequest(frame)),
      this.client.onEvent('chat.abort', frame => this.handleChatAbortEvent(frame.payload)),
      this.ctx.on('agent/inbox/claimed', payload => this.handleInboxClaimed(payload)),
      this.ctx.on('agent/inbox/discarded', payload => this.handleInboxDiscarded(payload)),
      this.ctx.on('session/event', (session, event) => this.handleSessionEvent(session, event)),
    );
  }

  get busy(): boolean {
    for (const run of this.runs.values()) if (!run.terminal) return true;
    return false;
  }

  async dispose(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    for (const dispose of this.disposers.splice(0).reverse()) await dispose();
    for (const run of this.runs.values()) {
      if (run.terminal) continue;
      const agent = this.ctx.agents.get(run.sessionId);
      agent?.cancel({ kind: 'disposed' });
    }
    await Promise.allSettled([...this.ownedAgents].map(handle => handle.dispose()));
    this.ownedAgents.clear();
    this.managedAgents.clear();
    this.sessionIdentityBySessionId.clear();
    this.runIdByMessageId.clear();
    this.runIdByTurn.clear();
    this.activeRunBySessionId.clear();
    this.runs.clear();
  }

  async captureRoute(
    agent: Agent | undefined,
    selectors: RouteSelector[],
    reason: string,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    if (!agent) return { ok: false, error: 'NO_AGENT', message: 'No active DSH agent owns this call.' };
    const runId = this.activeRunBySessionId.get(String(agent.id));
    const run = runId ? this.runs.get(runId) : undefined;
    if (!run || run.terminal) {
      return { ok: false, error: 'NO_BCN_RUN', message: 'No active BCN run owns this call.' };
    }
    if (run.toolProfile !== 'route') {
      return { ok: false, error: 'TOOL_NOT_ALLOWED', message: 'bcs_route is not available in this BCN session.' };
    }
    if (!Array.isArray(selectors) || selectors.length === 0 || selectors.length > 20) {
      return { ok: false, error: 'INVALID_PARAMS', message: 'to must contain 1-20 selectors.' };
    }
    const normalized: RouteSelector[] = [];
    for (const selector of selectors) {
      const value = selector.value.trim();
      if ((selector.type !== 'name' && selector.type !== 'bot') || !value) {
        return { ok: false, error: 'INVALID_PARAMS', message: 'Each selector needs type name|bot and a value.' };
      }
      normalized.push({ type: selector.type, value });
    }
    const normalizedReason = reason.trim();
    if (!normalizedReason) return { ok: false, error: 'INVALID_PARAMS', message: 'reason must not be empty.' };

    const params: Record<string, unknown> = { group_id: run.groupId, selectors: normalized };
    if (run.bcsSessionId) params.session_id = run.bcsSessionId;
    const response = await this.client.sendRequest('route.resolve', params, 10_000, signal);
    if (!response.ok) {
      return {
        ok: false,
        error: response.error?.code ?? 'ROUTE_RESOLVE_FAILED',
        message: response.error?.message ?? 'BCN could not resolve route targets.',
      };
    }
    const payload = asRecord(response.payload);
    const resolved = normalizeResolvedRouteTargets(payload?.resolved);
    if (payload?.ok !== true || resolved.length === 0) {
      return {
        ok: false,
        error: asNonEmptyString(payload?.error) ?? 'ROUTE_TARGET_INVALID',
        message: asNonEmptyString(payload?.message) ?? 'BCN could not resolve route targets.',
        ...normalizeRouteCandidates(payload?.candidates),
      };
    }

    const incoming: PendingRouteIntent = {
      responders: resolved,
      mode: 'required',
      reason: normalizedReason.slice(0, 500),
      includeSelf: false,
    };
    run.route = mergeRouteIntent(run.route, incoming);
    return { ok: true, captured: true, display_to_user: false, resolved };
  }

  async assignTask(
    agent: Agent | undefined,
    targetBot: string,
    message: string,
    responseMode: string | undefined,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    const run = this.activeRunForTaskTool(agent, 'manager');
    if (!run) return taskToolUnavailable();
    const normalizedTarget = targetBot.trim();
    const normalizedMessage = message.trim();
    if (!normalizedTarget || !normalizedMessage) {
      return taskToolInvalid("'target_bot' and 'message' must be non-empty strings.");
    }
    if (responseMode !== undefined && responseMode !== 'after-last-tool-call' && responseMode !== 'full') {
      return taskToolInvalid("'response_mode' must be 'after-last-tool-call' or 'full'.");
    }

    const params: Record<string, unknown> = {
      group_id: run.groupId,
      target_bot: normalizedTarget,
      message: normalizedMessage,
      ...(responseMode ? { response_mode: responseMode } : {}),
    };
    const response = await this.sendTaskRequest('task.dispatch', params, signal);
    if (!response.ok) return taskRequestFailure(response, 'TASK_DISPATCH_FAILED');
    const payload = asRecord(response.payload);
    const taskId = asNonEmptyString(payload?.task_id);
    if (!taskId) return taskToolInvalidResponse('task.dispatch');
    return {
      ok: true,
      task_id: taskId,
      status: asNonEmptyString(payload?.status) ?? 'dispatched',
    };
  }

  async sendTaskMessage(
    agent: Agent | undefined,
    message: string,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    const run = this.activeRunForTaskTool(agent, 'worker');
    if (!run) return taskToolUnavailable();
    const normalizedMessage = message.trim();
    if (!normalizedMessage) return taskToolInvalid("'message' must be a non-empty string.");

    const response = await this.sendTaskRequest('task.message', {
      group_id: run.groupId,
      message: normalizedMessage,
    }, signal);
    if (!response.ok) return taskRequestFailure(response, 'TASK_MESSAGE_FAILED');
    const payload = asRecord(response.payload);
    return { ok: true, status: asNonEmptyString(payload?.status) ?? 'sent' };
  }

  async completeTask(
    agent: Agent | undefined,
    summary: string,
    signal: AbortSignal,
  ): Promise<JsonValue> {
    const run = this.activeRunForTaskTool(agent, 'manager');
    if (!run) return taskToolUnavailable();
    const normalizedSummary = summary.trim();
    if (!normalizedSummary) return taskToolInvalid("'summary' must be a non-empty string.");

    const response = await this.sendTaskRequest('task.complete', {
      group_id: run.groupId,
      summary: normalizedSummary,
    }, signal);
    if (!response.ok) return taskRequestFailure(response, 'TASK_COMPLETE_FAILED');
    return { ok: true };
  }

  private async handleChatSend(frame: RequestFrame): Promise<void> {
    let params: ChatSendParams;
    try {
      params = parseChatSendParams(frame.params);
    } catch (error) {
      this.client.sendResponse(frame.id, false, undefined, invalidRequest(error));
      return;
    }
    const runId = params.idempotency_key ?? frame.id;
    const existing = this.runs.get(runId);
    if (existing) {
      this.client.sendResponse(frame.id, true, { run_id: existing.runId });
      return;
    }

    const sessionIdentity = resolveBcnSessionIdentity({
      sessionKey: params.session_key,
      groupId: params.bcs_group_id,
      ...(params.bcs_session_id ? { bcsSessionId: params.bcs_session_id } : {}),
    });
    const sessionId = dshSessionIdForV2(sessionIdentity);
    const toolProfile = resolveToolProfile(params.session_context);
    const run: RunContext = {
      runId,
      sessionKey: params.session_key,
      sessionIdentity,
      sessionId,
      groupId: params.bcs_group_id,
      ...(params.bcs_session_id ? { bcsSessionId: params.bcs_session_id } : {}),
      text: '',
      stepText: new Map(),
      terminal: false,
      usage: { input: 0, output: 0 },
      toolNames: new Map(),
      toolStarts: new Set(),
      toolResults: new Set(),
      pendingToolResults: new Map(),
      toolProfile,
    };
    this.runs.set(runId, run);
    this.client.sendResponse(frame.id, true, { run_id: runId });

    try {
      const managed = await this.getOrCreateAgent(sessionIdentity, toolProfile);
      const message = createUserMessage({
        content: [{ type: 'text', text: formatInboundMessage(params) }],
        source: { kind: 'plugin', plugin: 'deepseek-harness-channel-bcn', form: 'relay' },
      });
      this.runIdByMessageId.set(String(message.id), runId);
      managed.agent.followup(message);
    } catch {
      this.sendTerminal(run, 'error', 'DELIVERY_FAILED');
    }
  }

  private async handleChatInject(frame: RequestFrame): Promise<void> {
    let params: ChatInjectParams;
    try {
      params = parseChatInjectParams(frame.params);
    } catch (error) {
      this.client.sendResponse(frame.id, false, undefined, invalidRequest(error));
      return;
    }
    try {
      const sessionIdentity = resolveBcnSessionIdentity({
        sessionKey: params.session_key,
        groupId: params.bcs_group_id,
        ...(params.bcs_session_id ? { bcsSessionId: params.bcs_session_id } : {}),
      });
      const managed = await this.getOrCreateAgent(
        sessionIdentity,
        resolveToolProfile(params.session_context),
      );
      managed.agent.inject(createUserMessage({
        content: [{ type: 'text', text: formatInboundMessage(params) }],
        source: { kind: 'plugin', plugin: 'deepseek-harness-channel-bcn', form: 'relay' },
      }));
      this.client.sendResponse(frame.id, true, {});
    } catch {
      this.client.sendResponse(frame.id, false, undefined, {
        code: 'DELIVERY_FAILED',
        message: 'DeepSeek Harness could not inject the BCN message',
        retryable: true,
      });
    }
  }

  private async handleChatAbortRequest(frame: RequestFrame): Promise<void> {
    let params: ChatAbortParams;
    try {
      params = parseChatAbortParams(frame.params);
    } catch (error) {
      this.client.sendResponse(frame.id, false, undefined, invalidRequest(error));
      return;
    }
    const aborted = this.abortRun(params);
    this.client.sendResponse(frame.id, true, { aborted });
  }

  private async handleChatAbortEvent(payload: Record<string, unknown>): Promise<void> {
    try {
      this.abortRun(parseChatAbortParams(payload));
    } catch {
      this.log?.warn('Ignoring invalid BCN chat.abort event');
    }
  }

  private abortRun(params: ChatAbortParams): boolean {
    let run: RunContext | undefined;
    if (params.run_id) {
      run = this.runs.get(params.run_id);
    } else {
      const candidates = [...this.runs.values()].filter(
        item => runMatchesSession(item, params.session_key) && !item.terminal,
      );
      // COSEC: a V2 group-level session_key may match multiple isolated
      // conversations. Never cancel an arbitrary run when the scope is ambiguous.
      if (candidates.length !== 1) return false;
      run = candidates[0];
    }
    if (!run || run.terminal || !runMatchesSession(run, params.session_key)) return false;
    const agent = this.ctx.agents.get(run.sessionId);
    if (!agent) {
      this.sendTerminal(run, 'aborted');
      return true;
    }
    agent.cancel({ kind: 'user' });
    this.sendTerminal(run, 'aborted');
    return true;
  }

  private handleInboxClaimed(payload: { agent: Agent; message: { id: unknown }; turn: number }): void {
    const runId = this.runIdByMessageId.get(String(payload.message.id));
    if (!runId) return;
    const run = this.runs.get(runId);
    if (!run || run.sessionId !== payload.agent.id) return;
    this.runIdByMessageId.delete(String(payload.message.id));
    run.turn = payload.turn;
    this.runIdByTurn.set(turnKey(payload.agent.id, payload.turn), runId);
    this.activeRunBySessionId.set(String(payload.agent.id), runId);
  }

  private handleInboxDiscarded(payload: { agent: Agent; message: { id: unknown } }): void {
    const messageId = String(payload.message.id);
    const runId = this.runIdByMessageId.get(messageId);
    if (!runId) return;
    this.runIdByMessageId.delete(messageId);
    const run = this.runs.get(runId);
    if (run) this.sendTerminal(run, 'aborted');
  }

  private handleSessionEvent(session: Session, event: SessionEvent): void {
    if (this.disposed || !this.sessionIdentityBySessionId.has(String(session.id))) return;
    const eventData = asRecord(event.data);
    const turn = typeof eventData?.turn === 'number' ? eventData.turn : undefined;
    if (turn === undefined) return;
    const runId = this.runIdByTurn.get(turnKey(session.id, turn));
    const run = runId ? this.runs.get(runId) : undefined;
    if (!run || run.terminal) return;

    if (event.type === 'assistant/chunk') {
      const chunk = event.data.chunk;
      if (chunk.type !== 'text-delta' || !chunk.text) return;
      this.appendDelta(run, event.data.step, chunk.text);
      return;
    }
    if (event.type === 'assistant/message') {
      this.captureAssistantMessage(run, event.data.step, event.data.message.content, event.data.usage);
      return;
    }
    if (event.type === 'tool/call') {
      this.captureToolCall(run, String(event.data.callId), event.data.name, event.data.arguments);
      return;
    }
    if (event.type === 'tool/result') {
      const block = event.data.message.content[0];
      this.captureToolResult(run, {
        callId: String(block.toolCallId),
        content: block.content,
        isError: block.isError === true || event.data.error !== undefined,
      });
      return;
    }
    if (event.type === 'turn/end') {
      if (event.data.reason.kind === 'aborted') this.sendTerminal(run, 'aborted');
      else if (event.data.reason.kind === 'error' || event.data.reason.kind === 'interrupted') {
        const code = event.data.reason.kind === 'error'
          ? safeErrorCode(event.data.reason.error.code)
          : 'INTERRUPTED';
        this.sendTerminal(run, 'error', code);
      } else {
        this.sendTerminal(run, 'final', undefined, event.data.reason.kind);
      }
    }
  }

  private appendDelta(run: RunContext, step: number, text: string): void {
    run.text += text;
    run.stepText.set(step, `${run.stepText.get(step) ?? ''}${text}`);
    this.client.sendEvent('chat.event', {
      run_id: run.runId,
      bcs_group_id: run.groupId,
      state: 'delta',
      delta_text: text,
    });
  }

  private captureAssistantMessage(
    run: RunContext,
    step: number,
    content: ContentBlock[],
    usage: TokenUsage | undefined,
  ): void {
    if (usage) {
      run.usage.input += usage.inputTokens + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
      run.usage.output += usage.outputTokens;
    }
    const assembled = visibleText(content);
    const streamed = run.stepText.get(step) ?? '';
    if (!assembled || assembled === streamed) return;
    if (assembled.startsWith(streamed)) {
      this.appendDelta(run, step, assembled.slice(streamed.length));
    } else if (!streamed) {
      this.appendDelta(run, step, assembled);
    }
  }

  private captureToolCall(run: RunContext, callId: string, name: string, rawArguments: string): void {
    if (run.toolStarts.has(callId)) return;
    run.toolNames.set(callId, name);
    let args: unknown = rawArguments;
    try {
      args = JSON.parse(rawArguments);
    } catch {
      // Preserve the provider's raw argument string when it is not complete JSON.
    }
    this.client.sendEvent('agent', {
      run_id: run.runId,
      bcs_group_id: run.groupId,
      stream: 'tool',
      ts: Date.now(),
      data: { phase: 'start', toolCallId: callId, name, args },
    });
    run.toolStarts.add(callId);
    const pending = run.pendingToolResults.get(callId);
    if (pending) {
      this.sendToolResult(run, pending);
      run.pendingToolResults.delete(callId);
    }
  }

  private captureToolResult(run: RunContext, result: ToolResultSnapshot): void {
    if (run.toolResults.has(result.callId)) return;
    if (!run.toolNames.has(result.callId)) {
      run.pendingToolResults.set(result.callId, result);
      return;
    }
    this.sendToolResult(run, result);
  }

  private sendToolResult(run: RunContext, result: ToolResultSnapshot): void {
    if (run.toolResults.has(result.callId)) return;
    this.client.sendEvent('agent', {
      run_id: run.runId,
      bcs_group_id: run.groupId,
      stream: 'tool',
      ts: Date.now(),
      data: {
        phase: 'result',
        toolCallId: result.callId,
        name: run.toolNames.get(result.callId) ?? 'unknown',
        result: { content: result.content },
        isError: result.isError,
      },
    });
    run.toolResults.add(result.callId);
  }

  private sendTerminal(
    run: RunContext,
    state: 'final' | 'error' | 'aborted',
    errorCode?: string,
    stopReason?: string,
  ): void {
    if (run.terminal) return;
    for (const pending of run.pendingToolResults.values()) this.sendToolResult(run, pending);
    run.pendingToolResults.clear();

    const payload: Record<string, unknown> = {
      run_id: run.runId,
      bcs_group_id: run.groupId,
      state,
    };
    if (state === 'final') {
      if (run.text) payload.message = assistantMessage(run.text);
      if (run.usage.input || run.usage.output) payload.usage = run.usage;
      if (stopReason) payload.stop_reason = stopReason;
      if (run.route) payload.routing = routeWire(run.route);
    } else if (state === 'error') {
      payload.errorMessage = 'DeepSeek Harness run failed';
      payload.errorKind = 'dsh_turn';
      payload.errorCode = errorCode ?? 'UNKNOWN';
    } else {
      payload.stop_reason = 'aborted';
    }
    this.client.sendEvent('chat.event', payload);
    run.terminal = true;
    this.activeRunBySessionId.delete(String(run.sessionId));
    if (run.turn !== undefined) this.runIdByTurn.delete(turnKey(run.sessionId, run.turn));
    this.completedRunIds.push(run.runId);
    while (this.completedRunIds.length > 2_000) {
      const oldest = this.completedRunIds.shift();
      if (oldest) this.runs.delete(oldest);
    }
  }

  private async getOrCreateAgent(sessionIdentity: string, toolProfile: BcnToolProfile): Promise<ManagedAgent> {
    const existing = this.managedAgents.get(sessionIdentity);
    if (existing) {
      const managed = await existing;
      if (managed.toolProfile !== toolProfile) {
        throw new Error('BCN session coordination role changed after its DSH agent was created');
      }
      return managed;
    }
    const pending = this.createAgent(sessionIdentity, toolProfile).catch(error => {
      this.managedAgents.delete(sessionIdentity);
      throw error;
    });
    this.managedAgents.set(sessionIdentity, pending);
    return pending;
  }

  private async createAgent(sessionIdentity: string, toolProfile: BcnToolProfile): Promise<ManagedAgent> {
    const sessionId = dshSessionIdForV2(sessionIdentity);
    if (this.ctx.agents.get(sessionId)) {
      throw new Error('A non-BCN DSH agent already owns the derived BCN session id');
    }
    const persistedHeader = (await this.ctx.sessionPersistence.list()).find(header => header.id === sessionId);
    const cwd = process.cwd();
    const bcnCtx = this.ctx as BcnAgentContext;
    const defaultSelection = bcnCtx.agentDefaultModel.currentSelection();
    const recordedPreset = persistedHeader
      ? presetForInspection(await this.ctx.sessionPersistence.inspect(sessionId))
      : undefined;
    const presetId = (await bcnCtx.agentPresets.resolve(recordedPreset)).id;
    const setup = async (agentCtx: Context) => {
      installModelSelection(agentCtx, {
        current: selectionForSession(agentCtx.agent, defaultSelection),
        assembled: undefined,
      });
      (agentCtx as BcnAgentContext).systemPrompt.variable(
        'cwd',
        () => agentCtx.agent?.session.header.cwd ?? cwd,
      );
      await bcnCtx.agentPresets.mount(agentCtx, presetId);
      if (toolProfile === 'route') {
        agentCtx.tools.register(createBcsRouteTool(this));
      } else if (toolProfile === 'manager') {
        agentCtx.tools.register(createBcsAssignTaskTool(this));
        agentCtx.tools.register(createBcsTaskCompleteTool(this));
      } else if (toolProfile === 'worker') {
        agentCtx.tools.register(createBcsSendTaskMessageTool(this));
      }
    };
    const agentOptions = {
      provider: defaultSelection.provider,
      model: defaultSelection.model,
    };
    const handle = persistedHeader
      ? await this.ctx.agents.resume({ resumeSessionId: sessionId, agentOptions, setup })
      : await this.ctx.agents.create({
        sessionId,
        meta: { cwd, agentPreset: presetId },
        agentOptions,
        setup,
      });
    const managed = { sessionIdentity, sessionId, agent: handle.agent, handle, toolProfile };
    this.ownedAgents.add(handle);
    this.sessionIdentityBySessionId.set(String(sessionId), sessionIdentity);
    return managed;
  }

  private activeRunForTaskTool(
    agent: Agent | undefined,
    requiredProfile: 'manager' | 'worker',
  ): RunContext | undefined {
    if (!agent) return undefined;
    const runId = this.activeRunBySessionId.get(String(agent.id));
    const run = runId ? this.runs.get(runId) : undefined;
    return run && !run.terminal && run.toolProfile === requiredProfile ? run : undefined;
  }

  private async sendTaskRequest(
    method: 'task.dispatch' | 'task.message' | 'task.complete',
    params: Record<string, unknown>,
    signal: AbortSignal,
  ): Promise<ResponseFrame> {
    if (!this.client.connected) {
      return {
        type: 'res',
        id: 'local-not-connected',
        ok: false,
        error: {
          code: 'BCN_NOT_CONNECTED',
          message: 'BCN WebSocket is not connected.',
          retryable: true,
        },
      };
    }
    try {
      return await this.client.sendRequest(method, params, 30_000, signal);
    } catch {
      return {
        type: 'res',
        id: 'local-request-failed',
        ok: false,
        error: {
          code: 'BCN_REQUEST_FAILED',
          message: 'BCN task request failed.',
          retryable: true,
        },
      };
    }
  }
}

function presetForInspection(inspection: {
  meta: SessionHeader;
  events: readonly SessionEvent[];
}): string | undefined {
  for (let index = inspection.events.length - 1; index >= 0; index -= 1) {
    const event = asRecord(inspection.events[index]);
    if (event?.type !== 'agent-preset/selected') continue;
    const selected = asNonEmptyString(asRecord(event.data)?.agentPreset);
    if (selected) return selected;
  }
  return inspection.meta.agentPreset;
}

function selectionForSession(agent: Agent | undefined, fallback: ModelSelection): ModelSelection {
  const recorded = agent?.session.requestHeader()?.config;
  if (!recorded) return fallback;
  return {
    provider: recorded.provider,
    model: recorded.model,
    ...(recorded.reasoningEffort ? { reasoningEffort: recorded.reasoningEffort } : {}),
  };
}

function resolveToolProfile(sessionContext: Record<string, unknown>): BcnToolProfile {
  const groupType = asNonEmptyString(sessionContext.group_type);
  if (groupType === 'manager_worker') {
    // COSEC: task-tool authority comes only from the authenticated BCN
    // downlink context. Missing or unknown roles fail closed.
    const recipientRole = asNonEmptyString(sessionContext.recipient_role);
    if (recipientRole === 'manager') return 'manager';
    if (recipientRole === 'worker') return 'worker';
    return 'none';
  }
  return asNonEmptyString(sessionContext.routing_mode) === 'mention' ? 'none' : 'route';
}

function formatInboundMessage(params: ChatSendParams | ChatInjectParams): string {
  const text = extractMessageText(params.message);
  const metadata = {
    protocol_version: 2,
    bcs_group_id: params.bcs_group_id,
    ...(params.bcs_session_id ? { bcs_session_id: params.bcs_session_id } : {}),
    channel: params.channel,
    group_context: params.session_context,
    ...(params.tags?.length ? { tags: params.tags } : {}),
    ...(params.attachments?.length ? { attachments: params.attachments } : {}),
  };
  return `<bcn_context>\n${JSON.stringify(metadata)}\n</bcn_context>\n\n${text}`;
}

function assistantMessage(text: string): Record<string, unknown> {
  return { role: 'assistant', content: [{ type: 'text', text }], timestamp: Date.now() };
}

function visibleText(content: ContentBlock[]): string {
  return content.flatMap(block => block.type === 'text' ? [block.text] : []).join('');
}

function turnKey(sessionId: SessionId, turn: number): string {
  return `${String(sessionId)}:${turn}`;
}

function runMatchesSession(run: RunContext, value: string): boolean {
  return run.sessionKey === value || run.sessionIdentity === value;
}

function invalidRequest(error: unknown): { code: string; message: string; retryable: boolean } {
  return {
    code: 'INVALID_REQUEST',
    message: error instanceof Error ? error.message : 'Invalid BCN request',
    retryable: false,
  };
}

function safeErrorCode(value: unknown): string {
  const code = asNonEmptyString(value);
  return code && /^[A-Za-z0-9_.-]{1,80}$/.test(code) ? code : 'UNKNOWN';
}

function taskToolUnavailable(): JsonValue {
  return {
    ok: false,
    error: 'TOOL_NOT_ALLOWED',
    message: 'This BCN task tool is not available for the active session role.',
  };
}

function taskToolInvalid(message: string): JsonValue {
  return { ok: false, error: 'INVALID_PARAMS', message };
}

function taskToolInvalidResponse(method: string): JsonValue {
  return {
    ok: false,
    error: 'INVALID_RESPONSE',
    message: `BCN returned an invalid ${method} response.`,
  };
}

function taskRequestFailure(
  response: Awaited<ReturnType<BcnWsClient['sendRequest']>>,
  fallbackCode: string,
): JsonValue {
  return {
    ok: false,
    error: response.error?.code ?? fallbackCode,
    message: response.error?.message ?? 'BCN rejected the task request.',
  };
}

function normalizeResolvedRouteTargets(value: unknown): Array<{ type: 'bot'; value: string }> {
  if (!Array.isArray(value)) return [];
  const targets: Array<{ type: 'bot'; value: string }> = [];
  for (const item of value) {
    const record = asRecord(item);
    const target = asNonEmptyString(record?.value);
    if (record?.type === 'bot' && target) targets.push({ type: 'bot', value: target });
  }
  return targets;
}

function normalizeRouteCandidates(value: unknown): { candidates?: JsonValue[] } {
  if (!Array.isArray(value)) return {};
  const candidates: JsonValue[] = [];
  for (const item of value) {
    const record = asRecord(item);
    const botUuid = asNonEmptyString(record?.bot_uuid);
    if (!botUuid) continue;
    const botName = asNonEmptyString(record?.bot_name);
    const role = asNonEmptyString(record?.role);
    candidates.push({
      bot_uuid: botUuid,
      ...(botName ? { bot_name: botName } : {}),
      ...(role ? { role } : {}),
    });
  }
  return candidates.length ? { candidates } : {};
}

function mergeRouteIntent(
  existing: PendingRouteIntent | undefined,
  incoming: PendingRouteIntent,
): PendingRouteIntent {
  if (!existing) return incoming;
  const keys = new Set(existing.responders.map(item => `${item.type}:${item.value ?? ''}`));
  for (const responder of incoming.responders) {
    if (existing.responders.length >= 20) break;
    const key = `${responder.type}:${responder.value ?? ''}`;
    if (!keys.has(key)) existing.responders.push(responder);
    keys.add(key);
  }
  existing.reason = `${existing.reason}; ${incoming.reason}`.slice(0, 500);
  return existing;
}

function routeWire(intent: PendingRouteIntent): ChatEventRouting {
  return {
    responders: intent.responders,
    mode: intent.mode,
    reason: intent.reason,
    include_self: intent.includeSelf,
    ...(intent.dedupeKey ? { dedupe_key: intent.dedupeKey } : {}),
  };
}
