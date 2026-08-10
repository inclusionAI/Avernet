// session.new / sessions.{list,patch,delete,reset} handlers.

import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, statSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { createLogger } from '../../debug.js';
import { loadRelayModelProviderEnv } from '../../model-provider-settings.js';
import type { ConnectionContext } from '../connection-context.js';
import type { SessionStore } from '../../store.js';
import type { SessionRuntimeRegistry } from '../../runtime/session-runtime-registry.js';
import type { GatewayRequestFrame, SessionBinding } from '../../types.js';

const log = createLogger('server');

function nowIso() {
  return new Date().toISOString();
}

/**
 * Final hard-coded fallback model id, used only when neither the
 * `RELAY_DEFAULT_MODEL` env var nor the Claude `settings.json` provide one.
 */
const FALLBACK_SESSION_MODEL = 'claude-sonnet-4-5';

/**
 * Resolve the Claude config directory that the SDK / CLI subprocess actually
 * reads `settings.json` from. This mirrors the bridge logic in
 * `claude-sdk-bridge.ts` (`env.CLAUDE_CONFIG_DIR` / `env.HOME` wiring) and
 * `claude-cli-bridge.ts`, so relay's resolved default model stays in sync with
 * the file the spawned Claude process will load:
 *   1. `RELAY_CLAUDE_CONFIG_DIR` / `CLAUDE_CONFIG_DIR` (explicit config dir);
 *   2. otherwise `<RELAY_CLAUDE_HOME | os.homedir()>/.claude`.
 */
function resolveClaudeConfigDir(): string {
  const configDirOverride =
    process.env.RELAY_CLAUDE_CONFIG_DIR?.trim() || process.env.CLAUDE_CONFIG_DIR?.trim();
  if (configDirOverride) return configDirOverride;
  const home = process.env.RELAY_CLAUDE_HOME?.trim() || homedir();
  return path.join(home, '.claude');
}

/**
 * Resolve the default Claude model id for a freshly-seeded session binding when
 * neither `session.new` nor `chat.send` specifies one. Priority chain:
 *   1. `RELAY_DEFAULT_MODEL` env var (explicit relay override);
 *   2. `settings.json`'s `env.ANTHROPIC_MODEL` (keeps relay in sync with the
 *      Claude config the SDK / CLI subprocess reads — see `resolveClaudeConfigDir`);
 *   3. `RELAY_MODEL_SETTINGS_SOURCE`'s `env.ANTHROPIC_MODEL` when singlebox
 *      supplies a model-provider source for an otherwise empty role profile;
 *   4. the hard-coded fallback `claude-sonnet-4-5`.
 *
 * The model id flows to Claude SDK / CLI via the routing layer
 * (`claude-code-router.ts`), which maps it to provider-specific env vars.
 *
 * `settings.json` reading is fully defensive: missing file, corrupt JSON,
 * missing field, or a non-string value all fall back to `claude-sonnet-4-5`. It never
 * throws, so it can never block relay startup or session creation.
 */
export function resolveDefaultSessionModel(): string {
  const fromEnv = process.env.RELAY_DEFAULT_MODEL?.trim();
  if (fromEnv) {
    // 排查日志：默认模型来自 RELAY_DEFAULT_MODEL env 覆盖
    log.debug('default session model resolved', { model: fromEnv, source: 'env' });
    return fromEnv;
  }

  try {
    const settingsPath = path.join(resolveClaudeConfigDir(), 'settings.json');
    const parsed = JSON.parse(readFileSync(settingsPath, 'utf8'));
    const fromSettings = parsed?.env?.ANTHROPIC_MODEL;
    if (typeof fromSettings === 'string' && fromSettings.trim()) {
      const model = fromSettings.trim();
      // 排查日志：默认模型来自 settings.json 的 env.ANTHROPIC_MODEL
      log.debug('default session model resolved', { model, source: 'settings.json', settingsPath });
      return model;
    }
  } catch (err) {
    // 排查日志：settings.json 读取/解析失败（文件不存在/损坏/字段缺失/非字符串），回落到 fallback
    const message = err instanceof Error ? err.message : String(err);
    log.debug('default session model: settings.json read failed, falling back', { error: message });
  }

  const fromModelProviderSource = loadRelayModelProviderEnv().ANTHROPIC_MODEL;
  if (fromModelProviderSource?.trim()) {
    const model = fromModelProviderSource.trim();
    log.debug('default session model resolved', { model, source: 'model-provider-settings-source' });
    return model;
  }

  // 排查日志：默认模型回落到硬编码兜底
  log.debug('default session model resolved', { model: FALLBACK_SESSION_MODEL, source: 'fallback' });
  return FALLBACK_SESSION_MODEL;
}

/**
 * Default working directory when neither session.new nor chat.send provides one.
 * Override with the `RELAY_DEFAULT_CWD` env var.
 */
export const DEFAULT_CWD: string = process.env.RELAY_DEFAULT_CWD?.trim() || homedir();

/**
 * Materialize the binding for a session. On existing bindings this is a pure
 * read — `cwd` is **only** consulted when seeding a fresh binding. Updating
 * `binding.cwd` on an existing session is each caller's responsibility, so the
 * "已有上游会话后不允许静默切换 cwd" gate in `chat.send` can actually run.
 *
 * When `cwd` is not provided, falls back to `DEFAULT_CWD` so that sessions
 * always have a working directory.
 */
export function ensureBinding(
  store: SessionStore,
  sessionKey: string,
  cwd?: string,
  reset = false,
): SessionBinding {
  if (reset) store.deleteByGatewaySessionKey(sessionKey);
  let binding = store.getByGatewaySessionKey(sessionKey);
  if (!binding) {
    // 子串匹配：inject 可能用短 key 创建了 binding，chat.send 用完整 key 来查。
    // 反过来也可能：inject 用短 key 查，chat.send 已用完整 key 创建。
    const fuzzy = store.findBySessionKey(sessionKey);
    if (fuzzy) {
      // 短 key binding 已存在且有 history → 迁移到完整 key binding
      const pendingHistory = fuzzy.history ?? [];
      store.deleteByGatewaySessionKey(fuzzy.gatewaySessionKey);
      binding = {
        ...fuzzy,
        gatewaySessionKey: sessionKey,
        cwd: cwd || fuzzy.cwd || DEFAULT_CWD,
        updatedAt: nowIso(),
        history: pendingHistory,
      };
      store.set(binding);
    } else {
      binding = {
        gatewaySessionKey: sessionKey,
        acpSessionId: `cli:${randomUUID()}`,
        cwd: cwd || DEFAULT_CWD,
        model: resolveDefaultSessionModel(),
        createdAt: nowIso(),
        updatedAt: nowIso(),
        title: sessionKey,
        history: [],
      };
      store.set(binding);
    }
  }
  return binding;
}

function normalizeDirList(input: unknown): string[] | undefined {
  if (!Array.isArray(input)) return undefined;
  const cleaned = input
    .filter((v): v is string => typeof v === 'string')
    .map(v => v.trim())
    .filter(v => v.length > 0);
  return cleaned;
}

/**
 * Validate a directory path is absolute and points to an existing directory.
 * Returns null on success, or a human-readable error reason on failure.
 *
 * Used by session.new / sessions.patch / chat.send to reject malformed `cwd` /
 * `additionalDirectories` early instead of letting Claude SDK fail with an
 * opaque error downstream.
 */
export function validateDirPath(p: string): string | null {
  if (!path.isAbsolute(p)) return `path must be absolute: ${p}`;
  if (!existsSync(p)) return `path does not exist: ${p}`;
  try {
    if (!statSync(p).isDirectory()) return `path is not a directory: ${p}`;
  } catch (err) {
    return `path not accessible: ${p} (${(err as Error).message})`;
  }
  return null;
}

/**
 * Validate a list of directory paths. Returns null if all entries pass, or a
 * single error string aggregating the first failure.
 */
export function validateDirList(list: string[] | undefined): string | null {
  if (!list || list.length === 0) return null;
  for (const p of list) {
    const err = validateDirPath(p);
    if (err) return `additionalDirectories: ${err}`;
  }
  return null;
}

export function mapSessionSummary(binding: SessionBinding) {
  const history = binding.history ?? [];
  const preview = [ ...history ].reverse().find(m => m.role === 'assistant' || m.role === 'user')?.text?.slice(0, 160) ?? '';
  return {
    key: binding.gatewaySessionKey,
    label: binding.title || binding.gatewaySessionKey,
    displayName: binding.title || binding.gatewaySessionKey,
    derivedTitle: binding.title || binding.gatewaySessionKey,
    createdAt: binding.createdAt,
    updatedAt: binding.updatedAt,
    model: binding.model,
    permissionMode: binding.permissionMode,
    cwd: binding.cwd,
    additionalDirectories: binding.additionalDirectories,
    preview,
    messageCount: history.length,
    inputTokens: 0,
    outputTokens: 0,
  };
}

export type SessionsHandlerDeps = {
  store: SessionStore;
  runtimeRegistry: SessionRuntimeRegistry;
  interactionRegistry: import('../../interaction/registry.js').PendingInteractionRegistry;
};

export type SessionsHandler = (
  ctx: ConnectionContext,
  frame: GatewayRequestFrame,
  deps: SessionsHandlerDeps,
) => Promise<void>;

export const handleSessionNew: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const sessionKey = String(params.sessionKey ?? `session:${randomUUID()}:user:${String(params.userId ?? 'default')}`).trim();
  const cwd = typeof params.cwd === 'string' && params.cwd.trim() ? params.cwd.trim() : undefined;
  const model = typeof params.model === 'string' && params.model.trim() ? params.model.trim() : undefined;
  const permissionMode = typeof params.permissionMode === 'string' && params.permissionMode.trim() ? params.permissionMode.trim()
    : typeof params.mode === 'string' && params.mode.trim() ? params.mode.trim() : undefined;
  const additionalDirectories = normalizeDirList(params.additionalDirectories);
  const title = typeof params.label === 'string' ? params.label : typeof params.title === 'string' ? params.title : sessionKey;

  if (cwd) {
    const err = validateDirPath(cwd);
    if (err) {
      ctx.response(frame.id, false, undefined, { message: err, code: 'INVALID_REQUEST' });
      return;
    }
  }
  const dirsErr = validateDirList(additionalDirectories);
  if (dirsErr) {
    ctx.response(frame.id, false, undefined, { message: dirsErr, code: 'INVALID_REQUEST' });
    return;
  }
  log.debug('session.new: begin', {
    connId: ctx.connId,
    sessionKey,
    cwd,
    model,
    permissionMode,
    additionalDirectoriesCount: additionalDirectories?.length ?? 0,
    title,
    userId: params.userId,
    providedSessionKey: typeof params.sessionKey === 'string' && params.sessionKey.trim().length > 0,
  });
  deps.store.deleteByGatewaySessionKey(sessionKey);
  const binding = ensureBinding(deps.store, sessionKey, cwd, true);
  binding.title = title;
  if (model) binding.model = model;
  if (permissionMode) binding.permissionMode = permissionMode;
  if (additionalDirectories !== undefined) binding.additionalDirectories = additionalDirectories;
  deps.store.set(binding);
  log.debug('session.new: created', {
    connId: ctx.connId,
    frameId: frame.id,
    sessionKey: binding.gatewaySessionKey,
    requestedSessionKey: typeof params.sessionKey === 'string' ? params.sessionKey : undefined,
    cwd: binding.cwd,
    title: binding.title,
    historyLength: binding.history?.length ?? 0,
    sdkSessionId: binding.sdkSessionId,
  });

  log.debug('session.new: compare-key', {
    connId: ctx.connId,
    frameId: frame.id,
    sessionKey: binding.gatewaySessionKey,
    requestedSessionKey: typeof params.sessionKey === 'string' ? params.sessionKey : undefined,
    providedSessionKey: typeof params.sessionKey === 'string' && params.sessionKey.trim().length > 0,
  });

  const scriptPath = path.resolve(process.cwd(), 'scripts/on-session-new.sh');
  if (existsSync(scriptPath)) {
    try {
      log.debug('session.new: running init script', { scriptPath, sessionKey: binding.gatewaySessionKey });
      execFileSync('/bin/sh', [ scriptPath ], {
        timeout: 30_000,
        // Init script runs in binding.cwd when set, otherwise relay's process
        // cwd — the script is for git/MCP credential setup, not project work,
        // so falling back here is safe.
        cwd: binding.cwd ?? process.cwd(),
        env: {
          ...process.env,
          SESSION_KEY: binding.gatewaySessionKey,
          SESSION_CWD: binding.cwd ?? '',
          SESSION_TITLE: binding.title ?? '',
          SESSION_MODEL: binding.model ?? '',
          SESSION_ADDITIONAL_DIRS: (binding.additionalDirectories ?? []).join(':'),
          SESSION_CREATED_AT: binding.createdAt ?? '',
        },
        stdio: [ 'ignore', 'pipe', 'pipe' ],
      });
    } catch (err: any) {
      log.warn('session.new: init script failed', { scriptPath, error: err.message });
    }
  }

  ctx.response(frame.id, true, mapSessionSummary(binding));
};

export const handleSessionsList: SessionsHandler = async (ctx, frame, deps) => {
  const sessions = deps.store.list().map(mapSessionSummary);
  ctx.response(frame.id, true, { sessions });
};

export const handleSessionsPatch: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const key = String(params.key ?? '').trim();
  if (!key) {
    ctx.response(frame.id, false, undefined, { message: 'key required', code: 'INVALID_REQUEST' });
    return;
  }
  const cwd = typeof params.cwd === 'string' && params.cwd.trim() ? params.cwd.trim() : undefined;
  const model = typeof params.model === 'string' && params.model.trim() ? params.model.trim() : undefined;
  const permissionMode = typeof params.permissionMode === 'string' && params.permissionMode.trim() ? params.permissionMode.trim()
    : typeof params.mode === 'string' && params.mode.trim() ? params.mode.trim() : undefined;
  const additionalDirectories = normalizeDirList(params.additionalDirectories);

  if (cwd) {
    const err = validateDirPath(cwd);
    if (err) {
      ctx.response(frame.id, false, undefined, { message: err, code: 'INVALID_REQUEST' });
      return;
    }
  }
  const dirsErr = validateDirList(additionalDirectories);
  if (dirsErr) {
    ctx.response(frame.id, false, undefined, { message: dirsErr, code: 'INVALID_REQUEST' });
    return;
  }

  const binding = ensureBinding(deps.store, key, cwd, false);
  if (cwd !== undefined) {
    binding.cwd = cwd;
    binding.updatedAt = nowIso();
  }
  if (model !== undefined) binding.model = model;
  if (permissionMode !== undefined) binding.permissionMode = permissionMode;
  if (additionalDirectories !== undefined) binding.additionalDirectories = additionalDirectories;
  if (typeof params.label === 'string' && params.label.trim()) binding.title = params.label.trim();
  deps.store.set(binding);
  ctx.response(frame.id, true, mapSessionSummary(binding));
};

export const handleSessionsDelete: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const key = String(params.key ?? '').trim();
  if (!key) {
    ctx.response(frame.id, false, undefined, { message: 'key required', code: 'INVALID_REQUEST' });
    return;
  }
  deps.store.deleteByGatewaySessionKey(key);
  ctx.response(frame.id, true, { ok: true, deleted: true, key });
};

export const handleSessionsReset: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const key = String(params.key ?? '').trim();
  if (!key) {
    ctx.response(frame.id, false, undefined, { message: 'key required', code: 'INVALID_REQUEST' });
    return;
  }
  const binding = deps.store.getByGatewaySessionKey(key);
  if (!binding) {
    ctx.response(frame.id, true, { ok: true, reset: false, key });
    return;
  }
  binding.history = [];
  binding.updatedAt = nowIso();
  deps.store.set(binding);
  ctx.response(frame.id, true, { ok: true, reset: true, key });
};

export const handleSessionAttach: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const sessionKey = String(params.sessionKey ?? '').trim();
  if (!sessionKey) {
    ctx.response(frame.id, false, undefined, { message: 'sessionKey required', code: 'INVALID_REQUEST' });
    return;
  }

  deps.runtimeRegistry.attachConnection(sessionKey, ctx.connId);
  ctx.attachedSessions.add(sessionKey);
  ctx.controllerSessions.add(sessionKey);

  const pendingCount = deps.interactionRegistry.countForSession(sessionKey);
  const status = deps.runtimeRegistry.getStatus(sessionKey, pendingCount, ctx.connId);

  const binding = deps.store.getByGatewaySessionKey(sessionKey);

  ctx.response(frame.id, true, {
    ...status,
    sdkSessionId: binding?.sdkSessionId,
  });
};

export const handleSessionDetach: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const sessionKey = String(params.sessionKey ?? '').trim();
  if (!sessionKey) {
    ctx.response(frame.id, false, undefined, { message: 'sessionKey required', code: 'INVALID_REQUEST' });
    return;
  }

  deps.runtimeRegistry.detachConnection(sessionKey, ctx.connId);
  ctx.attachedSessions.delete(sessionKey);
  ctx.controllerSessions.delete(sessionKey);

  ctx.response(frame.id, true, { sessionKey, detached: true });
};

export const handleSessionStatus: SessionsHandler = async (ctx, frame, deps) => {
  const params = (frame.params ?? {}) as Record<string, unknown>;
  const sessionKey = String(params.sessionKey ?? '').trim();
  if (!sessionKey) {
    ctx.response(frame.id, false, undefined, { message: 'sessionKey required', code: 'INVALID_REQUEST' });
    return;
  }

  const pendingCount = deps.interactionRegistry.countForSession(sessionKey);
  const status = deps.runtimeRegistry.getStatus(sessionKey, pendingCount, ctx.connId);

  const binding = deps.store.getByGatewaySessionKey(sessionKey);

  ctx.response(frame.id, true, {
    ...status,
    sdkSessionId: binding?.sdkSessionId,
  });
};

export const SESSIONS_METHODS: Record<string, SessionsHandler> = {
  'session.new': handleSessionNew,
  'sessions.list': handleSessionsList,
  'sessions.patch': handleSessionsPatch,
  'sessions.delete': handleSessionsDelete,
  'sessions.reset': handleSessionsReset,
  'session.attach': handleSessionAttach,
  'session.detach': handleSessionDetach,
  'session.status': handleSessionStatus,
};
