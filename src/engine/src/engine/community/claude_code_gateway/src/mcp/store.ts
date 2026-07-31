// MCP server config persistence.
//
// Storage backend: a single JSON file, by default `~/.mcporter/mcporter.json`
// so OCB's OpenClaw-path (`src/engine/src/engine/web/mcp.py`, which reads/writes
// the same file) and this relay share one server inventory. Env var
// `MCP_CONFIG_PATH` overrides the location (tests, non-mcporter deployments).
//
// File format mirrors mcporter's convention:
//
//   {
//     "mcpServers": {
//       "<serverCode>": {
//         "type": "sse" | "http" | "stdio",      // alias: "transport"
//         "url": "...",                          // alias: "baseUrl"
//         "command": "...",                      // stdio only
//         "args": [ ... ],
//         "env": { ... },
//         "headers": { ... },
//         "timeout_seconds": 30,                 // alias: "timeoutSeconds"
//         "enabled": true,
//         "description": "..."
//       },
//       ...
//     }
//   }
//
// On read we normalise both key-variants (`type`/`transport`, `url`/`baseUrl`,
// `timeout_seconds`/`timeoutSeconds`). On write we preserve the existing
// variant for unchanged keys so we don't gratuitously rewrite files edited
// by mcporter CLI or the OCB OpenClaw path.

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createLogger } from '../debug.js';
import type { McpServerConfig, McpTransport } from './types.js';

const log = createLogger('mcp');
const DEFAULT_WRITE_DEBOUNCE_MS = 50;

export function defaultConfigPath(): string {
  if (process.env.MCP_CONFIG_PATH) return process.env.MCP_CONFIG_PATH;
  return path.join(os.homedir(), '.mcporter', 'mcporter.json');
}

export type McpStoreOptions = {
  writeDebounceMs?: number;
};

function normaliseTransport(raw: unknown): McpTransport {
  const v = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (v === 'stdio') return 'stdio';
  if (v === 'http' || v === 'streamable_http') return 'http';
  return 'sse';
}

function asStringList(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map(x => String(x));
}

function asStringMap(v: unknown): Record<string, string> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  const out: Record<string, string> = {};
  for (const [ k, val ] of Object.entries(v as Record<string, unknown>)) {
    out[String(k)] = String(val);
  }
  return out;
}

function normaliseTimeout(raw: unknown, fallback = 30): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.trunc(n);
}

/** Normalise a raw mcporter entry (with either key-variant) into our canonical type. */
export function fromRaw(serverCode: string, raw: unknown): McpServerConfig {
  const obj = (raw && typeof raw === 'object' && !Array.isArray(raw))
    ? raw as Record<string, unknown>
    : {};
  return {
    serverCode,
    type: normaliseTransport(obj.type ?? obj.transport),
    url: (typeof obj.url === 'string' ? obj.url : typeof obj.baseUrl === 'string' ? obj.baseUrl : undefined),
    command: typeof obj.command === 'string' ? obj.command : undefined,
    args: asStringList(obj.args),
    env: asStringMap(obj.env),
    headers: asStringMap(obj.headers),
    timeout_seconds: normaliseTimeout(obj.timeout_seconds ?? obj.timeoutSeconds),
    enabled: obj.enabled === undefined ? true : Boolean(obj.enabled),
    description: typeof obj.description === 'string' ? obj.description : undefined,
  };
}

/** Serialise canonical config back to mcporter's on-disk shape, preserving existing key variants. */
export function toRaw(config: McpServerConfig, existing?: Record<string, unknown>): Record<string, unknown> {
  const prev = existing && typeof existing === 'object' ? existing : {};
  const urlKey = 'baseUrl' in prev && !('url' in prev) ? 'baseUrl' : 'url';
  const transportKey = 'transport' in prev && !('type' in prev) ? 'transport' : 'type';
  const timeoutKey = 'timeoutSeconds' in prev && !('timeout_seconds' in prev) ? 'timeoutSeconds' : 'timeout_seconds';

  const out: Record<string, unknown> = { ...prev };
  out[transportKey] = config.type;
  if (config.url !== undefined) out[urlKey] = config.url;
  if (config.command !== undefined) out.command = config.command;
  out.args = config.args;
  out.env = config.env;
  out.headers = config.headers;
  out[timeoutKey] = config.timeout_seconds;
  out.enabled = config.enabled;
  if (config.description !== undefined) out.description = config.description;
  // Scrub the "other" keys so the disk representation doesn't drift.
  if (transportKey === 'type') delete out.transport;
  if (transportKey === 'transport') delete out.type;
  if (urlKey === 'url') delete out.baseUrl;
  if (urlKey === 'baseUrl') delete out.url;
  if (timeoutKey === 'timeout_seconds') delete out.timeoutSeconds;
  if (timeoutKey === 'timeoutSeconds') delete out.timeout_seconds;
  return out;
}

/**
 * In-memory cache of the mcporter.json with debounced atomic writes. Mirrors
 * the write pattern of `SessionStore` / `CronStore`.
 *
 * Keeps the top-level wrapper (`mcpServers` vs legacy `servers`) and each
 * entry's key-variants as they were on disk, so we only rewrite the keys we
 * changed.
 */
export class McpStore {
  private readonly filePath: string;
  private readonly writeDebounceMs: number;

  /** Raw file root object — we mutate servers in-place and dump this back. */
  private root: Record<string, unknown> = { mcpServers: {} };
  /** Which top-level key holds the server map (mcporter uses `mcpServers`, legacy uses `servers`). */
  private serversKey: 'mcpServers' | 'servers' = 'mcpServers';

  private dirty = false;
  private pendingTimer: NodeJS.Timeout | null = null;
  private writeInFlight: Promise<void> | null = null;

  constructor(filePath: string = defaultConfigPath(), opts: McpStoreOptions = {}) {
    this.filePath = filePath;
    this.writeDebounceMs = opts.writeDebounceMs ?? DEFAULT_WRITE_DEBOUNCE_MS;
    this.load();
  }

  private load(): void {
    if (!fs.existsSync(this.filePath)) {
      this.root = { mcpServers: {} };
      this.serversKey = 'mcpServers';
      return;
    }
    try {
      const raw = fs.readFileSync(this.filePath, 'utf8');
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('mcporter.json top-level must be an object');
      }
      this.root = parsed as Record<string, unknown>;
      if (this.root.mcpServers && typeof this.root.mcpServers === 'object') {
        this.serversKey = 'mcpServers';
      } else if (this.root.servers && typeof this.root.servers === 'object') {
        this.serversKey = 'servers';
      } else {
        this.root.mcpServers = {};
        this.serversKey = 'mcpServers';
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.warn('load: failed, starting empty', { path: this.filePath, error: msg });
      this.root = { mcpServers: {} };
      this.serversKey = 'mcpServers';
    }
  }

  private servers(): Record<string, unknown> {
    const m = this.root[this.serversKey];
    if (!m || typeof m !== 'object' || Array.isArray(m)) {
      this.root[this.serversKey] = {};
    }
    return this.root[this.serversKey] as Record<string, unknown>;
  }

  private scheduleSave(): void {
    this.dirty = true;
    if (this.pendingTimer) return;
    this.pendingTimer = setTimeout(() => {
      this.pendingTimer = null;
      void this.writeNow();
    }, this.writeDebounceMs);
  }

  private writeNow(): Promise<void> {
    if (this.writeInFlight) return this.writeInFlight;
    if (!this.dirty) return Promise.resolve();
    this.dirty = false;
    const snapshot = JSON.stringify(this.root, null, 2) + '\n';
    this.writeInFlight = this.writeAtomic(snapshot).finally(() => {
      this.writeInFlight = null;
      if (this.dirty) void this.writeNow();
    });
    return this.writeInFlight;
  }

  private async writeAtomic(contents: string): Promise<void> {
    const dir = path.dirname(this.filePath);
    await fsp.mkdir(dir, { recursive: true });
    const tmp = `${this.filePath}.${process.pid}.${Date.now()}.tmp`;
    await fsp.writeFile(tmp, contents, 'utf8');
    await fsp.rename(tmp, this.filePath);
  }

  /** Absolute path to the mcporter.json this store manages (for --config passthrough). */
  get configPath(): string {
    return this.filePath;
  }

  /** Await any pending writes. Use on shutdown or from tests. */
  async flush(): Promise<void> {
    if (this.pendingTimer) {
      clearTimeout(this.pendingTimer);
      this.pendingTimer = null;
    }
    if (this.dirty) await this.writeNow();
    if (this.writeInFlight) await this.writeInFlight;
  }

  // ---- CRUD API ----

  list(): McpServerConfig[] {
    const servers = this.servers();
    const codes = Object.keys(servers).sort();
    return codes.map(code => fromRaw(code, servers[code]));
  }

  get(serverCode: string): McpServerConfig | null {
    const raw = this.servers()[serverCode];
    if (raw === undefined) return null;
    return fromRaw(serverCode, raw);
  }

  create(config: McpServerConfig): McpServerConfig {
    const servers = this.servers();
    if (config.serverCode in servers) {
      throw new Error(`ALREADY_EXISTS:${config.serverCode}`);
    }
    servers[config.serverCode] = toRaw(config);
    this.scheduleSave();
    return fromRaw(config.serverCode, servers[config.serverCode]);
  }

  update(serverCode: string, patch: Partial<McpServerConfig>): McpServerConfig {
    const servers = this.servers();
    const raw = servers[serverCode];
    if (raw === undefined) throw new Error(`NOT_FOUND:${serverCode}`);
    const existing = fromRaw(serverCode, raw);
    // Determine the effective transport after the patch.
    const effectiveTransport = patch.type ?? existing.type;
    // Ensure HTTP/SSE servers carry the per-user CallerToken placeholder so
    // mcporter resolves $env:MCPORTER_USER_TOKEN at call time. Distinct from
    // Authorization on purpose: it coexists with a deployment's own
    // headerPolicy-injected Authorization instead of overwriting it.
    let effectiveHeaders = patch.headers ?? existing.headers;
    if (effectiveTransport !== 'stdio') {
      effectiveHeaders = { ...effectiveHeaders };
      if (!('CallerToken' in effectiveHeaders)) {
        effectiveHeaders.CallerToken = '$env:MCPORTER_USER_TOKEN';
      }
    }
    const merged: McpServerConfig = {
      ...existing,
      ...patch,
      // Force the serverCode to match the path, not the patch body.
      serverCode,
      // Ensure container fields stay objects/arrays even if patch passed null.
      args: patch.args ?? existing.args,
      env: patch.env ?? existing.env,
      headers: effectiveHeaders,
    };
    servers[serverCode] = toRaw(merged, raw as Record<string, unknown>);
    this.scheduleSave();
    return fromRaw(serverCode, servers[serverCode]);
  }

  delete(serverCode: string): boolean {
    const servers = this.servers();
    if (!(serverCode in servers)) return false;
    delete servers[serverCode];
    this.scheduleSave();
    return true;
  }
}

