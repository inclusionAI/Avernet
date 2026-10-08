export type EmbeddedAgentToolContext = {
  sessionKey?: unknown;
  agentId?: unknown;
  sessionId?: unknown;
  sessionFile?: unknown;
  workspaceDir?: unknown;
  agentDir?: unknown;
  messageChannel?: unknown;
  senderIsOwner?: unknown;
  agent?: unknown;
  session?: unknown;
  [key: string]: unknown;
};

export type EmbeddedAgentRuntimeApi = {
  runtime?: {
    config?: {
      loadConfig?: () => Promise<unknown> | unknown;
    };
    agent?: {
      defaults?: unknown;
      resolveAgentWorkspaceDir?: (config: unknown, agentId: string) => string | undefined;
      resolveAgentDir?: (config: unknown, agentId: string) => string | undefined;
      ensureAgentWorkspace?: (params: { dir: string; ensureBootstrapFiles?: boolean }) => Promise<{ dir?: string } | void> | { dir?: string } | void;
      session?: {
        resolveStorePath?: (unused?: unknown, opts?: { agentId?: string }) => Promise<unknown> | unknown;
        loadSessionStore?: (storePath?: unknown, opts?: { skipCache?: boolean }) => Promise<unknown> | unknown;
        resolveSessionFilePath?: (
          sessionId: string,
          entry?: Record<string, unknown>,
          opts?: { agentId?: string },
        ) => string | undefined;
      };
    };
  };
};

export type EmbeddedAgentContext = {
  sessionId?: string;
  sessionKey?: string;
  agentId: string;
  messageChannel?: string;
  senderIsOwner?: boolean;
  sessionFile?: string;
  workspaceDir?: string;
  agentDir?: string;
  config?: unknown;
  provider?: string;
  model?: string;
  authProfileId?: string;
  authProfileIdSource?: string;
  modelSource?: "session" | "config" | "runtime-defaults";
  skillsSnapshot?: Record<string, unknown>;
};

type ModelSelection = {
  provider?: string;
  model?: string;
  authProfileId?: string;
  authProfileIdSource?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    const stringValue = asString(value);
    if (stringValue) return stringValue;
  }
  return undefined;
}

function firstBoolean(...values: unknown[]): boolean | undefined {
  for (const value of values) {
    const booleanValue = asBoolean(value);
    if (booleanValue !== undefined) return booleanValue;
  }
  return undefined;
}

function splitModelRef(rawModel: string | undefined, explicitProvider?: string): ModelSelection {
  if (!rawModel) return {};

  const model = rawModel.trim();
  const provider = explicitProvider?.trim();
  if (!model) return provider ? { provider } : {};

  const providerPrefix = provider ? `${provider}/` : undefined;
  if (providerPrefix && model.toLowerCase().startsWith(providerPrefix.toLowerCase())) {
    const modelWithoutProvider = model.slice(providerPrefix.length).trim();
    return {
      provider,
      model: modelWithoutProvider || model,
    };
  }

  if (provider) {
    return { provider, model };
  }

  const slash = model.indexOf("/");
  if (slash <= 0 || slash >= model.length - 1) {
    return { model };
  }

  return {
    provider: model.slice(0, slash).trim(),
    model: model.slice(slash + 1).trim(),
  };
}

function getPath(value: unknown, path: string[]): unknown {
  let current = value;
  for (const part of path) {
    if (!isRecord(current)) return undefined;
    current = current[part];
  }
  return current;
}

function deriveAgentId(toolCtx: EmbeddedAgentToolContext | undefined, sessionKey: string | undefined): string {
  const explicitAgentId = firstString(
    toolCtx?.agentId,
    getPath(toolCtx, ["agent", "id"]),
    getPath(toolCtx, ["agent", "agentId"]),
    getPath(toolCtx, ["session", "agentId"]),
  );
  if (explicitAgentId) return explicitAgentId;

  const key = firstString(toolCtx?.sessionKey, sessionKey);
  const parts = key?.split(":").filter(Boolean) ?? [];
  const prefix = parts[0] === "agent" && parts[1] ? parts[1] : parts[0];
  if (prefix && /^[A-Za-z][A-Za-z0-9_-]*$/.test(prefix)) {
    return prefix;
  }

  return "main";
}

// ── Process-level caches for repeated concurrent calls ────────────────────────
//
// When multiple concurrent flows execute the same embedded-agent node, each
// calls loadConfig() and loadSessionStore() independently — but the results
// are identical within a short window.  These caches avoid redundant I/O by
// sharing results across concurrent invocations.
//
// Cache TTLs are conservative:
//   - config: 60s — application config changes rarely
//   - session store: 30s — covers the concurrent execution window

const CONFIG_CACHE_TTL_MS = 60_000;
const SESSION_STORE_CACHE_TTL_MS = 30_000;

let configCache: { config: unknown; timestamp: number } | null = null;

async function loadConfig(api: EmbeddedAgentRuntimeApi): Promise<unknown> {
  const now = Date.now();
  if (configCache && (now - configCache.timestamp) < CONFIG_CACHE_TTL_MS) {
    return configCache.config;
  }
  try {
    const config = await api.runtime?.config?.loadConfig?.();
    if (config !== undefined) {
      // Defensive copy — prevents downstream mutation from corrupting the cache
      configCache = { config: structuredClone(config), timestamp: now };
    }
    return config;
  } catch {
    return undefined;
  }
}

type SessionStoreCacheEntry = {
  store: unknown;
  timestamp: number;
};

const sessionStoreCache = new Map<string, SessionStoreCacheEntry>();

function purgeExpiredSessionStoreCache(): void {
  const now = Date.now();
  for (const [key, entry] of sessionStoreCache.entries()) {
    if (now - entry.timestamp > SESSION_STORE_CACHE_TTL_MS * 2) {
      sessionStoreCache.delete(key);
    }
  }
}

async function loadSessionStore(api: EmbeddedAgentRuntimeApi, agentId: string, userId?: string): Promise<unknown> {
  const sessionApi = api.runtime?.agent?.session;
  if (!sessionApi?.loadSessionStore) return undefined;

  let storePath: unknown;
  const resolveStorePath = sessionApi.resolveStorePath;
  if (resolveStorePath) {
    try {
      storePath = await resolveStorePath(undefined, { agentId });
    } catch {
      storePath = undefined;
    }

    if (!asString(storePath)) {
      try {
        storePath = await (resolveStorePath as (agentId: string) => Promise<unknown> | unknown)(agentId);
      } catch {
        storePath = undefined;
      }
    }
  }

  // Check cache — key includes userId (when available) + agentId + storePath
  // to prevent cross-user session store leakage in multi-user prod deployments.
  const cacheKey = `store:${userId ?? "anon"}:${agentId}:${asString(storePath) ?? "default"}`;
  const now = Date.now();
  const cached = sessionStoreCache.get(cacheKey);
  if (cached && (now - cached.timestamp) < SESSION_STORE_CACHE_TTL_MS) {
    console.log(
      `[embedded-context] session store cache HIT: agentId=${agentId}, age=${now - cached.timestamp}ms`,
    );
    return cached.store;
  }

  try {
    const store = await sessionApi.loadSessionStore(storePath, { skipCache: false });
    if (store !== undefined) {
      // Defensive copy — prevents downstream mutation from corrupting the cache
      sessionStoreCache.set(cacheKey, { store: structuredClone(store), timestamp: now });
      purgeExpiredSessionStoreCache();
    }
    return store;
  } catch {
    try {
      return await (sessionApi.loadSessionStore as (storePath?: unknown) => Promise<unknown> | unknown)(storePath);
    } catch {
      return undefined;
    }
  }
}

function getFromCollection(collection: unknown, keys: string[]): unknown {
  if (collection instanceof Map) {
    for (const key of keys) {
      const value = collection.get(key);
      if (value !== undefined) return value;
    }
    return undefined;
  }

  if (Array.isArray(collection)) {
    return collection.find((entry) => {
      if (!isRecord(entry)) return false;
      return keys.some((key) => (
        entry.sessionKey === key
        || entry.sessionId === key
        || entry.id === key
        || entry.key === key
      ));
    });
  }

  if (isRecord(collection)) {
    for (const key of keys) {
      const value = collection[key];
      if (value !== undefined) return value;
    }
  }

  return undefined;
}

function getDirectSessionEntry(store: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    for (const candidate of [key, key.toLowerCase()]) {
      const value = store[candidate];
      if (isRecord(value) && !Array.isArray(value)) return value;
    }
  }
  return undefined;
}

function currentEntryCandidate(store: Record<string, unknown>, keys: string[]): unknown {
  const directEntry = getDirectSessionEntry(store, keys);
  if (directEntry !== undefined) return directEntry;

  for (const key of ["currentEntry", "currentSession", "activeEntry"]) {
    const value = store[key];
    if (isRecord(value)) return value;
  }

  if (isRecord(store.current)) return store.current;
  if (isRecord(store.active)) return store.active;

  const collections = [
    store.entries,
    store.sessions,
    store.items,
    store.byKey,
    store.sessionEntries,
  ];
  for (const collection of collections) {
    const value = getFromCollection(collection, keys);
    if (value !== undefined) return value;
  }

  for (const key of ["currentSessionKey", "currentSessionId", "currentKey", "currentId", "sessionKey", "sessionId"]) {
    const currentKey = asString(store[key]);
    if (!currentKey) continue;
    for (const collection of collections) {
      const value = getFromCollection(collection, [currentKey]);
      if (value !== undefined) return value;
    }
  }

  return undefined;
}

/**
 * Extract a UUID from a sessionKey by scanning all colon-separated segments.
 *
 * Session keys follow various formats:
 *   agent:{agent}:dashboard:{uuid}                  — 4 parts, UUID is last
 *   agent:{agent}:session:{uuid}:user:{surface}     — 7 parts, UUID is part[3]
 *
 * Instead of assuming the UUID is always at a fixed position, this function
 * scans all segments for a UUID pattern, which is robust across formats.
 */
function extractUuidFromSessionKey(sessionKey: string | undefined): string | undefined {
  if (!sessionKey) return undefined;
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  for (const part of sessionKey.split(":")) {
    if (UUID_RE.test(part)) return part;
  }
  return undefined;
}

function resolveCurrentSessionEntry(store: unknown, sessionKey: string | undefined): Record<string, unknown> | undefined {
  if (!isRecord(store)) return undefined;

  const keys = [
    sessionKey,
    asString(store.currentSessionKey),
    asString(store.currentSessionId),
    asString(store.currentKey),
    asString(store.currentId),
    asString(store.sessionKey),
    asString(store.sessionId),
  ].filter((key): key is string => Boolean(key));

  const entry = currentEntryCandidate(store, keys);
  return isRecord(entry) ? entry : undefined;
}

function extractModelSelection(value: unknown): ModelSelection {
  if (!isRecord(value)) {
    return splitModelRef(asString(value));
  }

  const primary = isRecord(value.model) && "primary" in value.model
    ? extractModelSelection(value.model.primary)
    : {};

  const nestedPrimary = "primary" in value
    ? extractModelSelection(value.primary)
    : {};

  const provider = firstString(
    value.modelProvider,
    value.providerOverride,
    value.provider,
    value.providerId,
    primary.provider,
    nestedPrimary.provider,
  );
  const modelRef = splitModelRef(
    firstString(
      value.model,
      value.modelOverride,
      value.modelName,
      value.name,
      primary.model,
      nestedPrimary.model,
    ),
    provider,
  );

  return {
    provider: modelRef.provider,
    model: modelRef.model,
    authProfileId: firstString(
      value.authProfileOverride,
      value.authProfileId,
      value.authProfile,
      primary.authProfileId,
      nestedPrimary.authProfileId,
    ),
    authProfileIdSource: firstString(
      value.authProfileOverrideSource,
      value.authProfileIdSource,
      value.authProfileSource,
      primary.authProfileIdSource,
      nestedPrimary.authProfileIdSource,
    ),
  };
}

function resolveConfigModel(config: unknown, agentId: string): ModelSelection {
  const agentConfig = getPath(config, ["agents", agentId]);
  const defaultPrimary = getPath(config, ["agents", "defaults", "model", "primary"]);

  const candidates = [
    getPath(agentConfig, ["model", "primary"]),
    getPath(agentConfig, ["model"]),
    agentConfig,
    defaultPrimary,
    getPath(config, ["defaults", "model", "primary"]),
    getPath(config, ["model", "primary"]),
    getPath(config, ["model"]),
  ];

  const resolved: ModelSelection = {};
  for (const candidate of candidates) {
    const selection = extractModelSelection(candidate);
    resolved.provider ??= selection.provider;
    resolved.model ??= selection.model;
    resolved.authProfileId ??= selection.authProfileId;
    resolved.authProfileIdSource ??= selection.authProfileIdSource;
    if (resolved.provider && resolved.model && resolved.authProfileId && resolved.authProfileIdSource) break;
  }

  return resolved;
}

function modelSource(selection: ModelSelection, source: EmbeddedAgentContext["modelSource"]): EmbeddedAgentContext["modelSource"] | undefined {
  return selection.provider || selection.model || selection.authProfileId ? source : undefined;
}

function requireResolvedPath(value: string | undefined, label: string, sessionKey: string | undefined): string {
  if (value) return value;
  throw new Error(`无法解析当前 OpenClaw 会话${label}，embedded-agent 未启动。sessionKey: ${sessionKey ?? "未知"}`);
}

function safeResolveString(resolve: () => unknown): string | undefined {
  try {
    return asString(resolve());
  } catch {
    return undefined;
  }
}

function firstStringLazy(...resolvers: Array<() => unknown>): string | undefined {
  for (const resolve of resolvers) {
    const value = safeResolveString(resolve);
    if (value) return value;
  }
  return undefined;
}

export async function resolveEmbeddedAgentContext(
  api: EmbeddedAgentRuntimeApi,
  toolCtx?: EmbeddedAgentToolContext,
  sessionKeyArg?: string,
): Promise<EmbeddedAgentContext> {
  const sessionKey = firstString(toolCtx?.sessionKey, sessionKeyArg);
  const agentId = deriveAgentId(toolCtx, sessionKey);
  const userId = asString(toolCtx?.userId);
  const config = await loadConfig(api);
  const store = await loadSessionStore(api, agentId, userId);
  const sessionEntry = resolveCurrentSessionEntry(store, sessionKey);

  const sessionSelection = extractModelSelection(sessionEntry);
  const configSelection = resolveConfigModel(config, agentId);
  const runtimeSelection = extractModelSelection(api.runtime?.agent?.defaults);

  const provider = sessionSelection.provider ?? configSelection.provider ?? runtimeSelection.provider;
  const model = sessionSelection.model ?? configSelection.model ?? runtimeSelection.model;
  const authProfileId = sessionSelection.authProfileId ?? configSelection.authProfileId ?? runtimeSelection.authProfileId;
  const authProfileIdSource = sessionSelection.authProfileIdSource
    ?? configSelection.authProfileIdSource
    ?? runtimeSelection.authProfileIdSource;

  const selectedSource = modelSource(sessionSelection, "session")
    ?? modelSource(configSelection, "config")
    ?? modelSource(runtimeSelection, "runtime-defaults");

  const resolvedSessionId = firstString(toolCtx?.sessionId, sessionEntry?.sessionId, sessionEntry?.id)
    ?? extractUuidFromSessionKey(sessionKey);
  const resolvedSessionKey = firstString(toolCtx?.sessionKey, sessionKeyArg, sessionEntry?.sessionKey);
  const sessionApi = api.runtime?.agent?.session;
  const sessionId = requireResolvedPath(resolvedSessionId, " ID", resolvedSessionKey);
  const sessionFile = requireResolvedPath(firstStringLazy(
    () => toolCtx?.sessionFile,
    () => sessionApi?.resolveSessionFilePath?.(sessionId, sessionEntry, { agentId }),
    () => sessionEntry?.sessionFile,
  ), "文件", resolvedSessionKey);
  const workspaceDir = requireResolvedPath(firstStringLazy(
    () => toolCtx?.workspaceDir,
    () => api.runtime?.agent?.resolveAgentWorkspaceDir?.(config, agentId),
    () => sessionEntry?.workspaceDir,
  ), "工作目录", resolvedSessionKey);
  const agentDir = requireResolvedPath(firstStringLazy(
    () => toolCtx?.agentDir,
    () => api.runtime?.agent?.resolveAgentDir?.(config, agentId),
    () => sessionEntry?.agentDir,
  ), "Agent 目录", resolvedSessionKey);

  return {
    sessionId,
    sessionKey: resolvedSessionKey,
    agentId,
    messageChannel: firstString(toolCtx?.messageChannel, sessionEntry?.messageChannel),
    senderIsOwner: firstBoolean(toolCtx?.senderIsOwner, sessionEntry?.senderIsOwner),
    sessionFile,
    workspaceDir,
    agentDir,
    config,
    provider,
    model,
    authProfileId,
    authProfileIdSource,
    modelSource: selectedSource,
    skillsSnapshot: isRecord(sessionEntry?.skillsSnapshot) ? sessionEntry.skillsSnapshot : undefined,
  };
}

/**
 * Clear process-level caches.  Intended for test isolation —
 * prevents stale data from leaking between test cases.
 */
export function clearEmbeddedContextCaches(): void {
  configCache = null;
  sessionStoreCache.clear();
}
