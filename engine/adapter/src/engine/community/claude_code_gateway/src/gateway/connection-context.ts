// Per-socket context: sequencing, tick loop, attach tracking, frame send.
//
// One instance is created on `wss.on('connection')`. It no longer owns active
// runs — run lifecycle is managed by SessionRuntimeRegistry. On close it only
// detaches from sessions, allowing grace-period recovery.

import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';
import type {
  AgentEventPayload,
  GatewayChatEvent,
  GatewayFrame,
} from '../types.js';
import type { SessionRuntimeRegistry } from '../runtime/session-runtime-registry.js';

const FRAME_ERROR_WINDOW_MS = 5_000;
const FRAME_ERROR_THRESHOLD = 10;

export class ConnectionContext {
  ws: WebSocket;
  seq = 0;
  agentSeq = new Map<string, number>();
  tickTimer: NodeJS.Timeout | null = null;
  connId = randomUUID();
  attachedSessions = new Set<string>();
  controllerSessions = new Set<string>();
  private disposed = false;
  private frameErrorTimes: number[] = [];
  private readonly tickIntervalMs: number;
  private readonly runtimeRegistry: SessionRuntimeRegistry;

  constructor(ws: WebSocket, opts: {
    tickIntervalMs: number;
    runtimeRegistry: SessionRuntimeRegistry;
  }) {
    this.ws = ws;
    this.tickIntervalMs = opts.tickIntervalMs;
    this.runtimeRegistry = opts.runtimeRegistry;
  }

  send(frame: GatewayFrame) {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(frame));
  }

  event(event: string, payload: unknown) {
    this.send({ type: 'event', event, payload, seq: ++this.seq });
  }

  agentEvent(runId: string, sessionKey: string, stream: string, data: Record<string, unknown>, delta?: string) {
    const seq = (this.agentSeq.get(runId) ?? 0) + 1;
    this.agentSeq.set(runId, seq);
    const payload: AgentEventPayload = { runId, sessionKey, seq, stream, ts: Date.now(), data, ...(delta !== undefined && { delta }) };
    this.send({ type: 'event', event: 'agent', payload, seq: ++this.seq });
  }

  /** Emit a chat event using the per-run agent sequence for the payload seq. */
  chatEvent(runId: string, sessionKey: string, payload: Omit<GatewayChatEvent, 'runId' | 'sessionKey' | 'seq'>) {
    const seq = (this.agentSeq.get(runId) ?? 0) + 1;
    this.agentSeq.set(runId, seq);
    const chatPayload: GatewayChatEvent = { runId, sessionKey, seq, ...payload };
    this.send({ type: 'event', event: 'chat', payload: chatPayload, seq: ++this.seq });
  }

  /** Emit a top-level notification event (toast/snackbar). */
  notificationEvent(payload: { key: string; text: string; priority: string; color?: string; timeoutMs?: number; sessionKey?: string; runId?: string }) {
    this.send({ type: 'event', event: 'notification', payload, seq: ++this.seq });
  }

  /** Emit a top-level prompt.suggestions event. */
  promptSuggestionEvent(payload: { runId: string; sessionKey: string; suggestions: Array<{ text: string }> }) {
    this.send({ type: 'event', event: 'prompt.suggestions', payload, seq: ++this.seq });
  }

  response(id: string, ok: boolean, payload?: unknown, error?: { message: string; code: string; details?: unknown }) {
    this.send({ type: 'res', id, ok, payload, error });
  }

  startTicks() {
    this.stopTicks();
    this.tickTimer = setInterval(() => {
      if (this.ws.readyState === WebSocket.OPEN) {
        this.event('tick', { ts: Date.now() });
      }
    }, this.tickIntervalMs);
  }

  stopTicks() {
    if (this.tickTimer) clearInterval(this.tickTimer);
    this.tickTimer = null;
  }

  /**
   * Return true if we've seen too many frame parse errors in the rolling window
   * — the caller should close the socket.
   */
  noteFrameError(): boolean {
    const now = Date.now();
    this.frameErrorTimes = this.frameErrorTimes.filter(t => now - t <= FRAME_ERROR_WINDOW_MS);
    this.frameErrorTimes.push(now);
    return this.frameErrorTimes.length >= FRAME_ERROR_THRESHOLD;
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.stopTicks();
    // Detach from all sessions — runtime registry handles orphan grace
    this.runtimeRegistry.detachAllForConnection(this.connId);
    this.attachedSessions.clear();
    this.controllerSessions.clear();
    this.agentSeq.clear();
  }
}
