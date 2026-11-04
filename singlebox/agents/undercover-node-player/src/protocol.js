import { randomUUID } from "node:crypto";

export const PROTOCOL_VERSION = 3;
export const HEARTBEAT_INTERVAL_MS = 60_000;

export class ProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ProtocolError";
    this.code = code;
  }
}

export function extractText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
}

export class BcnProtocol {
  constructor({
    onTask = async () => "",
    clientKind = "undercover-node-player",
    token = null,
    heartbeatMs = HEARTBEAT_INTERVAL_MS,
    handshakeTimeoutMs = 30_000,
    environment = {},
    now = () => Date.now(),
    onError = () => {},
  } = {}) {
    this.onTask = onTask;
    this.clientKind = clientKind;
    this.token = token;
    this.botUuid = null;
    this.heartbeatMs = heartbeatMs;
    this.handshakeTimeoutMs = handshakeTimeoutMs;
    this.environment = environment;
    this.now = now;
    this.onError = onError;

    this.socket = null;
    this.requestSequence = 0;
    this.pending = new Map();
    this.heartbeatTimer = null;
    this.runs = new Map();
    this.latestTaskSeq = new Map();
  }

  attach(socket) {
    if (!socket || typeof socket.send !== "function" || typeof socket.on !== "function") {
      throw new TypeError("socket must provide send() and on()");
    }
    this.socket = socket;
    socket.on("message", (data) => {
      this.handleFrame(data).catch((error) => this.onError(error));
    });
    socket.on("close", () => {
      this.stopHeartbeat();
      this.rejectPending(new ProtocolError("unavailable", "WebSocket closed"));
    });
    return this;
  }

  async connect(socket = this.socket) {
    if (socket !== this.socket || !this.socket) this.attach(socket);
    const params = {
      protocol_version: PROTOCOL_VERSION,
      client_kind: this.clientKind,
    };
    if (this.token) params.token = this.token;

    const response = await this.request("bot.connect", params, this.handshakeTimeoutMs);
    if (!response.ok) {
      const error = response.error || {};
      throw new ProtocolError(
        error.code || "handshake_failed",
        error.message || "bot.connect failed",
      );
    }

    const payload = response.payload || {};
    if (payload.protocol_version !== PROTOCOL_VERSION) {
      throw new ProtocolError(
        "unsupported_protocol_version",
        `expected protocol_version=${PROTOCOL_VERSION}, got ${payload.protocol_version}`,
      );
    }
    const capabilities = payload.capabilities || {};
    if (capabilities.unified_run_events !== true ||
        capabilities.canonical_session_id !== true) {
      throw new ProtocolError(
        "unsupported_protocol_version",
        "V3 requires unified_run_events and canonical_session_id capabilities",
      );
    }

    // Commit identity only after the complete V3 contract has been validated.
    this.token = payload.token || this.token;
    this.botUuid = payload.bot_uuid || null;
    if (payload.env && typeof payload.env === "object" && !Array.isArray(payload.env)) {
      for (const [key, value] of Object.entries(payload.env)) {
        this.environment[key] = String(value);
      }
    }
    this.startHeartbeat();
    return payload;
  }

  sendFrame(frame) {
    if (!this.socket) throw new ProtocolError("unavailable", "WebSocket is not attached");
    this.socket.send(JSON.stringify(frame));
    return frame;
  }

  request(method, params, timeoutMs = 30_000) {
    const id = `req-${++this.requestSequence}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new ProtocolError("unavailable", `request timed out: ${method}`));
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.sendFrame({ type: "req", id, method, params });
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  rejectPending(error) {
    for (const { reject, timer } of this.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    this.pending.clear();
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.sendHeartbeat().catch((error) => this.onError(error));
    }, this.heartbeatMs);
    this.heartbeatTimer.unref?.();
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  async sendHeartbeat() {
    const response = await this.request("bot.status", {});
    if (!response.ok) {
      const error = response.error || {};
      throw new ProtocolError(error.code || "heartbeat_failed", error.message || "bot.status failed");
    }
    return response;
  }

  ack(id, payload) {
    return this.sendFrame({ type: "res", id, ok: true, payload });
  }

  ackError(id, code, message) {
    return this.sendFrame({
      type: "res",
      id,
      ok: false,
      error: { code, message, retryable: false, retry_after_ms: null },
    });
  }

  async handleFrame(raw) {
    let frame;
    try {
      frame = typeof raw === "object" && raw !== null && !Buffer.isBuffer(raw)
        ? raw
        : JSON.parse(String(raw));
    } catch {
      return { accepted: false, reason: "invalid_json" };
    }
    if (!frame || typeof frame !== "object") {
      return { accepted: false, reason: "invalid_frame" };
    }

    if (frame.type === "res") {
      const pending = this.pending.get(frame.id);
      if (!pending) return { accepted: false, reason: "unknown_response" };
      clearTimeout(pending.timer);
      this.pending.delete(frame.id);
      pending.resolve(frame);
      return { accepted: true, type: "res" };
    }
    if (frame.type === "event") return { accepted: true, type: "event", ignored: true };
    if (frame.type !== "req") return { accepted: false, reason: "unknown_frame_type" };

    const params = frame.params || {};
    switch (frame.method) {
      case "chat.send":
        return this.handleChatSend(frame, params);
      case "chat.inject":
        return this.handleChatInject(frame, params);
      case "chat.abort":
        return this.handleChatAbort(frame, params);
      case "chat.history":
        this.ack(frame.id, {
          session_key: params.session_key,
          session_id: params.session_key,
          messages: [],
        });
        return { accepted: true, type: "chat.history" };
      default:
        this.ackError(frame.id, "unknown_method", `unknown method: ${frame.method}`);
        return { accepted: false, reason: "unknown_method" };
    }
  }

  canonicalSession(params) {
    const sessionId = params.bcs_session_id;
    const sessionKey = params.session_key;
    if (typeof sessionId !== "string" || !sessionId || sessionKey !== sessionId) {
      throw new ProtocolError(
        "invalid_request",
        "V3 requires identical non-empty bcs_session_id and session_key",
      );
    }
    return sessionId;
  }

  handleChatInject(frame, params) {
    try {
      this.canonicalSession(params);
    } catch (error) {
      this.ackError(frame.id, error.code || "invalid_request", error.message);
      return { accepted: false, reason: "invalid_session" };
    }
    this.ack(frame.id, {});
    return { accepted: true, type: "chat.inject" };
  }

  handleChatSend(frame, params) {
    let sessionKey;
    try {
      sessionKey = this.canonicalSession(params);
    } catch (error) {
      this.ackError(frame.id, error.code || "invalid_request", error.message);
      return { accepted: false, reason: "invalid_session" };
    }

    if (frame.seq !== undefined) {
      if (!Number.isSafeInteger(frame.seq) || frame.seq < 1) {
        this.ackError(frame.id, "invalid_request", "task seq must be a positive integer");
        return { accepted: false, reason: "invalid_seq" };
      }
      const latest = this.latestTaskSeq.get(sessionKey) || 0;
      if (frame.seq <= latest) {
        // Reasoning: an old task must not create a run, overwrite history, or emit an
        // artifact. The error ACK releases the caller; run state and latest seq stay
        // untouched. Requests without this optional seq still follow the base contract.
        this.ackError(frame.id, "invalid_request", `stale task seq ${frame.seq}; latest is ${latest}`);
        return { accepted: false, reason: "stale_seq" };
      }
    }

    const runId = `run-${randomUUID()}`;
    const run = {
      runId,
      sessionKey,
      status: "active",
      controller: new AbortController(),
      eventSeq: 0,
      processing: null,
    };
    this.runs.set(runId, run);
    if (frame.seq !== undefined) this.latestTaskSeq.set(sessionKey, frame.seq);
    this.ack(frame.id, { run_id: runId });

    const content = extractText(params.message?.content);
    run.processing = Promise.resolve().then(() => this.processRun(run, content, params));
    return { accepted: true, type: "chat.send", runId };
  }

  async processRun(run, content, params) {
    try {
      let text = await this.onTask({
        runId: run.runId,
        sessionKey: run.sessionKey,
        signal: run.controller.signal,
        content,
        params,
      });
      if (run.status !== "active") return;
      if (text === null || text === undefined) text = "";
      if (typeof text !== "string") throw new TypeError("onTask must return a string");
      this.emitChat(run, {
        state: "final",
        content: text.trim(),
        stopReason: "complete",
      });
    } catch (error) {
      // Abort wins over every later completion, including a callback that ignores
      // AbortSignal. This sample sends no deltas, so no partial answer can leak.
      // A protocol abort changes run.status before rejecting the callback. An
      // otherwise-active AbortError can come from the callback's own timeout and
      // still needs a terminal event to release the BCS node.
      if (run.status !== "active") return;
      const message = (error instanceof Error ? error.message : String(error)) || "decision failed";
      this.emitChat(run, {
        state: "error",
        errorCode: "DECISION_ERROR",
        errorMessage: message.slice(0, 500),
      });
    }
  }

  emitChat(run, terminal) {
    if (run.status !== "active") return false;
    const seq = ++run.eventSeq;
    const payload = {
      runId: run.runId,
      sessionId: run.sessionKey,
      seq,
      ts: this.now(),
      ...terminal,
    };
    this.sendFrame({ type: "event", event: "chat", payload, seq });
    run.status = "finished";
    return true;
  }

  handleChatAbort(frame, params) {
    const runId = params.run_id;
    const sessionKey = params.session_key;
    if (typeof runId !== "string" || !runId || typeof sessionKey !== "string" || !sessionKey) {
      this.ackError(frame.id, "invalid_request", "chat.abort requires session_key and run_id");
      return { accepted: false, reason: "invalid_request" };
    }
    const run = this.runs.get(runId);
    if (!run) {
      this.ackError(frame.id, "not_found", `unknown run ${runId}`);
      return { accepted: false, reason: "not_found" };
    }
    if (run.sessionKey !== sessionKey) {
      this.ackError(frame.id, "invalid_request", `run ${runId} belongs to another session`);
      return { accepted: false, reason: "wrong_session" };
    }
    if (run.status !== "active") {
      this.ack(frame.id, { aborted: true, aborted_run_ids: [] });
      return { accepted: true, type: "chat.abort", alreadyFinished: true };
    }

    // Mark terminal before invoking user code: even a non-cooperative callback cannot
    // publish a late final after this point.
    run.status = "aborted";
    run.controller.abort();
    this.ack(frame.id, { aborted: true, aborted_run_ids: [runId] });
    return { accepted: true, type: "chat.abort", aborted: runId };
  }

  async idle() {
    const processing = [...this.runs.values()]
      .map((run) => run.processing)
      .filter(Boolean);
    await Promise.allSettled(processing);
  }

  stop() {
    this.stopHeartbeat();
    for (const run of this.runs.values()) {
      if (run.status === "active") {
        run.status = "aborted";
        run.controller.abort();
      }
    }
    this.rejectPending(new ProtocolError("unavailable", "protocol stopped"));
  }
}
