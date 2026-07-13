import { randomUUID } from 'node:crypto';
import { resolveGatewayPort, scopesForGatewayMethod } from './gateway-security.js';

const CHANNEL_ID = 'bcs';
const DEFAULT_INTERVAL_MS = 60 * 60 * 1000;
const MAX_DELETES_PER_RUN = 100;
const OPENCLAW_GATEWAY_MIN_PROTOCOL = 3;
const OPENCLAW_GATEWAY_MAX_PROTOCOL = 4;

export interface BcsSessionCleanupLog {
  info?: (...args: unknown[]) => void;
  warn?: (...args: unknown[]) => void;
  error?: (...args: unknown[]) => void;
}

export interface ResolvedBcsSessionCleanupConfig {
  enabled: boolean;
  pruneAfterMs: number;
  intervalMs: number;
  maxDeletesPerRun: number;
  disabledReason?: string;
}

export interface BcsSessionCleanupSession {
  key: string;
  updatedAt?: number | string | Date | null;
  channel?: unknown;
  groupChannel?: unknown;
  chatType?: unknown;
  deliveryContext?: unknown;
  origin?: unknown;
  lastChannel?: unknown;
  hasActiveRun?: boolean;
}

export interface BcsSessionCleanupGateway {
  listSessions(): Promise<BcsSessionCleanupSession[]>;
  deleteSession(key: string, params: { deleteTranscript: boolean }): Promise<void>;
}

export interface BcsSessionCleanupResult {
  scanned: number;
  candidates: number;
  deleted: number;
  failed: number;
  skippedActive: number;
  skippedFresh: number;
  skippedNonBcs: number;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function parseDurationMs(value: unknown): number | undefined {
  const numeric = numberValue(value);
  if (numeric !== undefined) return numeric > 0 ? numeric : undefined;

  const raw = stringValue(value);
  if (!raw) return undefined;

  const match = raw.match(/^(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)$/i);
  if (!match) return undefined;

  const amount = Number(match[1]);
  if (!Number.isFinite(amount) || amount <= 0) return undefined;

  const unit = match[2].toLowerCase();
  if (unit === 'ms' || unit.startsWith('millisecond')) return amount;
  if (unit === 's' || unit === 'sec' || unit.startsWith('second')) return amount * 1000;
  if (unit === 'm' || unit === 'min' || unit.startsWith('minute')) return amount * 60 * 1000;
  if (unit === 'h' || unit === 'hr' || unit.startsWith('hour')) return amount * 60 * 60 * 1000;
  if (unit === 'd' || unit.startsWith('day')) return amount * 24 * 60 * 60 * 1000;
  return undefined;
}

function getChannelCleanupConfig(cfg: unknown): Record<string, unknown> | undefined {
  const root = asRecord(cfg);
  const channels = asRecord(root?.channels);
  const bcs = asRecord(channels?.bcs);
  return asRecord(bcs?.sessionCleanup);
}

export function resolveBcsSessionCleanupConfig(cfg: unknown): ResolvedBcsSessionCleanupConfig {
  const raw = getChannelCleanupConfig(cfg);
  if (!raw) {
    return {
      enabled: false,
      pruneAfterMs: 0,
      intervalMs: DEFAULT_INTERVAL_MS,
      maxDeletesPerRun: MAX_DELETES_PER_RUN,
    };
  }

  const pruneAfterMs = parseDurationMs(raw.pruneAfter);
  if (pruneAfterMs === undefined) {
    return {
      enabled: false,
      pruneAfterMs: 0,
      intervalMs: DEFAULT_INTERVAL_MS,
      maxDeletesPerRun: MAX_DELETES_PER_RUN,
      disabledReason: 'channels.bcs.sessionCleanup.pruneAfter must be a positive duration, for example 2d',
    };
  }

  const intervalMinutes = numberValue(raw.intervalMinutes);
  if (intervalMinutes === undefined || intervalMinutes <= 0) {
    return {
      enabled: false,
      pruneAfterMs,
      intervalMs: DEFAULT_INTERVAL_MS,
      maxDeletesPerRun: MAX_DELETES_PER_RUN,
      disabledReason: 'channels.bcs.sessionCleanup.intervalMinutes must be a positive number',
    };
  }

  return {
    enabled: true,
    pruneAfterMs,
    intervalMs: intervalMinutes * 60 * 1000,
    maxDeletesPerRun: MAX_DELETES_PER_RUN,
  };
}

function getUpdatedAtMs(session: BcsSessionCleanupSession): number | undefined {
  const value = session.updatedAt;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (value instanceof Date) {
    const ms = value.getTime();
    return Number.isFinite(ms) ? ms : undefined;
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function isBcsText(value: unknown): boolean {
  return stringValue(value)?.toLowerCase() === CHANNEL_ID;
}

function recordHasBcsChannel(value: unknown): boolean {
  const record = asRecord(value);
  return Boolean(record && (isBcsText(record.channel) || isBcsText(record.provider) || isBcsText(record.surface)));
}

function isBcsSessionKey(key: string): boolean {
  if (key === CHANNEL_ID || key.startsWith(`${CHANNEL_ID}:`) || key.startsWith(`${CHANNEL_ID}-`)) {
    return true;
  }
  const agentMatch = key.match(/^agent:[^:]+:(.+)$/);
  const scoped = agentMatch?.[1];
  return Boolean(
    scoped &&
      (scoped === CHANNEL_ID || scoped.startsWith(`${CHANNEL_ID}:`) || scoped.startsWith(`${CHANNEL_ID}-`)),
  );
}

function isMainLikeSessionKey(key: string): boolean {
  return key === 'main' || key === 'global' || key === 'unknown' || /^agent:[^:]+:main$/.test(key);
}

function isBcsSession(session: BcsSessionCleanupSession): boolean {
  return (
    isBcsSessionKey(session.key) ||
    isBcsText(session.channel) ||
    isBcsText(session.groupChannel) ||
    isBcsText(session.lastChannel) ||
    recordHasBcsChannel(session.lastChannel) ||
    recordHasBcsChannel(session.deliveryContext) ||
    recordHasBcsChannel(session.origin)
  );
}

export async function runBcsSessionCleanupOnce(params: {
  cleanup: ResolvedBcsSessionCleanupConfig;
  gateway: BcsSessionCleanupGateway;
  now?: number;
  log?: BcsSessionCleanupLog;
}): Promise<BcsSessionCleanupResult> {
  const result: BcsSessionCleanupResult = {
    scanned: 0,
    candidates: 0,
    deleted: 0,
    failed: 0,
    skippedActive: 0,
    skippedFresh: 0,
    skippedNonBcs: 0,
  };

  if (!params.cleanup.enabled) {
    return result;
  }

  const now = params.now ?? Date.now();
  const cutoffMs = now - params.cleanup.pruneAfterMs;
  const sessions = await params.gateway.listSessions();
  result.scanned = sessions.length;

  params.log?.info?.(
    `[BCS sessionCleanup] scan started sessions=${sessions.length} pruneAfterMs=${params.cleanup.pruneAfterMs} cutoff=${new Date(cutoffMs).toISOString()}`,
  );

  for (const session of sessions) {
    if (result.deleted >= params.cleanup.maxDeletesPerRun) break;

    if (isMainLikeSessionKey(session.key)) {
      result.skippedNonBcs += 1;
      continue;
    }
    if (!isBcsSession(session)) {
      result.skippedNonBcs += 1;
      continue;
    }
    if (session.hasActiveRun === true) {
      result.skippedActive += 1;
      continue;
    }

    const updatedAtMs = getUpdatedAtMs(session);
    if (updatedAtMs === undefined || updatedAtMs > cutoffMs) {
      result.skippedFresh += 1;
      continue;
    }

    result.candidates += 1;
    const ageMs = Math.max(0, now - updatedAtMs);
    params.log?.info?.(
      `[BCS sessionCleanup] deleting stale BCS session key=${session.key} ageMs=${ageMs} reason=stale-bcs-session deleteTranscript=true`,
    );

    try {
      await params.gateway.deleteSession(session.key, { deleteTranscript: true });
      result.deleted += 1;
    } catch (err) {
      result.failed += 1;
      params.log?.warn?.(
        `[BCS sessionCleanup] failed to delete session key=${session.key}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  params.log?.info?.(
    `[BCS sessionCleanup] scan completed scanned=${result.scanned} candidates=${result.candidates} deleted=${result.deleted} failed=${result.failed} skippedActive=${result.skippedActive} skippedFresh=${result.skippedFresh} skippedNonBcs=${result.skippedNonBcs}`,
  );
  return result;
}

async function loadOrCreateDeviceKeypair(keyFile: string): Promise<{
  privateKey: import('node:crypto').KeyObject;
  publicKeyB64: string;
  deviceId: string;
}> {
  const fs = await import('node:fs');
  const crypto = await import('node:crypto');
  const path = await import('node:path');

  const dir = path.dirname(keyFile);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  let privateKey: import('node:crypto').KeyObject;
  if (fs.existsSync(keyFile)) {
    privateKey = crypto.createPrivateKey(fs.readFileSync(keyFile, 'utf8'));
  } else {
    const { privateKey: generated } = crypto.generateKeyPairSync('ed25519');
    privateKey = generated;
    const pem = generated.export({ type: 'pkcs8', format: 'pem' }) as string;
    fs.writeFileSync(keyFile, pem, { mode: 0o600 });
  }

  const publicKey = crypto.createPublicKey(privateKey);
  const pubBytes = publicKey.export({ type: 'spki', format: 'der' }).slice(-32);
  const publicKeyB64 = pubBytes.toString('base64url');
  const deviceId = crypto.createHash('sha256').update(pubBytes).digest('hex');
  return { privateKey, publicKeyB64, deviceId };
}

async function callGatewayRpc(params: {
  cfg: Record<string, unknown>;
  method: string;
  rpcParams: Record<string, unknown>;
  timeoutMs?: number;
  dataDir?: string;
  log?: BcsSessionCleanupLog;
}): Promise<unknown> {
  const port = resolveGatewayPort(params.cfg);
  const token = stringValue(asRecord(asRecord(params.cfg.gateway)?.auth)?.token);
  if (!token) {
    throw new Error('No gateway token found');
  }

  const os = await import('node:os');
  const path = await import('node:path');
  const crypto = await import('node:crypto');
  const WebSocket = (await import('ws')).default;
  const keyFile = path.join(params.dataDir || os.homedir(), '.openclaw', 'bcs_device_key.pem');
  const { privateKey, publicKeyB64, deviceId } = await loadOrCreateDeviceKeypair(keyFile);
  const ws = new WebSocket(`ws://127.0.0.1:${port}`);

  return await new Promise<unknown>((resolve, reject) => {
    const connectId = `connect-${randomUUID()}`;
    const reqId = `cleanup-${randomUUID()}`;
    const timeoutMs = params.timeoutMs ?? 8000;
    let connected = false;
    let settled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    const finish = (ok: boolean, value: unknown) => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      ws.close();
      if (ok) {
        resolve(value);
      } else {
        reject(value instanceof Error ? value : new Error(String(value)));
      }
    };

    timeout = setTimeout(() => {
      finish(false, new Error(`gateway ${params.method} timeout`));
    }, timeoutMs);

    ws.on('message', data => {
      let frame: Record<string, unknown>;
      try {
        const parsed = JSON.parse(data.toString());
        const record = asRecord(parsed);
        if (!record) return;
        frame = record;
      } catch {
        return;
      }

      if (frame.type === 'event' && frame.event === 'connect.challenge') {
        const payload = asRecord(frame.payload);
        const nonce = stringValue(payload?.nonce) ?? '';
        const signedAtMs = Date.now();
        const scopes = scopesForGatewayMethod(params.method);
        const payloadStr = [
          'v3',
          deviceId,
          'gateway-client',
          'backend',
          'operator',
          scopes.join(','),
          String(signedAtMs),
          token,
          nonce,
          'node',
          '',
        ].join('|');
        const signature = crypto.sign(null, Buffer.from(payloadStr, 'utf8'), privateKey).toString('base64url');
        ws.send(JSON.stringify({
          type: 'req',
          id: connectId,
          method: 'connect',
          params: {
            minProtocol: OPENCLAW_GATEWAY_MIN_PROTOCOL,
            maxProtocol: OPENCLAW_GATEWAY_MAX_PROTOCOL,
            client: { id: 'gateway-client', version: '1.0.0', platform: 'node', mode: 'backend' },
            auth: { token },
            scopes,
            role: 'operator',
            device: { id: deviceId, publicKey: publicKeyB64, signature, signedAt: signedAtMs, nonce },
          },
        }));
        return;
      }

      if (!connected && frame.id === connectId) {
        if (frame.ok !== true) {
          finish(false, new Error(`gateway connect failed: ${JSON.stringify(frame.error)}`));
          return;
        }
        connected = true;
        ws.send(JSON.stringify({
          type: 'req',
          id: reqId,
          method: params.method,
          params: params.rpcParams,
        }));
        return;
      }

      if (frame.id === reqId) {
        if (frame.ok === true) {
          finish(true, frame.payload);
        } else {
          finish(false, new Error(`gateway ${params.method} failed: ${JSON.stringify(frame.error)}`));
        }
      }
    });

    ws.on('error', err => {
      finish(false, new Error(`gateway connection error: ${err.message}`));
    });

    ws.on('close', () => {
      clearTimeout(timeout);
    });
  });
}

export function createBcsSessionCleanupGateway(params: {
  cfg: Record<string, unknown>;
  dataDir?: string;
  log?: BcsSessionCleanupLog;
}): BcsSessionCleanupGateway {
  return {
    async listSessions() {
      const payload = await callGatewayRpc({
        ...params,
        method: 'sessions.list',
        rpcParams: { includeGlobal: false, includeUnknown: false },
      });
      const sessions = asRecord(payload)?.sessions;
      return Array.isArray(sessions)
        ? sessions
          .map(session => asRecord(session))
          .filter((session): session is Record<string, unknown> & { key: string } => (
            Boolean(session?.key && typeof session.key === 'string')
          ))
          .map(session => session as unknown as BcsSessionCleanupSession)
        : [];
    },
    async deleteSession(key, deleteParams) {
      await callGatewayRpc({
        ...params,
        method: 'sessions.delete',
        rpcParams: {
          key,
          deleteTranscript: deleteParams.deleteTranscript,
        },
      });
    },
  };
}

export function startBcsSessionCleanup(params: {
  loadConfig: () => Promise<Record<string, unknown>>;
  dataDir?: string;
  log?: BcsSessionCleanupLog;
  createGateway?: (cfg: Record<string, unknown>) => BcsSessionCleanupGateway;
  now?: () => number;
}): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function schedule(delayMs: number) {
    if (stopped) return;
    timer = setTimeout(() => {
      void runAndSchedule();
    }, delayMs);
  }

  async function runAndSchedule() {
    let nextDelayMs = DEFAULT_INTERVAL_MS;
    try {
      const cfg = await params.loadConfig();
      const cleanup = resolveBcsSessionCleanupConfig(cfg);
      nextDelayMs = cleanup.intervalMs || DEFAULT_INTERVAL_MS;
      if (!cleanup.enabled) {
        if (cleanup.disabledReason) {
          params.log?.warn?.(`[BCS sessionCleanup] disabled: ${cleanup.disabledReason}`);
        }
        return;
      }
      await runBcsSessionCleanupOnce({
        cleanup,
        gateway: params.createGateway?.(cfg) ?? createBcsSessionCleanupGateway({
          cfg,
          dataDir: params.dataDir,
          log: params.log,
        }),
        now: params.now?.(),
        log: params.log,
      });
    } catch (err) {
      params.log?.warn?.(
        `[BCS sessionCleanup] run failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      schedule(nextDelayMs);
    }
  }

  void runAndSchedule();

  return () => {
    stopped = true;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };
}
