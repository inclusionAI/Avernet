// RPC handlers for mcp.* methods.
//
// Contract follows `src/cron/handlers.ts`: each handler is a pure function of
// (store, params) returning `McpResult<T>`. `server.ts#handleFrame` flattens
// the result into a response frame.
//
// Methods implemented in this phase:
//   - mcp.config.list     — snapshot of all servers
//   - mcp.config.get      — { server } or NOT_FOUND
//   - mcp.config.create   — ALREADY_EXISTS on duplicate serverCode
//   - mcp.config.update   — NOT_FOUND when code missing
//   - mcp.config.delete   — { deleted: boolean }
//   - mcp.tools.list      — returns { tools: [] } (phase-1 stub; see note below)
//   - mcp.tools.call      — returns NOT_IMPLEMENTED result (phase-1 stub)
//
// Phase-1 note on tools.*:
// AiCodingMCPPlugin's docstring already says "list_tools: CLI returns empty".
// The plugin handles an empty `tools` list and NOT_IMPLEMENTED from call_tool
// gracefully (folds to MCPToolCallResult{is_error=true}). Wiring real MCP
// transport requires `@modelcontextprotocol/sdk`; adding that dep + managing
// client lifetimes is a follow-up, tracked separately. For now the config
// plane is functional and aicoding users get parity with OpenClaw's REST path.

import { createLogger } from '../debug.js';
import { execFile as _nodeExecFile } from 'node:child_process';
import type { McpStore } from './store.js';
import type { McpResult, McpServerConfig, McpTransport, FilterServersResult } from './types.js';

// Testable injection point — defaults to node:child_process.execFile.
// Tests can replace the execFileRunner module's export via module-level mutation.
export interface ExecFileRunner {
  execFile: typeof _nodeExecFile;
}
const runner: ExecFileRunner = { execFile: _nodeExecFile };
export function _setExecFileRunner(fn: typeof _nodeExecFile): void {
  runner.execFile = fn;
}
export function _resetExecFileRunner(): void {
  runner.execFile = _nodeExecFile;
}

const log = createLogger('mcp');

// Dedicated header carrying the per-user caller token, injected into every
// HTTP/SSE server's headers so that mcporter resolves $env:MCPORTER_USER_TOKEN
// at call time. We deliberately use a DISTINCT header name (not Authorization)
// so we do NOT collide with a deployment's own Authorization header policy —
// both headers coexist.
// $env: interpolation in per-server headers is verified on the deployed
// mcporter 0.7.4-openclaw.4 (live test) and 0.9.0. The env var is set by
// handleToolsCall and the CLI/SDK subprocess spawn (chat.ts → startChatRun → bridge env).
const MCP_AUTH_HEADER = 'CallerToken';
const MCP_AUTH_PLACEHOLDER = '$env:MCPORTER_USER_TOKEN';

// mcporter's `call` subcommand emits JSON via `--output json`. It does NOT accept
// `--json` (that flag exists only on `list`) — passing it makes every tools.call
// fail with "Unknown flag '--json'". Verified on mcporter 0.7.4-openclaw.4 (prod)
// and 0.9.0 (local). Exported so the real-binary integration test pins this flag
// contract against the installed mcporter (mock-based tests can't catch it).
export const MCPORTER_CALL_OUTPUT_ARGS: readonly string[] = [ '--output', 'json' ];

/**
 * Ensure HTTP/SSE servers carry the per-user auth placeholder in their headers.
 * stdio servers are left untouched — env-var injection is meaningless for them.
 */
function ensureAuthHeader(
  headers: Record<string, string> | undefined,
  transport: McpTransport,
): Record<string, string> {
  if (transport === 'stdio') return headers ?? {};
  const out: Record<string, string> = { ...(headers ?? {}) };
  // Only inject if not already present (don't overwrite explicit config).
  if (!(MCP_AUTH_HEADER in out)) {
    out[MCP_AUTH_HEADER] = MCP_AUTH_PLACEHOLDER;
  }
  return out;
}

// ---- Small validators ----

function asObject(value: unknown): Record<string, unknown> {
  return (value && typeof value === 'object' && !Array.isArray(value))
    ? value as Record<string, unknown>
    : {};
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

function asStringList(v: unknown): string[] | undefined {
  if (!Array.isArray(v)) return undefined;
  return v.map(x => String(x));
}

function asStringMap(v: unknown): Record<string, string> | undefined {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return undefined;
  const out: Record<string, string> = {};
  for (const [ k, val ] of Object.entries(v as Record<string, unknown>)) {
    out[String(k)] = String(val);
  }
  return out;
}

function asTransport(v: unknown): McpTransport {
  const s = typeof v === 'string' ? v.trim().toLowerCase() : '';
  if (s === 'stdio') return 'stdio';
  if (s === 'http' || s === 'streamable_http') return 'http';
  return 'sse';
}

function asTimeout(v: unknown, fallback = 30): number {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.trunc(n);
}

function err(code: string, message: string): McpResult<never> {
  return { ok: false, error: { code, message } };
}
function ok<T>(payload: T): McpResult<T> { return { ok: true, payload }; }

// ---- Handlers ----

export async function handleConfigList(
  store: McpStore,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _params: unknown,
): Promise<McpResult<{ servers: McpServerConfig[] }>> {
  return ok({ servers: store.list() });
}

export async function handleConfigGet(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{ server: McpServerConfig | null }>> {
  const code = asString(asObject(params).serverCode);
  if (!code) return err('INVALID_PARAMS', 'serverCode is required');
  const server = store.get(code);
  if (server === null) return err('NOT_FOUND', `MCP server not found: ${code}`);
  return ok({ server });
}

export async function handleConfigCreate(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{ server: McpServerConfig }>> {
  const p = asObject(params);
  const code = asString(p.serverCode);
  if (!code) return err('INVALID_PARAMS', 'serverCode is required');

  const transport = asTransport(p.type ?? p.transport);
  const url = asString(p.url);
  const command = asString(p.command);
  // Minimal sanity: stdio needs `command`; http/sse need `url`.
  if (transport === 'stdio' && !command) {
    return err('INVALID_PARAMS', 'stdio transport requires command');
  }
  if ((transport === 'http' || transport === 'sse') && !url) {
    return err('INVALID_PARAMS', `${transport} transport requires url`);
  }

  const config: McpServerConfig = {
    serverCode: code,
    type: transport,
    url,
    command,
    args: asStringList(p.args) ?? [],
    env: asStringMap(p.env) ?? {},
    headers: ensureAuthHeader(asStringMap(p.headers), transport),
    timeout_seconds: asTimeout(p.timeout_seconds ?? p.timeoutSeconds),
    enabled: p.enabled === undefined ? true : Boolean(p.enabled),
    description: asString(p.description),
  };

  try {
    const created = store.create(config);
    log.debug('config.create', { serverCode: code });
    return ok({ server: created });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('ALREADY_EXISTS:')) {
      return err('ALREADY_EXISTS', `MCP server already exists: ${code}`);
    }
    log.error('config.create:failed', { serverCode: code, error: msg });
    return err('INTERNAL_ERROR', msg);
  }
}

export async function handleConfigUpdate(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{ server: McpServerConfig }>> {
  const p = asObject(params);
  const code = asString(p.serverCode);
  if (!code) return err('INVALID_PARAMS', 'serverCode is required');

  const patch: Partial<McpServerConfig> = {};
  if (p.type !== undefined || p.transport !== undefined) {
    patch.type = asTransport(p.type ?? p.transport);
  }
  if (p.url !== undefined) patch.url = asString(p.url);
  if (p.command !== undefined) patch.command = asString(p.command);
  const argsN = asStringList(p.args);
  if (argsN !== undefined) patch.args = argsN;
  const envN = asStringMap(p.env);
  if (envN !== undefined) patch.env = envN;
  const headersN = asStringMap(p.headers);
  if (headersN !== undefined) patch.headers = headersN;
  if (p.timeout_seconds !== undefined || p.timeoutSeconds !== undefined) {
    patch.timeout_seconds = asTimeout(p.timeout_seconds ?? p.timeoutSeconds);
  }
  if (p.enabled !== undefined) patch.enabled = Boolean(p.enabled);
  if (p.description !== undefined) patch.description = asString(p.description);

  try {
    const updated = store.update(code, patch);
    log.debug('config.update', { serverCode: code });
    return ok({ server: updated });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('NOT_FOUND:')) {
      return err('NOT_FOUND', `MCP server not found: ${code}`);
    }
    log.error('config.update:failed', { serverCode: code, error: msg });
    return err('INTERNAL_ERROR', msg);
  }
}

export async function handleConfigDelete(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{ deleted: boolean }>> {
  const code = asString(asObject(params).serverCode);
  if (!code) return err('INVALID_PARAMS', 'serverCode is required');
  const deleted = store.delete(code);
  log.debug('config.delete', { serverCode: code, deleted });
  return ok({ deleted });
}

// ---- Tools plane (phase-1 stub) ----

export async function handleToolsList(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{ tools: Array<{ name: string; description?: string }> }>> {
  const p = asObject(params);
  const serverCode = asString(p.serverCode) ?? '';
  // When no serverCode, return empty (no server to query)
  if (!serverCode) {
    return ok({ tools: [] });
  }

  // Per-call user token (engine → relay → mcporter env), same as handleToolsCall,
  // so tool discovery (list) carries the caller's CallerToken identity. Always
  // set the env so mcporter's $env: resolution never throws; empty value →
  // empty CallerToken header (zero-regression when no token is present).
  const userToken = asString(p.userToken);
  log.debug('tools.list:invoking', { serverCode, hasUserToken: !!userToken });

  try {
    const { stdout } = await new Promise<{ stdout: string; stderr: string }>(
      (resolve, reject) => {
        runner.execFile('mcporter', [ 'list', serverCode, '--schema', '--json', '--config', store.configPath ], {
          timeout: 30000,
          maxBuffer: 10 * 1024 * 1024,
          env: { ...process.env, MCPORTER_USER_TOKEN: userToken ? `Bearer ${userToken}` : '' },
        }, (error, stdout, stderr) => {
          if (error) reject(error);
          else resolve({ stdout, stderr });
        });
      },
    );

    const raw = (stdout || '').trim();
    if (!raw) {
      log.warn('tools.list:empty_stdout', { serverCode });
      return ok({ tools: [] });
    }

    const parsed = JSON.parse(raw);
    // mcporter list --schema --json returns { tools: [...] }
    const tools = (parsed.tools ?? []).map((t: Record<string, unknown>) => ({
      name: String(t.name ?? ''),
      description: t.description ? String(t.description) : undefined,
    }));
    log.debug('tools.list:done', { serverCode, count: tools.length });
    return ok({ tools });
  } catch (e) {
    log.warn('tools.list:failed', {
      serverCode,
      error: e instanceof Error ? e.message : String(e),
    });
    return ok({ tools: [] });
  }
}

export async function handleToolsCall(
  store: McpStore,
  params: unknown,
): Promise<McpResult<{
  serverCode: string;
  content: Array<{ type: string; text: string }>;
  isError: boolean;
}>> {
  const p = asObject(params);
  const serverCode = asString(p.serverCode) ?? '';
  const toolName = asString(p.toolName) ?? '';
  if (!toolName) {
    return ok({
      serverCode,
      content: [{ type: 'text', text: 'toolName is required' }],
      isError: true,
    });
  }

  const args = asObject(p.arguments ?? p.args ?? {});
  // Pass arguments as a JSON object via --args. mcporter's key=value positional
  // form does not work for tools whose schema expects named string params
  // (e.g. skylark_resolve_url errors with "Too many positional arguments").
  const cmdArgs = [ '--args', JSON.stringify(args) ];

  const selector = toolName;

  // Per-call user token for MCP auth (engine → relay → mcporter env).
  // mcporter resolves $env:MCPORTER_USER_TOKEN in the server's headers field
  // (runtime-header-utils.js → resolveEnvPlaceholders). The env var is always
  // set so that $env: resolution never throws; an empty value produces an empty
  // CallerToken header, which is zero-regression when no token is present.
  const userToken = asString(p.userToken);
  const mcporterEnv: Record<string, string> = {
    MCPORTER_USER_TOKEN: userToken ? `Bearer ${userToken}` : '',
  };

  log.debug('tools.call:invoking', {
    toolName,
    args,
    hasUserToken: !!userToken,
  });

  try {
    const { stdout, stderr } = await new Promise<{ stdout: string; stderr: string }>(
      (resolve, reject) => {
        // mcporter `call` uses `--output json`, not `--json` — see
        // MCPORTER_CALL_OUTPUT_ARGS. Pinned by test/mcp-real-binary.test.ts.
        runner.execFile('mcporter', [ 'call', selector, ...cmdArgs, ...MCPORTER_CALL_OUTPUT_ARGS, '--config', store.configPath ], {
          timeout: 60000,
          maxBuffer: 10 * 1024 * 1024,
          env: { ...process.env, ...mcporterEnv },
        }, (error, stdout, stderr) => {
          if (error) reject(error);
          else resolve({ stdout, stderr });
        });
      },
    );

    const raw = (stdout || '').trim();
    if (!raw) {
      const fallback = (stderr || '').trim() || `mcporter returned empty output for ${toolName}`;
      log.warn('tools.call:empty_stdout', { toolName, stderr: fallback });
      return ok({
        serverCode,
        content: [{ type: 'text', text: fallback }],
        isError: true,
      });
    }

    try {
      const parsed = JSON.parse(raw);
      // mcporter returns { ok: bool, data: ..., error: ... }. Forward the full
      // envelope as content (do NOT unwrap `.data`) so callers see a consistent
      // shape and can read ok/error/data themselves.
      const ok_field = parsed.ok ?? !(parsed.error);

      log.debug('tools.call:done', { toolName, ok: ok_field });

      return ok({
        serverCode,
        content: [{ type: 'text', text: raw }],
        isError: !ok_field,
      });
    } catch {
      // stdout is not JSON — return as plain text
      log.warn('tools.call:non_json_output', { toolName, raw: raw.slice(0, 200) });
      return ok({
        serverCode,
        content: [{ type: 'text', text: raw }],
        isError: false,
      });
    }
  } catch (e) {
    const message = (e as Error & { killed?: boolean }).killed
      ? `mcporter call timed out (60s) for ${toolName}`
      : `mcporter call failed: ${e instanceof Error ? e.message : String(e)}`;
    log.error('tools.call:mcporter_failed', { toolName, error: message });
    return ok({
      serverCode,
      content: [{ type: 'text', text: `mcporter call failed: ${message}` }],
      isError: true,
    });
  }
}

/**
 * mcp.filter_servers RPC method
 *
 * Called by OCB's AiCodingMCPPlugin.sync_all_mcp_servers() to filter the local
 * mcporter.json so that only the specified server_codes are enabled. This matches
 * the behavior of OpenClaw's `mcporter filter-servers <csv>` CLI command.
 *
 * The method:
 * 1. Takes a list of server_codes that should be enabled
 * 2. Iterates over all servers in mcporter.json
 * 3. Enables servers in the allowed list, disables those not in the list
 *
 * @param store - The McpStore instance
 * @param params - Should contain { serverCodes: string[] }
 * @return FilterServersResult with command/returnCode/stdout/stderr for compatibility
 */
export async function handleFilterServers(
  store: McpStore,
  params: unknown,
): Promise<McpResult<FilterServersResult>> {
  const p = asObject(params);
  const serverCodes = asStringList(p.serverCodes) ?? [];
  const allowedSet = new Set(serverCodes);

  log.debug('mcp.filter_servers', { serverCodes, allowedCount: allowedSet.size });

  // Get all current MCP servers
  const allServers = store.list();
  const changes: Array<{ serverCode: string; action: 'enabled' | 'disabled' }> = [];

  // Enable/disable based on the allowed list
  for (const server of allServers) {
    const shouldBeEnabled = allowedSet.has(server.serverCode);

    if (server.enabled && !shouldBeEnabled) {
      // Currently enabled but not in allowed list -> disable
      store.update(server.serverCode, { enabled: false });
      changes.push({ serverCode: server.serverCode, action: 'disabled' });
      log.debug('mcp.filter_servers:disabled', { serverCode: server.serverCode });
    } else if (!server.enabled && shouldBeEnabled) {
      // Currently disabled but in allowed list -> enable
      store.update(server.serverCode, { enabled: true });
      changes.push({ serverCode: server.serverCode, action: 'enabled' });
      log.debug('mcp.filter_servers:enabled', { serverCode: server.serverCode });
    }
  }

  // Also ensure servers in the allowed list exist (create stub if missing)
  // This matches the behavior where OCB may send server_codes for servers
  // that haven't been created yet via mcp.config.create
  for (const code of serverCodes) {
    const existing = store.get(code);
    if (!existing) {
      // Create a minimal stub entry - OCB should follow up with mcp.config.create/update
      // to provide full configuration. For now just create a placeholder that's enabled.
      // Note: We can't create a valid server without url/command, so just log a warning.
      log.warn('mcp.filter_servers:missing_config', {
        serverCode: code,
        message: `Server ${code} is in allowed list but has no config. Use mcp.config.create first.`,
      });
    }
  }

  const stdout = JSON.stringify({
    filtered: serverCodes.length,
    changes: changes.length,
    details: changes,
  });

  log.debug('mcp.filter_servers:complete', {
    totalServers: allServers.length,
    filteredCount: serverCodes.length,
    changesCount: changes.length,
  });

  // Return result matching OpenClaw's mcporter filter-servers output format
  return ok({
    serverCodes,
    command: [ 'mcp.filter_servers', ...serverCodes ],
    returnCode: 0,
    stdout,
    stderr: '',
  });
}

// ---- Dispatch table consumed by server.ts ----

export type McpHandler = (store: McpStore, params: unknown) => Promise<McpResult<unknown>>;

export const MCP_METHODS: Record<string, McpHandler> = {
  'mcp.config.list': handleConfigList,
  'mcp.config.get': handleConfigGet,
  'mcp.config.create': handleConfigCreate,
  'mcp.config.update': handleConfigUpdate,
  'mcp.config.delete': handleConfigDelete,
  'mcp.tools.list': handleToolsList,
  'mcp.tools.call': handleToolsCall,
  'mcp.filter_servers': handleFilterServers,
};
