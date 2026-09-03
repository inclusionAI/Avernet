import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';
import type { RawData } from 'ws';
import type { Config } from './config.js';
import type { ResolvedEndpoint } from './endpoint.js';
import {
  BCN_PROTOCOL_VERSION,
  MAX_FRAME_BYTES,
  asNonEmptyString,
  asRecord,
  parseFrame,
  type BcnFrame,
  type BotConnectResponse,
  type BotSession,
  type EventFrame,
  type RequestFrame,
  type ResponseFrame,
} from './protocol.js';

type RequestHandler = (frame: RequestFrame) => Promise<void>;
type EventHandler = (frame: EventFrame) => Promise<void>;

interface PendingRequest {
  resolve: (response: ResponseFrame) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  abortDispose?: () => void;
}

export interface BcnWsClientOptions {
  endpoint: ResolvedEndpoint;
  session: BotSession;
  config: Config;
  onSessionChanged: (session: BotSession) => Promise<void>;
  status?: () => 'idle' | 'busy';
  log?: {
    info(message: string): void;
    warn(message: string): void;
    error(message: string): void;
    debug?(message: string): void;
  };
}

export class BcnWsClient {
  private socket: WebSocket | undefined;
  private openingSocket: WebSocket | undefined;
  private connectedState = false;
  private stopped = true;
  private connecting = false;
  private reconnectDelayMs: number;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined;
  private requestCounter = 0;
  private eventSequence = 0;
  private pending = new Map<string, PendingRequest>();
  private requestHandlers = new Map<string, Set<RequestHandler>>();
  private eventHandlers = new Map<string, Set<EventHandler>>();
  private queuedEvents: Array<{ frame: EventFrame; bytes: number }> = [];
  private queuedEventBytes = 0;
  private session: BotSession;

  constructor(private readonly options: BcnWsClientOptions) {
    this.session = options.session;
    this.reconnectDelayMs = options.config.reconnectInitialMs;
  }

  get connected(): boolean {
    return this.connectedState && this.socket?.readyState === WebSocket.OPEN;
  }

  get botSession(): BotSession {
    return this.session;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.scheduleConnect(0);
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.connectedState = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.reconnectTimer = undefined;
    this.heartbeatTimer = undefined;
    this.rejectPending(new Error('BCN WebSocket client stopped'));
    const socket = this.socket;
    this.socket = undefined;
    const openingSocket = this.openingSocket;
    this.openingSocket = undefined;
    openingSocket?.terminate();
    if (!socket) return;
    socket.removeAllListeners();
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      await new Promise<void>(resolve => {
        const timer = setTimeout(() => {
          socket.terminate();
          resolve();
        }, 1_000);
        socket.once('close', () => {
          clearTimeout(timer);
          resolve();
        });
        socket.close();
      });
    }
  }

  onRequest(method: string, handler: RequestHandler): () => void {
    const handlers = this.requestHandlers.get(method) ?? new Set<RequestHandler>();
    handlers.add(handler);
    this.requestHandlers.set(method, handlers);
    return () => {
      handlers.delete(handler);
      if (handlers.size === 0) this.requestHandlers.delete(method);
    };
  }

  onEvent(event: string, handler: EventHandler): () => void {
    const handlers = this.eventHandlers.get(event) ?? new Set<EventHandler>();
    handlers.add(handler);
    this.eventHandlers.set(event, handlers);
    return () => {
      handlers.delete(handler);
      if (handlers.size === 0) this.eventHandlers.delete(event);
    };
  }

  sendResponse(
    id: string,
    ok: boolean,
    payload?: Record<string, unknown>,
    error?: ResponseFrame['error'],
  ): void {
    const frame: ResponseFrame = {
      type: 'res',
      id,
      ok,
      ...(payload ? { payload } : {}),
      ...(error ? { error } : {}),
    };
    this.sendFrameNow(frame);
  }

  sendEvent(event: string, payload: Record<string, unknown>): void {
    const frame: EventFrame = { type: 'event', event, payload, seq: ++this.eventSequence };
    if (this.connected) {
      this.sendFrameNow(frame);
      return;
    }
    const bytes = Buffer.byteLength(JSON.stringify(frame), 'utf8');
    if (bytes > MAX_FRAME_BYTES) throw new Error('BCN event exceeds 2 MiB');
    if (this.queuedEvents.length >= 1_000 || this.queuedEventBytes + bytes > 8 * MAX_FRAME_BYTES) {
      throw new Error('BCN reconnect event buffer is full');
    }
    this.queuedEvents.push({ frame, bytes });
    this.queuedEventBytes += bytes;
  }

  async sendRequest(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = this.options.config.connectionTimeoutMs,
    signal?: AbortSignal,
  ): Promise<ResponseFrame> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('BCN WebSocket is not connected');
    }
    if (signal?.aborted) throw new Error('BCN request was aborted');
    const id = this.nextRequestId();
    const response = new Promise<ResponseFrame>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`BCN request ${method} timed out`));
      }, timeoutMs);
      const pending: PendingRequest = { resolve, reject, timer };
      if (signal) {
        const onAbort = () => {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new Error(`BCN request ${method} was aborted`));
        };
        signal.addEventListener('abort', onAbort, { once: true });
        pending.abortDispose = () => signal.removeEventListener('abort', onAbort);
      }
      this.pending.set(id, pending);
    });
    try {
      this.sendFrameNow({ type: 'req', id, method, params });
    } catch (error) {
      const pending = this.pending.get(id);
      if (pending) {
        clearTimeout(pending.timer);
        pending.abortDispose?.();
        this.pending.delete(id);
      }
      throw error;
    }
    return response;
  }

  private scheduleConnect(delayMs: number): void {
    if (this.stopped || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      void this.connectOnce();
    }, delayMs);
  }

  private async connectOnce(): Promise<void> {
    if (this.stopped || this.connecting) return;
    this.connecting = true;
    try {
      const socket = await this.openSocket();
      if (this.stopped) {
        socket.close();
        return;
      }
      this.socket = socket;
      this.installSocketListeners(socket);
      const response = await this.sendRequest('bot.connect', {
        bot_id: this.session.botUuid,
        token: this.session.botToken,
        protocol_version: BCN_PROTOCOL_VERSION,
      });
      const connected = parseConnectResponse(response, this.session.botUuid);
      if (connected.token !== this.session.botToken) {
        const rotated = { ...this.session, botToken: connected.token };
        await this.options.onSessionChanged(rotated);
        this.session = rotated;
      }
      this.connectedState = true;
      this.reconnectDelayMs = this.options.config.reconnectInitialMs;
      this.flushQueuedEvents();
      this.startHeartbeat();
      this.options.log?.info(
        `Connected to BCN with Bot WebSocket protocol v${connected.protocol_version} (bot_uuid=${connected.bot_uuid})`,
      );
    } catch (error) {
      this.connectedState = false;
      const socket = this.socket;
      this.socket = undefined;
      socket?.removeAllListeners();
      socket?.terminate();
      this.rejectPending(new Error('BCN connection attempt failed'));
      if (!this.stopped) {
        this.options.log?.warn(`BCN connection failed; retrying in ${this.reconnectDelayMs}ms`);
        this.scheduleConnect(this.reconnectDelayMs);
        this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, this.options.config.reconnectMaxMs);
      }
      void error;
    } finally {
      this.connecting = false;
    }
  }

  private openSocket(): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      // COSEC: the endpoint was resolved and screened before this client is
      // created. The pinned lookup prevents DNS rebinding during the handshake.
      const socket = new WebSocket(this.options.endpoint.webSocketUrl, {
        lookup: this.options.endpoint.lookup,
        maxPayload: MAX_FRAME_BYTES,
        handshakeTimeout: this.options.config.connectionTimeoutMs,
      });
      this.openingSocket = socket;
      let settled = false;
      const cleanup = () => {
        if (this.openingSocket === socket) this.openingSocket = undefined;
        socket.off('open', onOpen);
        socket.off('error', onError);
        socket.off('close', onClose);
      };
      const onOpen = () => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(socket);
      };
      const onError = () => {
        if (settled) return;
        settled = true;
        cleanup();
        socket.terminate();
        reject(new Error('BCN WebSocket handshake failed'));
      };
      const onClose = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new Error('BCN WebSocket closed during handshake'));
      };
      socket.once('open', onOpen);
      socket.once('error', onError);
      socket.once('close', onClose);
    });
  }

  private installSocketListeners(socket: WebSocket): void {
    socket.on('message', data => this.handleMessage(data));
    socket.on('error', () => {
      if (!this.stopped) this.options.log?.warn('BCN WebSocket reported a transport error');
    });
    socket.on('close', () => {
      if (socket !== this.socket) return;
      this.connectedState = false;
      this.socket = undefined;
      if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = undefined;
      this.rejectPending(new Error('BCN WebSocket disconnected'));
      if (!this.stopped) this.scheduleConnect(this.reconnectDelayMs);
    });
  }

  private handleMessage(data: RawData): void {
    const buffer = Buffer.isBuffer(data)
      ? data
      : Array.isArray(data)
        ? Buffer.concat(data)
        : Buffer.from(data as ArrayBuffer);
    if (buffer.byteLength > MAX_FRAME_BYTES) {
      this.options.log?.warn('Ignoring oversized BCN WebSocket frame');
      return;
    }
    let value: unknown;
    try {
      value = JSON.parse(buffer.toString('utf8'));
    } catch {
      this.options.log?.warn('Ignoring malformed BCN WebSocket JSON');
      return;
    }
    const frame = parseFrame(value);
    if (!frame) {
      this.options.log?.warn('Ignoring invalid BCN WebSocket frame');
      return;
    }
    if (frame.type === 'res') {
      this.handleResponse(frame);
    } else if (frame.type === 'req') {
      void this.handleRequest(frame);
    } else {
      void this.handleEvent(frame);
    }
  }

  private handleResponse(frame: ResponseFrame): void {
    const pending = this.pending.get(frame.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    pending.abortDispose?.();
    this.pending.delete(frame.id);
    pending.resolve(frame);
  }

  private async handleRequest(frame: RequestFrame): Promise<void> {
    const handlers = this.requestHandlers.get(frame.method);
    if (!handlers?.size) {
      this.sendResponse(frame.id, false, undefined, {
        code: 'NOT_FOUND',
        message: `Unsupported BCN method: ${frame.method}`,
        retryable: false,
      });
      return;
    }
    try {
      for (const handler of handlers) await handler(frame);
    } catch {
      this.sendResponse(frame.id, false, undefined, {
        code: 'INTERNAL_ERROR',
        message: 'DeepSeek Harness could not process the BCN request',
        retryable: false,
      });
    }
  }

  private async handleEvent(frame: EventFrame): Promise<void> {
    const handlers = this.eventHandlers.get(frame.event);
    if (!handlers) return;
    for (const handler of handlers) {
      try {
        await handler(frame);
      } catch {
        this.options.log?.warn(`BCN event handler failed for ${frame.event}`);
      }
    }
  }

  private startHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (!this.connected) return;
      void this.sendRequest('bot.status', {
        status: this.options.status?.() ?? 'idle',
        dynamic_summary: 'DeepSeek Harness BCN channel active',
        load: this.options.status?.() === 'busy' ? 1 : 0,
      }).catch(() => {
        this.options.log?.debug?.('BCN heartbeat did not receive a response');
      });
    }, this.options.config.heartbeatIntervalMs);
  }

  private flushQueuedEvents(): void {
    const queued = this.queuedEvents;
    this.queuedEvents = [];
    this.queuedEventBytes = 0;
    for (const item of queued) this.sendFrameNow(item.frame);
  }

  private sendFrameNow(frame: BcnFrame): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('BCN WebSocket is not open');
    }
    const body = JSON.stringify(frame);
    if (Buffer.byteLength(body, 'utf8') > MAX_FRAME_BYTES) throw new Error('BCN frame exceeds 2 MiB');
    this.socket.send(body);
  }

  private rejectPending(error: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.abortDispose?.();
      pending.reject(error);
      this.pending.delete(id);
    }
  }

  private nextRequestId(): string {
    this.requestCounter += 1;
    return `dsh-${this.requestCounter.toString(36)}-${randomUUID().slice(0, 8)}`;
  }
}

function parseConnectResponse(response: ResponseFrame, expectedBotUuid: string): BotConnectResponse {
  if (!response.ok) throw new Error(`BCN bot.connect failed with code ${response.error?.code ?? 'UNKNOWN'}`);
  const payload = asRecord(response.payload);
  const botUuid = asNonEmptyString(payload?.bot_uuid);
  const token = asNonEmptyString(payload?.token);
  const protocolVersion = payload?.protocol_version;
  if (!botUuid || !token || typeof payload?.is_new !== 'boolean' || !Number.isSafeInteger(protocolVersion)) {
    throw new Error('BCN bot.connect returned an invalid response');
  }
  if (botUuid !== expectedBotUuid) throw new Error('BCN bot.connect returned a different Bot identity');
  if (protocolVersion !== BCN_PROTOCOL_VERSION) {
    throw new Error(`BCN selected unsupported Bot WebSocket protocol version ${String(protocolVersion)}`);
  }
  const envRecord = payload.env === undefined ? undefined : asRecord(payload.env);
  if (payload.env !== undefined && !envRecord) throw new Error('BCN bot.connect returned an invalid env map');
  const env = envRecord ? normalizeStringMap(envRecord) : undefined;
  return {
    is_new: payload.is_new,
    token,
    bot_uuid: botUuid,
    protocol_version: protocolVersion,
    ...(typeof payload.min_supported_version === 'number'
      ? { min_supported_version: payload.min_supported_version }
      : {}),
    ...(env ? { env } : {}),
  };
}

function normalizeStringMap(value: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!key || typeof item !== 'string') throw new Error('BCN bot.connect returned an invalid env map');
    result[key] = item;
  }
  return result;
}
