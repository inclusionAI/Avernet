import { readFileSync } from 'node:fs';
import { startClaudePrompt, type ClaudePromptHandlers, type RunningClaudePrompt } from './claude-cli-bridge.js';
import { startClaudePromptSdk } from './claude-sdk-bridge.js';
import { createLogger } from './debug.js';
import type { ChatRunnerFactory } from './chat-orchestrator.js';

const log = createLogger('router');

export type ModelEnvConfig = {
  id: string;
  name: string;
  display_name: string;
  env?: Record<string, string>;
};

export type ProviderConfig = {
  id: string;
  name: string;
  enabled: boolean;
  models: ModelEnvConfig[];
};

export type RouterState = {
  providers: ProviderConfig[];
  routeMap: Map<string, Record<string, string>>;
  runner: ChatRunnerFactory;
};

function buildRouteMap(providers: ProviderConfig[]): Map<string, Record<string, string>> {
  const map = new Map<string, Record<string, string>>();
  for (const provider of providers) {
    if (!provider.enabled) continue;
    for (const model of provider.models) {
      const env = model.env ?? { ANTHROPIC_MODEL: model.id };
      map.set(model.id, env);
    }
  }
  return map;
}

export function loadProvidersFromFile(filePath?: string): ProviderConfig[] | null {
  if (!filePath) return null;
  try {
    const raw = readFileSync(filePath, 'utf-8');
    const parsed = JSON.parse(raw) as ProviderConfig[];
    if (!Array.isArray(parsed) || parsed.length === 0) {
      log.warn('loadProviders: empty or invalid file', { filePath });
      return null;
    }
    log.debug('loadProviders: loaded', {
      filePath,
      providers: parsed.length,
      models: parsed.reduce((n, p) => n + p.models.length, 0),
    });
    return parsed;
  } catch (err) {
    log.warn('loadProviders: failed', { filePath, error: (err as Error).message });
    return null;
  }
}

export function resolveEnvForModel(
  routeMap: Map<string, Record<string, string>>,
  modelId?: string,
): Record<string, string> | undefined {
  if (!modelId) return undefined;
  const route = routeMap.get(modelId);
  if (route) return route;
  // Strip provider prefix (e.g. "anthropic/claude-sonnet-4-5" → "claude-sonnet-4-5") and retry
  const slashIdx = modelId.indexOf('/');
  if (slashIdx > 0) {
    const bare = modelId.substring(slashIdx + 1);
    const bareRoute = routeMap.get(bare);
    if (bareRoute) return bareRoute;
    return { ANTHROPIC_MODEL: bare };
  }
  return { ANTHROPIC_MODEL: modelId };
}

export function createRoutedRunner(routeMap: Map<string, Record<string, string>>): ChatRunnerFactory {
  // Bridge selector: `sdk` (default) uses @anthropic-ai/claude-agent-sdk;
  // set `CLAUDE_BRIDGE=cli` to spawn the `claude` binary instead. Mirrors the
  // flag used in server.ts so both the health probe and the runner stay in sync.
  const useSdk = String(process.env.CLAUDE_BRIDGE ?? 'sdk').toLowerCase() !== 'cli';
  return (
    params: {
      cwd: string;
      message: string;
      systemPrompt?: string;
      model?: string;
      permissionMode?: string;
      env?: Record<string, string>;
      resumeSessionId?: string;
      isNewSession?: boolean;
      additionalDirectories?: string[];
      runId?: string;
      sessionKey?: string;
      onInteractionRequested?: import('./claude-sdk-bridge.js').InteractionRequestedRuntimeEvent extends never ? never : (event: import('./claude-sdk-bridge.js').InteractionRequestedRuntimeEvent) => void;
    },
    handlers?: ClaudePromptHandlers,
  ): RunningClaudePrompt => {
    const routeEnv = resolveEnvForModel(routeMap, params.model);
    const mergedEnv = routeEnv
      ? { ...(params.env ?? {}), ...routeEnv }
      : params.env;

    log.warn('route:resolve', {
      requestedModel: params.model,
      resolvedModel: routeEnv?.ANTHROPIC_MODEL ?? params.model,
      routeMatched: routeMap.has(params.model ?? ''),
      routeEnvKeys: routeEnv ? Object.keys(routeEnv) : [],
      bridge: useSdk ? 'sdk' : 'cli',
      cwd: params.cwd,
      sessionKey: params.sessionKey,
      resuming: Boolean(params.resumeSessionId),
    });

    if (useSdk) {
      // Route per-request env vars into process.env for the SDK call; the SDK
      // library reads `ANTHROPIC_MODEL` / related knobs from process.env, and
      // has no param for per-call env overrides. Restore on completion so
      // concurrent runs don't leak into each other beyond the bridge's scope.
      const previousEnv: Record<string, string | undefined> = {};
      if (mergedEnv) {
        for (const [ k, v ] of Object.entries(mergedEnv)) {
          previousEnv[k] = process.env[k];
          process.env[k] = v;
        }
      }
      const sdkModel = routeEnv?.ANTHROPIC_MODEL ?? params.model;
      log.warn('route:sdk-invoke', {
        sdkModel,
        envAnthropicModel: process.env.ANTHROPIC_MODEL,
        envAnthropicBaseUrl: process.env.ANTHROPIC_BASE_URL ? '(set)' : '(unset)',
        envAnthropicAuthTokenSet: Boolean(process.env.ANTHROPIC_AUTH_TOKEN),
        mergedEnvKeys: mergedEnv ? Object.keys(mergedEnv) : [],
      });
      const running = startClaudePromptSdk({
        cwd: params.cwd,
        message: params.message,
        systemPrompt: params.systemPrompt,
        model: sdkModel,
        permissionMode: params.permissionMode,
        additionalDirectories: params.additionalDirectories,
        resumeSessionId: params.resumeSessionId,
        isNewSession: params.isNewSession,
        runId: params.runId,
        sessionKey: params.sessionKey,
        onInteractionRequested: params.onInteractionRequested,
      }, handlers);
      // Best-effort env restore after settle.
      running.completed.finally(() => {
        for (const [ k, v ] of Object.entries(previousEnv)) {
          if (v === undefined) delete process.env[k];
          else process.env[k] = v;
        }
      });
      // SDK has no child process; return the optional-child shape.
      return {
        completed: running.completed,
        abort: running.abort,
      };
    }
    const cliModel = routeEnv?.ANTHROPIC_MODEL ?? params.model;
    log.warn('route:cli-invoke', { cliModel, requestedModel: params.model, cwd: params.cwd });
    return startClaudePrompt({ ...params, env: mergedEnv, model: cliModel }, handlers);
  };
}

const DEFAULT_PROVIDERS: ProviderConfig[] = [
  {
    id: 'anthropic',
    name: 'Claude',
    enabled: true,
    models: [
      { id: 'claude-sonnet-4-5', name: 'Claude Sonnet 4.5', display_name: 'Claude Sonnet 4.5' },
      { id: 'claude-opus-4-1', name: 'Claude Opus 4.1', display_name: 'Claude Opus 4.1' },
    ],
  },
];

export function initRouter(modelsFilePath?: string): RouterState {
  const filePath = modelsFilePath ?? process.env.RELAY_MODELS_FILE;
  const providers = loadProvidersFromFile(filePath) ?? DEFAULT_PROVIDERS;
  const routeMap = buildRouteMap(providers);

  log.debug('init', {
    source: filePath ? 'file' : 'defaults',
    routes: routeMap.size,
  });

  const runner = createRoutedRunner(routeMap);
  return { providers, routeMap, runner };
}
