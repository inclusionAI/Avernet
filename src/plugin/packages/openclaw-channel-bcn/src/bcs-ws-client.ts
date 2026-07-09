/**
 * BCS WebSocket client — ported from Python client.py.
 *
 * Connects to BCS via WebSocket long-connection, handles:
 * - bot.connect registration (with token in URL query)
 * - bot.status heartbeat
 * - Receiving requests (chat.send, chat.inject) from BCS
 * - Sending response/event frames back to BCS
 * - Session persistence for reconnection
 */

import WebSocket from 'ws';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import type {
  RequestFrame,
  ResponseFrame,
  EventFrame,
  BcsFrame,
  ResolvedBcsAccount,
  SessionInfo,
} from './types.js';

type RequestHandler = (request: RequestFrame) => Promise<void>;

interface PendingRequest {
  resolve: (response: ResponseFrame) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export function sanitizeBcsUrlForLog(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.searchParams.has('token')) {
      parsed.searchParams.set('token', '[REDACTED]');
    }
    return parsed.toString();
  } catch {
    return url.replace(/([?&]token=)[^&]*/gi, '$1[REDACTED]');
  }
}

export function isValidBcsWebSocketUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === 'ws:' || parsed.protocol === 'wss:') && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

function assertValidBcsWebSocketUrl(url: string): void {
  if (!isValidBcsWebSocketUrl(url)) {
    throw new Error(`Invalid BCS WebSocket URL: ${sanitizeBcsUrlForLog(url)}`);
  }
}

export interface BcsWsClientOptions {
  account: ResolvedBcsAccount;
  /** Optional data directory override. If not provided, uses OPENCLAW_DATA_DIR env or ~/.openclaw */
  dataDir?: string;
  resolveConnectBotId?: () => string | undefined;
  log?: {
    info: (...args: unknown[]) => void;
    warn: (...args: unknown[]) => void;
    error: (...args: unknown[]) => void;
    debug?: (...args: unknown[]) => void;
  };
}

export class BcsWsClient {
  private _ws: WebSocket | null = null;
  private _connected = false;
  private _closing = false;
  private _sessionToken: string | null = null;
  private _botUuid: string | null = null;
  private _dataDir: string | null = null;

  private _requestHandlers = new Map<string, RequestHandler>();
  private _pendingRequests = new Map<string, PendingRequest>();

  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private _requestIdCounter = 0;

  private readonly _account: ResolvedBcsAccount;
  private readonly _log: BcsWsClientOptions['log'];
  private readonly _resolveConnectBotId?: () => string | undefined;

  constructor(options: BcsWsClientOptions) {
    this._account = options.account;
    this._log = options.log;
    this._dataDir = options.dataDir ?? null;
    this._resolveConnectBotId = options.resolveConnectBotId;
  }

  get botUuid(): string | null {
    return this._botUuid;
  }

  get connected(): boolean {
    return this._connected && this._ws?.readyState === WebSocket.OPEN;
  }

  get sessionToken(): string | null {
    return this._sessionToken;
  }

  /** Register a handler for BCS requests (e.g. chat.send). */
  onRequest(method: string, handler: RequestHandler): void {
    this._requestHandlers.set(method, handler);
  }

  /** Connect to BCS and complete registration. */
  async connect(session?: SessionInfo | null): Promise<void> {
    if (this._connected) return;

    // Prefer explicitly provided session (e.g. from waitForSession in non-prod),
    // otherwise fall back to loading from file (with URL mismatch check).
    const connectBotId = this._resolveExplicitConnectBotId();
    const savedSession = this._selectSessionForConnect(session ?? this._loadSession(), connectBotId);
    const token = savedSession?.token;

    const connectUrl = this._account.bcsUrl;
    assertValidBcsWebSocketUrl(connectUrl);
    this._log?.info?.(`Connecting to BCS: ${sanitizeBcsUrlForLog(connectUrl)}`);

    if (token) {
      this._log?.info?.(`Using saved session token for reconnection (bot_uuid=${savedSession?.bot_uuid})`);
    } else {
      this._log?.info?.('No saved session found, starting fresh connection');
    }

    const ws = await this._openWebSocket(connectUrl);
    this._ws = ws;
    this._connected = true;
    this._closing = false;

    // Set up message handling
    ws.on('message', data => this._handleMessage(data));

    try {
      // Connect bot — pass the session so _connect can use bot_uuid and token
      await this._connect(savedSession, connectBotId);
    } catch (err) {
      await this.disconnect();
      throw err;
    }

    // Start heartbeat
    this._startHeartbeat();

    this._log?.info?.(`Connected to BCS, bot_uuid=${this._botUuid}`);
  }

  /** Disconnect from BCS. */
  async disconnect(): Promise<void> {
    this._closing = true;
    this._connected = false;

    this._stopHeartbeat();

    // Reject all pending requests
    for (const [ id, pending ] of this._pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error('Client disconnecting'));
      this._pendingRequests.delete(id);
    }

    if (this._ws) {
      this._ws.removeAllListeners();
      if (this._ws.readyState === WebSocket.OPEN) {
        this._ws.close();
      }
      this._ws = null;
    }

    this._sessionToken = null;
    this._closing = false;
    this._log?.info?.('Disconnected from BCS');
  }

  /** Send a ResponseFrame to BCS. */
  sendResponse(
    requestId: string,
    ok: boolean,
    payload?: Record<string, unknown>,
    error?: Record<string, unknown>,
  ): void {
    const frame: ResponseFrame = {
      type: 'res',
      id: requestId,
      ok,
      ...(payload !== undefined ? { payload } : {}),
      ...(error !== undefined ? { error: error as any } : {}),
    };
    this._send(frame);
  }

  /** Send an EventFrame to BCS. */
  sendEvent(event: string, payload: Record<string, unknown>, seq: number): void {
    const frame: EventFrame = {
      type: 'event',
      event,
      payload,
      seq,
    };
    this._send(frame);
  }

  /** Send a request to BCS and wait for the response. */
  async sendRequest(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = 30_000,
  ): Promise<ResponseFrame> {
    const requestId = this._nextRequestId();
    const frame: RequestFrame = { type: 'req', id: requestId, method, params };
    this._send(frame);
    return this._waitResponse(requestId, timeoutMs);
  }

  // ── Internal ──────────────────────────────────────────────────────────

  private _nextRequestId(): string {
    return (++this._requestIdCounter).toString(36).padStart(6, '0');
  }

  private _openWebSocket(url: string): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      const logUrl = sanitizeBcsUrlForLog(url);
      let ws: WebSocket | null = null;
      const timeout = setTimeout(() => {
        if (!ws) {
          reject(new Error(`BCS connection timeout: ${logUrl}`));
          return;
        }
        ws.removeAllListeners();
        ws.on('error', err => {
          this._log?.warn?.(`WebSocket error during timeout cleanup: ${err.message}`);
        });
        ws.terminate();
        reject(new Error(`BCS connection timeout: ${logUrl}`));
      }, this._account.connectionTimeoutMs);

      const wsOptions: WebSocket.ClientOptions = {
        maxPayload: 2 * 1024 * 1024,
      };

      this._log?.info?.(
        `WebSocket connecting to ${logUrl}`,
      );

      ws = new WebSocket(url, wsOptions);

      // Handle HTTP responses other than 101 Switching Protocols
      ws.on('unexpected-response', (_req, res) => {
        clearTimeout(timeout);
        let body = '';
        res.on('data', chunk => (body += chunk));
        res.on('end', () => {
          this._log?.error?.(
            `BCS returned HTTP ${res.statusCode} ${res.statusMessage}\n` +
              `Headers: ${JSON.stringify(res.headers)}\n` +
              `Body: ${body.slice(0, 500)}`,
          );
          reject(
            new Error(
              `Unexpected server response: ${res.statusCode} ${res.statusMessage}`,
            ),
          );
        });
      });

      ws.on('open', () => {
        clearTimeout(timeout);
        this._log?.info?.(`WebSocket TCP handshake succeeded: ${logUrl}`);
        resolve(ws);
      });

      ws.on('error', err => {
        clearTimeout(timeout);
        ws.removeAllListeners();
        this._log?.error?.(`WebSocket connection error: ${err.message}`);
        reject(new Error(`Failed to connect to BCS: ${err.message}`));
      });
    });
  }

  private async _connect(preferredSession?: SessionInfo | null, explicitConnectBotId?: string): Promise<void> {
    const requestId = this._nextRequestId();

    // Determine bot_id: explicit config/resolver > preferred session (from waitForSession/connect) > file session > none
    const savedSession = this._selectSessionForConnect(
      preferredSession ?? this._loadSession(),
      explicitConnectBotId,
    );
    const botId = explicitConnectBotId ?? savedSession?.bot_uuid ?? undefined;

    if (botId) {
      this._log?.info?.(`Using bot_id: ${botId} (source: ${explicitConnectBotId ? 'config' : 'session'})`);
    }

    // bot.connect with optional bot_id and token
    const token = savedSession?.token;
    const frame: RequestFrame = {
      type: 'req',
      id: requestId,
      method: 'bot.connect',
      params: {
        ...(botId ? { bot_id: botId } : {}),
        ...(token ? { token } : {}),
        protocol_version: 2,
      },
    };

    this._log?.info?.(`Sending bot.connect (id=${requestId}, bot_id=${botId ?? 'none'}, token=${token ? 'present' : 'none'})`);
    this._send(frame);

    const response = await this._waitResponse(requestId, this._account.connectionTimeoutMs);
    if (!response.ok) {
      const errorMsg = response.error?.message ?? 'Unknown error';
      const errorCode = response.error?.code ?? 'unknown';

      // Special handling for bot_id conflict
      if (errorCode === 'bot_id_conflict') {
        this._log?.error?.(
          `Bot ID conflict: ${errorMsg}. ` +
            'This may indicate another instance is already running with the same bot_id.',
        );
      }

      throw new Error(`Bot connection failed: ${errorMsg}`);
    }

    const payload = response.payload as any;
    // BCS uses snake_case for JSON fields (serde default)
    this._botUuid = payload?.bot_uuid ?? null;
    this._sessionToken = payload?.token ?? null;
    const isNew = payload?.is_new ?? true;
    const protocolVersion = payload?.protocol_version ?? 1;

    if (explicitConnectBotId && this._botUuid && this._botUuid !== explicitConnectBotId) {
      throw new Error(
        `BCS connected bot_uuid ${this._botUuid} does not match configured bot_id ${explicitConnectBotId}`,
      );
    }

    // Warn if BCS signals protocol deprecation
    const deprecation = payload?.deprecation;
    if (deprecation) {
      this._log?.warn?.(
        `[BCS] Protocol deprecation notice: ${deprecation.message}` +
        (deprecation.sunset_date ? ` (sunset: ${deprecation.sunset_date})` : ''),
      );
    }

    this._log?.info?.(`Protocol version: ${protocolVersion}`);

    // Save session for reconnection
    // Save session even if botUuid is null (new bot awaiting onboarding)
    if (this._sessionToken) {
      this._saveSession({
        bot_uuid: this._botUuid, // may be null for new bots
        token: this._sessionToken,
        bcs_url: savedSession?.bcs_url ?? this._account.bcsUrl,
      });
    }

    this._log?.info?.(
      `Bot connected: bot_uuid=${this._botUuid}, is_new=${isNew}, token=${this._sessionToken ? 'present' : 'none'}`,
    );
  }

  /**
   * Poll until session.json exists and contains valid session info.
   * Returns the loaded session, or null if aborted.
   */
  async waitForSession(opts?: {
    pollIntervalMs?: number;
    abortSignal?: AbortSignal;
  }): Promise<SessionInfo | null> {
    const interval = opts?.pollIntervalMs ?? 3000;
    const signal = opts?.abortSignal;

    while (!signal?.aborted) {
      // Read session file directly, skipping URL mismatch check —
      // the session may have been written by the main process with a different bcsUrl format.
      try {
        const sessionPath = this._getSessionFilePath();
        if (fs.existsSync(sessionPath)) {
          const content = fs.readFileSync(sessionPath, 'utf-8');
          const session = JSON.parse(content) as SessionInfo;
          if (session?.token && session?.bcs_url) {
            this._log?.info?.(`Session file ready: bot_uuid=${session.bot_uuid}, bcs_url=${sanitizeBcsUrlForLog(session.bcs_url)}`);
            return session;
          }
        }
      } catch {
        // ignore read errors, retry
      }

      this._log?.info?.(`Session file not ready, polling again in ${interval}ms...`);
      await new Promise<void>(resolve => {
        const timer = setTimeout(resolve, interval);
        signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
      });
    }

    return null;
  }

  // ── Session persistence ─────────────────────────────────────────────────

  private _getSessionFilePath(): string {
    const dataDir = this._dataDir || process.env.OPENCLAW_DATA_DIR || path.join(os.homedir(), '.openclaw');
    const bcsDir = path.join(dataDir, '.bcs');
    return path.join(bcsDir, 'session.json');
  }

  private _resolveExplicitConnectBotId(): string | undefined {
    const resolved = this._resolveConnectBotId?.()?.trim();
    if (resolved) return resolved;
    return this._account.connectBotId?.trim() || undefined;
  }

  private _selectSessionForConnect(
    session: SessionInfo | null,
    explicitConnectBotId?: string,
  ): SessionInfo | null {
    if (!session || !explicitConnectBotId) {
      return session;
    }
    if (session.bot_uuid === explicitConnectBotId) {
      return session;
    }
    this._log?.warn?.(
      `Ignoring saved BCS session for bot_uuid=${session.bot_uuid ?? 'none'} because configured bot_id=${explicitConnectBotId}`,
    );
    return null;
  }

  private _loadSession(): SessionInfo | null {
    try {
      const sessionPath = this._getSessionFilePath();
      if (!fs.existsSync(sessionPath)) {
        return null;
      }
      const content = fs.readFileSync(sessionPath, 'utf-8');
      const session = JSON.parse(content) as SessionInfo;
      // Verify URL matches
      if (session.bcs_url !== this._account.bcsUrl) {
        this._log?.warn?.(
          `Session file URL mismatch: expected ${sanitizeBcsUrlForLog(this._account.bcsUrl)}, got ${sanitizeBcsUrlForLog(session.bcs_url)}`,
        );
        return null;
      }
      return session;
    } catch (err) {
      this._log?.warn?.(`Failed to load session: ${err}`);
      return null;
    }
  }

  private _saveSession(session: SessionInfo): void {
    try {
      const sessionPath = this._getSessionFilePath();
      const bcsDir = path.dirname(sessionPath);
      if (!fs.existsSync(bcsDir)) {
        fs.mkdirSync(bcsDir, { recursive: true });
      }
      fs.writeFileSync(sessionPath, JSON.stringify(session, null, 2), {
        encoding: 'utf-8',
        mode: 0o600,
      });
      this._log?.info?.(`Session saved to ${sessionPath}`);
    } catch (err) {
      this._log?.error?.(`Failed to save session: ${err}`);
    }
  }

  private _waitResponse(requestId: string, timeoutMs: number): Promise<ResponseFrame> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pendingRequests.delete(requestId);
        reject(new Error(`BCS response timeout for request ${requestId}`));
      }, timeoutMs);

      this._pendingRequests.set(requestId, { resolve, reject, timer });
    });
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat();
    this._log?.info?.(`Heartbeat started (interval=${this._account.heartbeatIntervalMs}ms)`);
    this._heartbeatTimer = setInterval(() => {
      if (!this.connected) return;

      const requestId = this._nextRequestId();
      const frame: RequestFrame = {
        type: 'req',
        id: requestId,
        method: 'bot.status',
        params: {
          status: 'idle',
          dynamic_summary: 'Running',
          load: 0.0,
        },
      };
      this._send(frame);
      this._log?.debug?.('Heartbeat sent');
    }, this._account.heartbeatIntervalMs);
  }

  private _stopHeartbeat(): void {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }

  private _send(frame: BcsFrame): void {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
      this._log?.warn?.('Cannot send frame: WebSocket not open');
      return;
    }
    this._ws.send(JSON.stringify(frame));
  }

  private _handleMessage(data: WebSocket.RawData): void {
    if (this._closing) return;

    let parsed: any;
    try {
      parsed = JSON.parse(data.toString());
    } catch {
      this._log?.warn?.('Invalid JSON from BCS');
      return;
    }

    const frameType = parsed?.type;

    if (frameType === 'res') {
      this._handleResponse(parsed as ResponseFrame);
    } else if (frameType === 'req') {
      this._handleRequest(parsed as RequestFrame);
    } else if (frameType === 'event') {
      this._log?.debug?.(`Received event from BCS: ${parsed.event}`);
    }
  }

  private _handleResponse(response: ResponseFrame): void {
    const pending = this._pendingRequests.get(response.id);
    if (pending) {
      clearTimeout(pending.timer);
      this._pendingRequests.delete(response.id);
      pending.resolve(response);
    }
  }

  private _handleRequest(request: RequestFrame): void {
    const handler = this._requestHandlers.get(request.method);
    if (handler) {
      handler(request).catch(err => {
        this._log?.error?.(`Error handling BCS request ${request.method}:`, err);
        this.sendResponse(request.id, false, undefined, {
          code: 'INTERNAL_ERROR',
          message: err instanceof Error ? err.message : String(err),
          retryable: false,
        });
      });
    } else {
      this._log?.warn?.(`No handler for BCS request: ${request.method}`);
      this.sendResponse(request.id, false, undefined, {
        code: 'NOT_FOUND',
        message: `Unknown method: ${request.method}`,
        retryable: false,
      });
    }
  }

  /** Set a callback for when the WebSocket closes unexpectedly. */
  onClose(callback: (code: number, reason: string) => void): void {
    this._ws?.on('close', (code, reason) => {
      this._connected = false;
      this._stopHeartbeat();
      if (!this._closing) {
        callback(code, reason.toString());
      }
    });
    this._ws?.on('error', err => {
      if (!this._closing) {
        this._log?.warn?.(`BCS WebSocket error: ${err.message}`);
      }
    });
  }
}
