/**
 * TeClawProvider — WebSocket client for TeClaw Channel 2 (/ws/v1/chat).
 *
 * Establishes a persistent WebSocket connection to TeClaw's /ws/v1/chat endpoint.
 * Performs connect handshake (protocol negotiation + auth), then provides:
 * - Agent Loop execution via chat.send req frame
 * - ChatInject messaging via chat.inject req frame
 * - Job cancellation via chat.abort req frame
 * - Approval resolution via exec.approval.resolve req frame
 *
 * Replaces the previous HTTP API implementation (POST /api/chat/*).
 *
 * @module platform/teclaw-provider
 */

import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import type {
  TeClawWsProviderConfig,
  WsReqFrame,
  WsResFrame,
  WsEventFrame,
  ConnectPayload,
  ConnectClientInfo,
  HelloOkPayload,
  ChatEventPayload,
} from "./teclaw-ws-types.js";
import {
  validateConnectPayload,
  isWsResFrame,
  isWsEventFrame,
  isChatFinalEvent,
  isChatErrorEvent,
  isChatAbortedEvent,
  isTickEvent,
} from "./teclaw-ws-types.js";
import type {
  TeClawChatInjectContext,
  TeClawChatInjectResponse,
} from "./teclaw-types.js";

// ── Dependencies (injectable for testing) ──

/** Injectable dependencies for TeClawProvider (allows test mocking). */
export interface TeClawProviderDeps {
  /** Custom WebSocket constructor (defaults to global WebSocket or ws package). */
  createWebSocket?: (url: string, protocols?: string[], options?: { headers?: Record<string, string> }) => WebSocketLike;
  /** Custom timer for testing. */
  setTimeout?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  /** Custom clearTimeout for testing. */
  clearTimeout?: (id: ReturnType<typeof setTimeout>) => void;
  /** @deprecated Delta progress throttling was removed; retained for source compatibility. */
  now?: () => number;
  /** Custom fetch for createSession() HTTP requests (defaults to global fetch). */
  fetch?: (url: string, init: RequestInit) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;
}

/** Minimal WebSocket interface for testability. */
export interface WebSocketLike {
  onopen: (() => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: ((ev: { message?: string }) => void) | null;
  send(data: string): void;
  close(): void;
  readyState: number;
}

// ── Request ID Counter ──

let reqIdCounter = 0;
function nextReqId(): string {
  return String(++reqIdCounter);
}

export interface TeClawAgentProgressEvent {
  text: string;
  seq?: number;
}

function extractChatMessageText(
  payload: ChatEventPayload,
  deltaBuffer: string,
): string {
  const messageText = payload.message?.content
    ?.filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => (block.text as string).trim())
    .filter((text) => text.length > 0)
    .join("\n") ?? "";

  if (messageText) return messageText;

  const topLevelContent = typeof payload.content === "string"
    ? payload.content.trim()
    : "";
  if (topLevelContent) return topLevelContent;

  return deltaBuffer.trim();
}

// ── TeClawProvider ──

/**
 * WebSocket client for TeClaw Channel 2.
 *
 * Provides Agent Loop execution via chat.send, ChatInject via chat.inject,
 * cancellation via chat.abort, and approval resolution via exec.approval.resolve.
 */
export class TeClawProvider {
  public readonly wsUrl: string;
  public readonly token: string;
  public headers: Record<string, string>;
  public readonly connectTimeoutMs: number;
  public sessionKey: string;
  /** HTTP base URL for REST API calls (session creation etc.). Derived from wsUrl if not provided. */
  public readonly httpBaseUrl: string;

  private ws: WebSocketLike | null = null;
  private helloOk: HelloOkPayload | null = null;
  private pendingRequests: Map<string, {
    resolve: (payload: Record<string, unknown>) => void;
    reject: (error: Error) => void;
  }> = new Map();
  private eventListeners: Map<string, Array<(payload: Record<string, unknown>) => void>> = new Map();
  private _connected = false;
  /**
   * In-flight connect promise (single-flight guard). When set, concurrent
   * callers of connect() share the SAME connect attempt instead of each
   * opening their own WebSocket. Without this, N callers seeing `!connected`
   * race to open N sockets, and their overlapping onclose handlers clobber
   * `this.ws` — the connection ends up null and never recovers.
   */
  private _connectingPromise: Promise<HelloOkPayload> | null = null;
  private readonly clientInfo: ConnectClientInfo;
  private readonly deps: TeClawProviderDeps;
  private readonly _setTimeout: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  private readonly _clearTimeout: (id: ReturnType<typeof setTimeout>) => void;
  private _defaultSessionKey: string;

  /** The default session key used for chat.send/chat.inject when no explicit key is provided. */
  public get defaultSessionKey(): string { return this._defaultSessionKey; }

  /**
   * Override the default session key (e.g., with the real teclaw session_key
   * from x-teclaw-session-key HTTP header). Call this after construction
   * when per-request context becomes available.
   */
  public setSessionKey(sk: string): void {
    console.error(`[teclaw:provider] setSessionKey: ${this._defaultSessionKey.slice(0, 50)} → ${sk.slice(0, 50)}`);
    this._defaultSessionKey = sk;
    this.sessionKey = sk;
  }

  constructor(config: TeClawWsProviderConfig, deps?: TeClawProviderDeps) {
    if (!config.wsUrl) {
      throw new Error("TeClawProvider requires wsUrl");
    }
    if (!config.token) {
      throw new Error("TeClawProvider requires token");
    }

    this.wsUrl = config.wsUrl;
    this.token = config.token;
    this.headers = config.headers ?? {};
    this.connectTimeoutMs = config.connectTimeoutMs ?? 10_000;
    this.clientInfo = config.client ?? { id: "clawmind", version: "0.1.0", platform: "openclaw", mode: "agent-loop" };
    this._defaultSessionKey = config.sessionKey ?? `clawmind-${Date.now()}`;
    this.sessionKey = this._defaultSessionKey;
    this.deps = deps ?? {};
    this._setTimeout = this.deps.setTimeout ?? setTimeout.bind(globalThis);
    this._clearTimeout = this.deps.clearTimeout ?? clearTimeout.bind(globalThis);

    // Derive HTTP base URL from config or wsUrl
    if (config.httpBaseUrl) {
      this.httpBaseUrl = config.httpBaseUrl;
    } else {
      // Derive: ws://host:port/ws/v1/chat → http://host:port
      const parsed = new URL(this.wsUrl);
      const scheme = parsed.protocol === "wss:" ? "https:" : "http:";
      this.httpBaseUrl = `${scheme}//${parsed.host}`;
    }
  }

  get connected(): boolean {
    return this._connected && this.ws !== null && this.ws.readyState === 1;
  }

  /**
   * Build diagnostic metadata for error returns.
   * Captures full TeClaw connection/session/routing state so that
   * downstream consumers (mcp-agent-runner → embedded-agent executor →
   * controller → result_json) can diagnose WS/chat failures without
   * needing to correlate server-side logs.
   */
  private _buildDiagnosticMeta(extra?: Record<string, unknown>): Record<string, unknown> {
    const botId = this.headers["x-target-bot-id"];
    const targetService = this.headers["x-andc-target-service"];
    const traceId = this.headers["x-tracer-traceid"];
    return {
      teclawDiagnostic: {
        connected: this.connected,
        wsReadyState: this.ws?.readyState ?? null, // 0=CONNECTING, 1=OPEN, 2=CLOSING, 3=CLOSED
        wsUrl: this.wsUrl,
        httpBaseUrl: this.httpBaseUrl,
        sessionKey: this.sessionKey?.slice(0, 80) ?? null,
        defaultSessionKey: this._defaultSessionKey?.slice(0, 80) ?? null,
        isValidSessionKey: this.isValidTeClawSessionKey(this.sessionKey),
        createdSessions: [...this._createdSessions],
        headers: {
          "x-target-bot-id": botId ?? null,
          "x-andc-target-service": targetService ?? null,
          "x-tracer-traceid": traceId ?? null,
        },
        helloOk: this.helloOk ? { protocol: (this.helloOk as unknown as Record<string, unknown>).protocol ?? null, serverVersion: (this.helloOk as unknown as Record<string, unknown>).serverVersion ?? null } : null,
        ...extra,
      },
    };
  }

  // ── Connection Management ──

  /**
   * Connect to the WebSocket server and perform handshake.
   *
   * 1. Open WS connection
   * 2. Send connect req frame with protocol + auth
   * 3. Wait for hello-ok res
   * 4. Mark as connected
   */
  async connect(): Promise<HelloOkPayload> {
    if (this.connected && this.helloOk) {
      return this.helloOk;
    }
    // Single-flight: share one in-flight connect across all concurrent callers.
    if (this._connectingPromise) {
      return this._connectingPromise;
    }

    const createWs = this.deps.createWebSocket ?? this.defaultCreateWebSocket.bind(this);
    const ws = createWs(this.wsUrl, undefined, { headers: this.headers });
    // Eagerly record this socket so onclose can identity-guard: a stale close
    // from a superseded/lingering socket must not tear down the live connection.
    this.ws = ws;

    const promise = new Promise<HelloOkPayload>((resolve, reject) => {
      // Set up a single persistent message handler that routes based on
      // connection state.  This avoids the problem of reassigning ws.onmessage
      // after connect — the mock/test's `receive()` always calls the same
      // handler reference.
      let connectResolve: ((hello: HelloOkPayload) => void) | null = resolve;
      let connectReject: ((err: Error) => void) | null = reject;

      const timeoutId = this._setTimeout(() => {
        if (connectReject) connectReject(new Error(`TeClaw WS connect handshake timeout (${this.connectTimeoutMs}ms)`));
        connectResolve = null;
        connectReject = null;
        try { ws.close(); } catch { /* ignore */ }
      }, this.connectTimeoutMs);

      ws.onmessage = (ev: { data: unknown }) => {
        // Post-connect: route through handleMessage
        if (this._connected) {
          this.handleMessage(ev);
          return;
        }
        // Pre-connect handshake phase
        const frame = this.parseFrame(ev.data);
        if (!frame) return;

        if (isWsResFrame(frame) && frame.ok === true && (frame.payload as { type?: string }).type === "hello-ok") {
          this._clearTimeout(timeoutId);
          this.helloOk = frame.payload as unknown as HelloOkPayload;
          this._connected = true;
          this.ws = ws;
          if (connectResolve) connectResolve(this.helloOk);
          connectResolve = null;
          connectReject = null;
          return;
        }

        // Handshake error (ok=false with error details per WS API v2 spec)
        if (isWsResFrame(frame) && frame.ok === false) {
          this._clearTimeout(timeoutId);
          const errShape = frame.error;
          const errMsg = errShape
            ? `${errShape.code}: ${errShape.message}`
            : "Unknown handshake error";
          if (connectReject) connectReject(new Error(`TeClaw WS handshake failed: ${errMsg}`));
          connectResolve = null;
          connectReject = null;
          try { ws.close(); } catch { /* ignore */ }
          return;
        }
      };

      ws.onclose = () => {
        // Ignore close events from sockets that are no longer the active one
        // (e.g. a losing reconnect attempt whose socket lingered, or a socket
        // from before a single-flight connect). Only the current socket may
        // tear down shared connection state — otherwise a stale close clobbers
        // a fresh connection and the provider stays stuck at null.
        if (this.ws !== ws) return;
        this._connected = false;
        this.ws = null;
        this.helloOk = null;

        // If the close happens during handshake, reject the in-flight connect
        // promptly so callers don't hang until the (slower) handshake timeout.
        if (connectReject) {
          connectReject(new Error("TeClaw WS closed during handshake"));
          connectResolve = null;
          connectReject = null;
        }

        // Reject any pending requests so callers don't hang forever
        for (const [id, pending] of this.pendingRequests) {
          pending.reject(new Error(`WebSocket closed while waiting for response (req ${id})`));
        }
        this.pendingRequests.clear();

        // Reject any event listeners that are still waiting (e.g., runAgentLoop)
        for (const [eventKey, listeners] of this.eventListeners) {
          // For chat events (bare "chat" or composite "chat:<sessionKey>"),
          // resolve with an error so runAgentLoop doesn't hang
          if (eventKey === "chat" || eventKey.startsWith("chat:")) {
            for (const listener of listeners) {
              try {
                listener({ state: "error", error: "WebSocket connection closed" } as Record<string, unknown>);
              } catch {
                // Swallow listener errors during cleanup
              }
            }
          }
        }
        this.eventListeners.clear();
      };

      ws.onerror = (ev: { message?: string }) => {
        this._clearTimeout(timeoutId);
        if (connectReject) connectReject(new Error(`TeClaw WS connection error: ${ev.message ?? "unknown"}`));
        connectResolve = null;
        connectReject = null;
      };

      ws.onopen = () => {
        // Send connect req frame
        const connectPayload: ConnectPayload = {
          minProtocol: 3,
          maxProtocol: 3,
          client: this.clientInfo,
          auth: { token: this.token },
        };
        const validation = validateConnectPayload(connectPayload);
        if (!validation.valid) {
          this._clearTimeout(timeoutId);
          if (connectReject) connectReject(new Error(`Invalid connect payload: ${validation.error}`));
          connectResolve = null;
          connectReject = null;
          try { ws.close(); } catch { /* ignore */ }
          return;
        }
        this.sendFrame(ws, {
          type: "req",
          id: nextReqId(),
          method: "connect",
          params: connectPayload as unknown as Record<string, unknown>,
        });
      };
    });

    this._connectingPromise = promise;
    // Clear the single-flight slot once the attempt settles so a later
    // (genuine) reconnect can proceed.
    promise.then(
      () => { if (this._connectingPromise === promise) this._connectingPromise = null; },
      () => { if (this._connectingPromise === promise) this._connectingPromise = null; },
    );
    return promise;
  }

  /** Disconnect from the WebSocket server. */
  disconnect(): void {
    if (this.ws) {
      try { this.ws.close(); } catch { /* ignore */ }
      this.ws = null;
      this._connected = false;
      this.helloOk = null;
    }
  }

  // ── Post-Connect Message Routing ──

  private handleMessage(ev: { data: unknown }): void {
    const frame = this.parseFrame(ev.data);
    if (!frame) return;

    if (isWsResFrame(frame)) {
      const pending = this.pendingRequests.get(frame.id);
      if (pending) {
        this.pendingRequests.delete(frame.id);
        if (frame.ok === false) {
          // Server reported an error per WS API v2 spec
          const errShape = frame.error;
          const errMsg = errShape
            ? `${errShape.code}: ${errShape.message}`
            : "Unknown error";
          pending.reject(new Error(`TeClaw WS error: ${errMsg}`));
        } else {
          pending.resolve(frame.payload ?? {});
        }
      }
    } else if (isWsEventFrame(frame)) {
      // Handle tick events (heartbeat) silently
      if (isTickEvent(frame)) {
        return;
      }
      // Dispatch to event listeners, routing by payload.sessionKey for
      // concurrent runAgentLoop() isolation. TeClaw server injects
      // sessionKey into every event payload (handler.rs:636).
      const payloadSessionKey = (frame.payload as Record<string, unknown>).sessionKey;
      const compositeKey = typeof payloadSessionKey === "string"
        ? `${frame.event}:${payloadSessionKey}`
        : frame.event;
      const listeners = this.eventListeners.get(compositeKey) ?? [];
      // Fallback: bare event type (defensive — should not happen with correct TeClaw)
      const matched = listeners.length > 0
        ? listeners
        : (this.eventListeners.get(frame.event) ?? []);
      for (const listener of matched) {
        try {
          listener(frame.payload);
        } catch {
          // Swallow listener errors
        }
      }
    }
  }

  // ── Session Creation (HTTP POST /api/v1/sessions) ──

  /** Session cache: maps sessionKey → true for sessions we've already created. */
  private _createdSessions = new Set<string>();

  /**
   * Create a teclaw session via REST API (POST /api/v1/sessions).
   *
   * Per teclaw docs, a session must exist before chat.send can route messages
   * to it. The returned session_key (data.id, shaped like
   * "session:<uuid>:user:<bot_id>") is the authoritative key for WS chat.send.
   *
   * Uses the same mandatory headers as the WS handshake (x-target-bot-id,
   * x-andc-target-service, x-tracer-traceid) plus x-load-test.
   *
   * @returns The session_key string from teclaw's response
   * @throws Error if the request fails or response is invalid
   */
  async createSession(options?: { title?: string; agentId?: string }): Promise<string> {
    const url = `${this.httpBaseUrl}/api/v1/sessions`;

    const reqHeaders: Record<string, string> = {
      "Content-Type": "application/json",
      ...this.headers, // includes x-andc-target-service, x-target-bot-id, x-tracer-traceid
      "x-load-test": "F",
    };

    const body: Record<string, unknown> = {};
    if (options?.title) body.title = options.title;
    if (options?.agentId) body.agent_id = options.agentId;

    const _fetch = this.deps.fetch ?? globalThis.fetch.bind(globalThis);

    console.error(`[teclaw:agent-loop] createSession: POST ${url}`);
    console.error(`[teclaw:agent-loop] createSession: headers: x-target-bot-id=${reqHeaders["x-target-bot-id"] ?? "MISSING"} x-andc-target-service=${reqHeaders["x-andc-target-service"] ?? "MISSING"} x-tracer-traceid=${reqHeaders["x-tracer-traceid"] ?? "MISSING"} x-load-test=${reqHeaders["x-load-test"]}`);
    console.error(`[teclaw:agent-loop] createSession: body=${JSON.stringify(body)}`);

    let response: { ok: boolean; status: number; json(): Promise<unknown> };
    try {
      response = await _fetch(url, {
        method: "POST",
        headers: reqHeaders,
        body: JSON.stringify(body),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[teclaw:agent-loop] createSession: FETCH ERROR: ${msg.slice(0, 300)}`);
      throw new Error(`TeClaw createSession fetch failed: ${msg}`);
    }

    let result: Record<string, unknown>;
    try {
      result = await response.json() as Record<string, unknown>;
    } catch (err) {
      console.error(`[teclaw:agent-loop] createSession: JSON PARSE ERROR (HTTP ${response.status}): ${err instanceof Error ? err.message : String(err)}`);
      throw new Error(`TeClaw createSession: invalid JSON response (HTTP ${response.status})`);
    }

    console.error(`[teclaw:agent-loop] createSession: response HTTP ${response.status}: ${JSON.stringify(result).slice(0, 500)}`);

    if (!result.success) {
      throw new Error(`TeClaw createSession failed: HTTP ${response.status} ${JSON.stringify(result)}`);
    }

    const data = result.data as Record<string, unknown> | undefined;
    const sessionKey = data?.id as string | undefined;
    if (!sessionKey) {
      throw new Error(`TeClaw createSession: response missing data.id: ${JSON.stringify(result).slice(0, 300)}`);
    }

    this._createdSessions.add(sessionKey);
    console.error(`[teclaw:agent-loop] createSession: SUCCESS sessionKey=${sessionKey}`);
    return sessionKey;
  }

  /**
   * Check if a sessionKey looks like a valid teclaw session_key
   * (shaped like "session:<uuid>:user:<bot_id>") versus a fallback key
   * (like "clawmind-1234567890").
   */
  private isValidTeClawSessionKey(sk: string): boolean {
    return sk.startsWith("session:") && sk.includes(":user:");
  }

  // ── Agent Loop Execution ──

  /**
   * Execute a full Agent Loop via WebSocket chat.send.
   *
   * Flow (MUST be followed exactly, same as chatInject):
   * 1. Ensure connected (auto-connect if needed) — same WS connection as chatInject
   * 2. ALWAYS create a new agent session via POST /api/v1/sessions
   *    — the user's conversation session (x-teclaw-session-key) is for
   *      chatInject notification routing, NOT for agent loop execution
   *    — agent loops need their own dedicated session
   * 3. Send chat.send req frame with the NEW sessionKey
   * 4. Wait for res (accepted)
   * 5. Collect chat events (delta/final/error/aborted)
   * 6. Return result from final event
   */
  async runAgentLoop(
    params: {
      prompt: string;
      systemPrompt?: string;
      maxTurns?: number;
      maxTokens?: number;
      allowedTools?: string[];
      workflowContext?: Record<string, unknown>;
      sessionKey?: string;
      onProgress?: (event: TeClawAgentProgressEvent) => void | Promise<void>;
    },
  ): Promise<EmbeddedAgentResult> {
    console.error(`[teclaw:agent-loop] runAgentLoop: START prompt_len=${params.prompt.length} sessionKey=${params.sessionKey ?? "none"} defaultSessionKey=${this.defaultSessionKey} connected=${this.connected}`);

    // Track createSession outcome for diagnostic meta — declared before try
    // so it's accessible in the catch block for error reporting.
    let createSessionOutcome: { success: boolean; sessionKey?: string; error?: string } | undefined;

    try {
    if (!this.connected) {
      console.error(`[teclaw:agent-loop] runAgentLoop: not connected, auto-connecting to ${this.wsUrl}...`);
      await this.connect();
      console.error(`[teclaw:agent-loop] runAgentLoop: connected=${this.connected}`);
    }

    // ── ALWAYS create a new agent session via POST /api/v1/sessions ──
    //
    // The user's conversation session (x-teclaw-session-key, stored in
    // this.defaultSessionKey) is for chatInject notification routing —
    // it tells TeClaw which client session to push progress messages to.
    //
    // For chat.send (agent loop execution), we MUST use a dedicated
    // agent session. Reusing the user's conversation session would:
    //   1. Inject agent loop messages into the user's chat stream
    //   2. Risk session-level conflicts (concurrent access, state corruption)
    //   3. Violate TeClaw's session isolation model
    //
    // This matches chatInject's behavior: chatInject uses the user's
    // session for routing; agent loops use their own session for execution.
    console.error(`[teclaw:agent-loop] runAgentLoop: creating dedicated agent session via POST /api/v1/sessions...`);
    let sk: string;
    try {
      const createdKey = await this.createSession({
        title: `ClawMind workflow: ${params.workflowContext?.workflowId ?? "unknown"}`,
        agentId: "clawmind",
      });
      sk = createdKey;
      createSessionOutcome = { success: true, sessionKey: sk };
      console.error(`[teclaw:agent-loop] runAgentLoop: created agent sessionKey=${sk}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[teclaw:agent-loop] runAgentLoop: createSession FAILED: ${msg.slice(0, 300)}`);
      createSessionOutcome = { success: false, error: msg.slice(0, 300) };
      // createSession failed — cannot proceed without a valid session.
      // Return error immediately (no degradation to sampling).
      return {
        error: `TeClaw createSession failed: ${msg.slice(0, 200)}`,
        meta: this._buildDiagnosticMeta({
          chatSendOutcome: "createSession-failed",
          createSessionOutcome,
        }),
      };
    }
    console.error(`[teclaw:agent-loop] runAgentLoop: sending chat.send with sessionKey=${sk}`);

    // Send chat.send req
    const reqId = nextReqId();
    const chatSendPayload: Record<string, unknown> = {
      sessionKey: sk,
      message: params.prompt,
    };
    if (params.maxTurns) chatSendPayload.maxTurns = params.maxTurns;
    if (params.maxTokens) chatSendPayload.maxTokens = params.maxTokens;
    if (params.allowedTools) chatSendPayload.allowedTools = params.allowedTools;
    if (params.systemPrompt) chatSendPayload.systemPrompt = params.systemPrompt;
    if (params.workflowContext) chatSendPayload.workflowContext = params.workflowContext;

    this.sendFrame(this.getConnectedWs(), {
      type: "req",
      id: reqId,
      method: "chat.send",
      params: chatSendPayload,
    });

    try {
      // Wait for res (accepted/queued)
      const resPayload = await this.waitForResponse(reqId, 5_000);
      const resStatus = (resPayload as { status?: string }).status;
      console.error(`[teclaw:agent-loop] runAgentLoop: chat.send res status=${resStatus ?? "unknown"} payload=${JSON.stringify(resPayload).slice(0, 300)}`);
      // When ok=true (which waitForResponse guarantees — it rejects on ok=false),
      // the server has accepted the request. The status field is informational;
      // don't reject on unexpected/unrecognized status values — only ok=false
      // means actual rejection (handled by waitForResponse → catch below).
      // Previously, missing status treated as "unknown" and rejected — that was
      // wrong because TeClaw may respond with ok=true + empty payload or a
      // status other than "accepted"/"queued" (e.g., "pending").
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[teclaw:agent-loop] runAgentLoop: chat.send response TIMEOUT/ERROR: ${msg.slice(0, 300)}`);
      return {
        error: `TeClaw chat.send response timeout: ${msg}`,
        meta: this._buildDiagnosticMeta({
          chatSendOutcome: "timeout",
          chatSendError: msg.slice(0, 300),
          sessionKeyUsed: sk,
          createSessionOutcome,
        }),
      };
    }

    // Collect chat events until final/error/aborted
    // Use composite key "chat:<sessionKey>" for listener isolation —
    // enables concurrent runAgentLoop() calls on the same TeClawProvider.
    const listenerKey = `chat:${sk}`;
    console.error(`[teclaw:agent-loop] runAgentLoop: waiting for chat events on listenerKey=${listenerKey}...`);

    return new Promise<EmbeddedAgentResult>((resolve) => {
      let deltaBuffer = "";
      let eventCount = 0;

      const cleanup = () => {
        this.eventListeners.delete(listenerKey);
      };

      const reportProgress = (event: TeClawAgentProgressEvent): void => {
        if (!params.onProgress) return;
        try {
          void Promise.resolve(params.onProgress(event)).catch((err) => {
            const message = err instanceof Error ? err.message : String(err);
            console.error(
              `[teclaw:agent-loop] progress callback failed seq=${event.seq ?? "none"} ` +
              `text_len=${event.text.length} error=${message.slice(0, 200)}`,
            );
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          console.error(
            `[teclaw:agent-loop] progress callback threw seq=${event.seq ?? "none"} ` +
            `text_len=${event.text.length} error=${message.slice(0, 200)}`,
          );
        }
      };

      const chatListener = (payload: Record<string, unknown>) => {
        const chatPayload = payload as unknown as ChatEventPayload;
        eventCount++;
        switch (chatPayload.state) {
          case "delta": {
            // TeClaw wire format: delta events use `text` field (not `content`)
            const deltaText = chatPayload.text || chatPayload.content;
            if (deltaText) deltaBuffer += deltaText;
            break;
          }
          case "final": {
            const messageText = extractChatMessageText(chatPayload, deltaBuffer);

            if (chatPayload.completed !== true) {
              if (messageText) {
                reportProgress({ text: messageText, seq: chatPayload.seq });
              }
              deltaBuffer = "";
              break;
            }

            cleanup();
            console.error(
              `[teclaw:agent-loop] runAgentLoop: completed final after ${eventCount} events, ` +
              `final_text_len=${messageText.length}`,
            );
            resolve({
              output: messageText || undefined,
              payloads: chatPayload.payloads,
              messagingToolSentTexts: chatPayload.messagingToolSentTexts,
              meta: chatPayload.meta,
            });
            break;
          }
          case "error":
            cleanup();
            // TeClaw wire format: error events use `errorMessage` field (not `error`)
            const errMsg = chatPayload.errorMessage || chatPayload.error || "unknown error";
            console.error(`[teclaw:agent-loop] runAgentLoop: chat event ERROR after ${eventCount} events: ${errMsg}`);
            resolve({
              error: `TeClaw Agent Loop failed: ${errMsg}`,
              meta: {
                ...(chatPayload.meta ?? {}),
                ...this._buildDiagnosticMeta({
                  chatEventOutcome: "error",
                  chatEventError: errMsg,
                  chatEventCount: eventCount,
                  sessionKeyUsed: sk,
                  createSessionOutcome,
                }),
              },
            });
            break;
          case "aborted":
            cleanup();
            resolve({
              error: "TeClaw Agent Loop was aborted",
              meta: this._buildDiagnosticMeta({
                chatEventOutcome: "aborted",
                chatEventCount: eventCount,
                sessionKeyUsed: sk,
                createSessionOutcome,
              }),
            });
            break;
          case "thinking":
          case "status":
            // Informational events — no action needed for agent loop result
            break;
        }
      };

      this.eventListeners.set(listenerKey, [chatListener]);
    });
    } catch (err) {
      // Catch exceptions from connect(), sendFrame(), or other sync/async setup
      // that occur before the chat event promise is established.
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[teclaw:agent-loop] runAgentLoop: UNCAUGHT ERROR: ${msg.slice(0, 300)}`);
      return {
        error: `TeClaw Agent Loop exception: ${msg.slice(0, 200)}`,
        meta: this._buildDiagnosticMeta({
          uncaughtError: msg.slice(0, 500),
          createSessionOutcome,
        }),
      };
    }
  }

  // ── ChatInject (WS) ──

  /**
   * Send a chat/inject message via WebSocket chat.inject method.
   */
  async chatInjectWS(
    sessionKey: string,
    message: string,
    label: string,
    context?: TeClawChatInjectContext,
  ): Promise<TeClawChatInjectResponse> {
    if (!this.connected) {
      await this.connect();
    }

    const reqId = nextReqId();
    console.error(`[teclaw:ws] chat.inject reqId=${reqId} sessionKey=${sessionKey.slice(0, 30)} label=${label} msg_len=${message.length}`);
    this.sendFrame(this.getConnectedWs(), {
      type: "req",
      id: reqId,
      method: "chat.inject",
      params: {
        ...(context ?? {}),
        sessionKey,
        message,
        label,
      },
    });

    try {
      const resPayload = await this.waitForResponse(reqId, 3_000);
      const status = (resPayload as { status?: string }).status;
      console.error(`[teclaw:ws] chat.inject response reqId=${reqId} status=${status ?? "unknown"}`);
      return {
        status: status === "pending" ? "pending" : "delivered",
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[teclaw:ws] chat.inject FAILED reqId=${reqId} error=${msg.slice(0, 100)}`);
      // Best-effort: don't block on inject failure
      return { status: "delivered" as const };
    }
  }

  // ── Cancel (chat.abort) ──

  /** Cancel a running agent loop via chat.abort. */
  async cancelJob(
    runId: string,
  ): Promise<{ status: "cancelled" | "not_found" }> {
    if (!this.connected) {
      await this.connect();
    }

    const reqId = nextReqId();
    const sk = this.defaultSessionKey;
    this.sendFrame(this.getConnectedWs(), {
      type: "req",
      id: reqId,
      method: "chat.abort",
      params: { sessionKey: sk, runId },
    });

    try {
      const resPayload = await this.waitForResponse(reqId, 3_000);
      const status = (resPayload as { status?: string }).status;
      return { status: status === "aborted" ? "cancelled" : "not_found" };
    } catch {
      return { status: "not_found" as const };
    }
  }

  // ── Approval Resolution ──

  /** Resolve a pending approval via exec.approval.resolve. */
  async resolveApproval(
    approvalId: string,
    action: string,
    comment?: string,
  ): Promise<{ status: string }> {
    if (!this.connected) {
      await this.connect();
    }

    const reqId = nextReqId();
    const payload: Record<string, unknown> = { approvalId, action };
    if (comment) payload.comment = comment;

    this.sendFrame(this.getConnectedWs(), {
      type: "req",
      id: reqId,
      method: "exec.approval.resolve",
      params: payload,
    });

    try {
      const resPayload = await this.waitForResponse(reqId, 3_000);
      return { status: (resPayload as { status?: string }).status ?? "resolved" };
    } catch {
      return { status: "error" };
    }
  }

  // ── Internal Helpers ──

  /**
   * Get the current WebSocket connection, throwing if not connected.
   *
   * Guards against the race condition where `onclose` fires between
   * `connect()` returning and `sendFrame()` being called, which would
   * leave `this.ws` as null and cause a runtime crash on the non-null
   * assertion (`this.ws!`).
   */
  private getConnectedWs(): WebSocketLike {
    const ws = this.ws;
    if (!ws || ws.readyState !== 1) {
      throw new Error("WebSocket not connected");
    }
    return ws;
  }

  /** Send a frame to the WebSocket. */
  private sendFrame(ws: WebSocketLike, frame: WsReqFrame): void {
    if (ws.readyState !== 1) {
      throw new Error(`Cannot send frame: WebSocket readyState=${ws.readyState}`);
    }
    ws.send(JSON.stringify(frame));
  }

  /** Parse an incoming WebSocket message into a frame. */
  private parseFrame(data: unknown): WsReqFrame | WsResFrame | WsEventFrame | null {
    if (typeof data !== "string") return null;
    try {
      const parsed = JSON.parse(data);
      if (parsed && typeof parsed === "object" && typeof parsed.type === "string") {
        return parsed as WsReqFrame | WsResFrame | WsEventFrame;
      }
    } catch {
      // Not JSON
    }
    return null;
  }

  /** Wait for a res frame with the given request ID. */
  private waitForResponse(reqId: string, timeoutMs: number): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      const timeoutId = this._setTimeout(() => {
        this.pendingRequests.delete(reqId);
        reject(new Error(`Response timeout for req ${reqId}`));
      }, timeoutMs);

      this.pendingRequests.set(reqId, {
        resolve: (payload) => {
          this._clearTimeout(timeoutId);
          resolve(payload);
        },
        reject: (error) => {
          this._clearTimeout(timeoutId);
          reject(error);
        },
      });
    });
  }

  /** Default WebSocket factory — uses 'ws' package when custom headers are needed, otherwise native WebSocket. */
  private defaultCreateWebSocket(url: string, _protocols?: string[], options?: { headers?: Record<string, string> }): WebSocketLike {
    const hasCustomHeaders = options?.headers && Object.keys(options.headers).length > 0;

    // When custom headers are needed (e.g., x-target-bot-id for TeClaw WS),
    // native WebSocket does NOT support custom headers — must use 'ws' package.
    if (hasCustomHeaders) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const wsModule = require("ws") as { WebSocket: new (url: string, protocols?: string[], options?: { headers?: Record<string, string> }) => unknown };
        const wsInstance = new wsModule.WebSocket(url, undefined, { headers: options?.headers });
        console.error(`[teclaw:ws] Creating WS with custom headers: ${Object.keys(options!.headers!).join(", ")}`);
        return wsInstance as unknown as WebSocketLike;
      } catch (err) {
        // ws package not available — fall through to native WebSocket (headers will be lost)
        console.warn(`[teclaw:ws] 'ws' package not available, custom headers cannot be sent: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    // Try native WebSocket (Node.js 22+, browsers) — no custom headers support
    if (typeof globalThis.WebSocket === "function") {
      return new globalThis.WebSocket(url) as unknown as WebSocketLike;
    }
    // Fallback to ws package without custom headers
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const wsModule = require("ws") as { WebSocket: new (url: string, protocols?: string[], options?: { headers?: Record<string, string> }) => unknown };
      const wsInstance = new wsModule.WebSocket(url, undefined, { headers: options?.headers });
      return wsInstance as unknown as WebSocketLike;
    } catch {
      throw new Error(
        "TeClawProvider requires WebSocket support. " +
        "Install the 'ws' package (npm install ws) or use Node.js 22+.",
      );
    }
  }
}

// ── Factory from Environment ──

/**
 * Create a TeClawProvider from environment variables.
 * Returns undefined if TECLAW_WS_URL is not set.
 *
 * New env vars (WebSocket):
 *   TECLAW_WS_URL    — WebSocket URL (e.g., "wss://angw.andc-inc.cn/ws/v1/chat")
 *   TECLAW_WS_TOKEN  — MCP Token for auth
 *   TECLAW_WS_HEADERS — JSON string of additional headers (e.g., '{"x-andc-target-service":"tautie"}')
 *
 * Backward compat (DEPRECATED):
 *   TECLAW_BASE_URL       — will derive WS URL by appending /ws/v1/chat
 *   TECLAW_CHAT_INJECT_KEY — will be used as token
 */
export function createTeClawProviderFromEnv(): TeClawProvider | undefined {
  let wsUrl = process.env.TECLAW_WS_URL;
  let token = process.env.TECLAW_WS_TOKEN ?? "";
  let httpBaseUrl = process.env.TECLAW_HTTP_BASE_URL ?? "";
  let headers: Record<string, string> = {};

  // Parse TECLAW_WS_HEADERS if present
  const wsHeadersJson = process.env.TECLAW_WS_HEADERS;
  if (wsHeadersJson) {
    try {
      headers = JSON.parse(wsHeadersJson) as Record<string, string>;
    } catch {
      console.warn("[clawmind:mcp] TECLAW_WS_HEADERS is not valid JSON, ignoring");
    }
  }

  // Backward compat: derive from TECLAW_BASE_URL if TECLAW_WS_URL not set
  if (!wsUrl && process.env.TECLAW_BASE_URL) {
    const baseUrl = process.env.TECLAW_BASE_URL.replace(/\/+$/, "");
    wsUrl = baseUrl
      .replace(/^http:/, "ws:")
      .replace(/^https:/, "wss:") + "/ws/v1/chat";
    if (!token) {
      token = process.env.TECLAW_CHAT_INJECT_KEY ?? "";
    }
    console.warn(
      "[clawmind:mcp] DEPRECATED: TECLAW_BASE_URL is deprecated. " +
      "Use TECLAW_WS_URL instead.",
    );
  }

  // Also support old TECLAW_AGENT_LOOP_URL
  if (!wsUrl && process.env.TECLAW_AGENT_LOOP_URL) {
    const agentLoopUrl = process.env.TECLAW_AGENT_LOOP_URL.replace(/\/+$/, "");
    wsUrl = agentLoopUrl
      .replace(/^http:/, "ws:")
      .replace(/^https:/, "wss:")
      .replace(/\/api\/.*$/, "") + "/ws/v1/chat";
    if (!token) {
      token = process.env.TECLAW_CHAT_INJECT_KEY ?? "";
    }
    console.warn(
      "[clawmind:mcp] DEPRECATED: TECLAW_AGENT_LOOP_URL is deprecated. " +
      "Use TECLAW_WS_URL instead.",
    );
  }

  if (!wsUrl) {
    return undefined;
  }

  // Auto-inject mandatory teclaw WS handshake headers if missing.
  // TeClaw's /ws/v1/chat endpoint requires these headers (per docs):
  //   x-andc-target-service — routing identifier (must be present, value logged not validated)
  //   x-target-bot-id       — session tenant key (missing → HTTP 400)
  //   x-tracer-traceid      — trace ID (missing → HTTP 400)
  const envHeaders: Record<string, string> = { ...headers };
  if (!envHeaders["x-tracer-traceid"]) {
    envHeaders["x-tracer-traceid"] = `clawmind-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  return new TeClawProvider({ wsUrl, token, headers: envHeaders, httpBaseUrl: httpBaseUrl || undefined });
}

/**
 * Create a TeClawProvider from the application config (configs/application.yaml).
 *
 * This is the preferred factory — config file values take precedence,
 * with env vars as overrides (handled by parseTeClaw in the config loader).
 *
 * Falls back to deprecated baseUrl / agentLoopUrl derivation when wsUrl is not set.
 * Returns undefined if neither wsUrl nor baseUrl nor agentLoopUrl is configured.
 */
export function createTeClawProviderFromConfig(config: import("../config/types.js").TeClawConfig, sessionKey?: string): TeClawProvider | undefined {
  if (!config.enabled) {
    return undefined;
  }

  let wsUrl = config.wsUrl;
  let token = config.wsToken;
  const headers = config.wsHeaders ?? {};

  // Backward compat: derive from baseUrl if wsUrl not set
  if (!wsUrl && config.baseUrl) {
    const baseUrl = config.baseUrl.replace(/\/+$/, "");
    wsUrl = baseUrl
      .replace(/^http:/, "ws:")
      .replace(/^https:/, "wss:") + "/ws/v1/chat";
    if (!token) {
      token = config.chatInjectKey;
    }
    console.warn(
      "[clawmind:mcp] DEPRECATED: teclaw.baseUrl is deprecated. " +
      "Use teclaw.wsUrl instead.",
    );
  }

  // Also support old agentLoopUrl
  if (!wsUrl && config.agentLoopUrl) {
    const agentLoopUrl = config.agentLoopUrl.replace(/\/+$/, "");
    wsUrl = agentLoopUrl
      .replace(/^http:/, "ws:")
      .replace(/^https:/, "wss:")
      .replace(/\/api\/.*$/, "") + "/ws/v1/chat";
    if (!token) {
      token = config.chatInjectKey;
    }
    console.warn(
      "[clawmind:mcp] DEPRECATED: teclaw.agentLoopUrl is deprecated. " +
      "Use teclaw.wsUrl instead.",
    );
  }

  if (!wsUrl) {
    return undefined;
  }

  // Auto-inject mandatory teclaw WS handshake headers if missing.
  const configHeaders: Record<string, string> = { ...headers };
  if (!configHeaders["x-tracer-traceid"]) {
    configHeaders["x-tracer-traceid"] = `clawmind-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  return new TeClawProvider({
    wsUrl,
    token,
    headers: configHeaders,
    httpBaseUrl: config.httpBaseUrl || undefined,
    sessionKey: sessionKey || undefined,
  });
}
