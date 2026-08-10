// WebSocket gateway bootstrap.
//
// This file composes the subsystems (stores, scheduler, router, bridges) and
// exposes `startGatewayServer`. All protocol handling lives under:
//   - src/gateway/      — ConnectionContext, dispatcher, per-topic handlers
//   - src/interaction/  — unified HITL registry + builders + resolve
//   - src/runtime/      — session-owned runtime registry

import http from 'node:http';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { WebSocketServer } from 'ws';
import { handleHttpRequest } from './http-server.js';
import { createLogger } from './debug.js';
import { SessionStore } from './store.js';
import { CronStore } from './cron/store.js';
import { CronScheduler } from './cron/scheduler.js';
import { McpStore, defaultConfigPath as defaultMcpConfigPath } from './mcp/store.js';
import { SkillsStore, defaultSkillsDir } from './skills/store.js';
import { CommandsStore } from './commands/store.js';
import { setDefaultChatRunner } from './chat-orchestrator.js';
import { initRouter } from './claude-code-router.js';
import { ConnectionContext } from './gateway/connection-context.js';
import { createOrchestratorBridge } from './gateway/orchestrator-bridge.js';
import { handleFrame } from './gateway/frame-dispatcher.js';
import { PendingInteractionRegistry } from './interaction/registry.js';
import { SessionRuntimeRegistry } from './runtime/session-runtime-registry.js';
import { setupFileLogger } from './log-to-file.js';

const log = createLogger('server');

const PORT = Number(process.env.PORT ?? process.env.WS_PORT ?? 18900);
// A singlebox mixed topology runs one gateway process per Claude role.  Keep
// the per-role session/cron state outside the gateway source directory while
// leaving the process cwd inside the checkout (so lifecycle cleanup can verify
// ownership before stopping a PID).
const DATA_DIR = process.env.RELAY_DATA_DIR?.trim()
  ? path.resolve(process.env.RELAY_DATA_DIR)
  : path.join(process.cwd(), '.data');
const DEFAULT_STORE_PATH = path.join(DATA_DIR, 'sessions.json');
const DEFAULT_CRON_JOBS_PATH = path.join(DATA_DIR, 'cron-tasks.json');
const DEFAULT_CONTEXT_TURNS = Number(process.env.CONTEXT_TURNS ?? 8);
const MAX_CONTEXT_CHARS = Number(process.env.MAX_CONTEXT_CHARS ?? 12000);
const TICK_INTERVAL_MS = Number(process.env.TICK_INTERVAL_MS ?? 30000);

const CLAUDE_BRIDGE = String(process.env.CLAUDE_BRIDGE ?? 'sdk').toLowerCase();
const USE_SDK_BRIDGE = CLAUDE_BRIDGE !== 'cli';

const _router = initRouter();
const PROVIDERS = _router.providers;
setDefaultChatRunner(_router.runner);

const MODELS = PROVIDERS.flatMap(provider =>
  provider.models.map(model => ({
    id: model.id,
    provider: provider.id,
    name: model.name,
    display_name: model.display_name,
    enabled: true,
    default: false,
    capabilities: {
      context_window: 128000,
      max_output_tokens: 4096,
      vision: false,
      function_calling: true,
      reasoning: true,
      streaming: true,
      json_mode: true,
    },
  })),
);

export type GatewayServer = {
  port: number;
  close: () => Promise<void>;
  store: SessionStore;
  cronStore: CronStore;
  cronScheduler: CronScheduler;
  mcpStore: McpStore;
  skillsStore: SkillsStore;
  commandsStore: CommandsStore;
};

export type StartGatewayServerOptions = {
  port?: number;
  store?: SessionStore;
  cronStore?: CronStore;
  cronScheduler?: CronScheduler;
  cronAutoStart?: boolean;
  mcpStore?: McpStore;
  skillsStore?: SkillsStore;
  commandsStore?: CommandsStore;
  useSdkBridge?: boolean;
};

export function startGatewayServer(options: StartGatewayServerOptions | number = {}): GatewayServer {
  const opts: StartGatewayServerOptions = typeof options === 'number' ? { port: options } : options;

  const sessionStore = opts.store ?? new SessionStore(DEFAULT_STORE_PATH);
  const cronStore = opts.cronStore ?? new CronStore(DEFAULT_CRON_JOBS_PATH);
  const cronScheduler = opts.cronScheduler ?? new CronScheduler(cronStore);
  const mcpStore = opts.mcpStore ?? new McpStore(defaultMcpConfigPath());
  const skillsStore = opts.skillsStore ?? new SkillsStore({ rootDir: defaultSkillsDir() });
  const commandsStore = opts.commandsStore ?? new CommandsStore();
  const interactionRegistry = new PendingInteractionRegistry();

  // Orphan cleanup: when grace period expires, cancel pending interactions for the session
  const runtimeRegistry = new SessionRuntimeRegistry({
    onOrphanCleanup: (sessionKey: string) => {
      log.debug('orphan-cleanup: cancelling interactions', { sessionKey });
      interactionRegistry.cancelForSession(sessionKey);
    },
  });

  const autoStart = opts.cronAutoStart !== false && process.env.CRON_DISABLED !== '1';
  if (autoStart) cronScheduler.start();

  const bridge = createOrchestratorBridge(interactionRegistry);

  const useSdkBridge = opts.useSdkBridge ?? USE_SDK_BRIDGE;

  const dispatcherDeps = {
    sessionStore,
    cronStore,
    cronScheduler,
    mcpStore,
    skillsStore,
    commandsStore,
    registry: interactionRegistry,
    runtimeRegistry,
    meta: {
      useSdkBridge,
      providers: PROVIDERS,
      models: MODELS,
    },
    chat: {
      store: sessionStore,
      bridge,
      useSdkBridge,
      defaultContextTurns: DEFAULT_CONTEXT_TURNS,
      maxContextChars: MAX_CONTEXT_CHARS,
      registry: interactionRegistry,
      runtimeRegistry,
    },
    interaction: {
      registry: interactionRegistry,
      store: sessionStore,
      bridge,
      runtimeRegistry,
      contextTurns: DEFAULT_CONTEXT_TURNS,
      maxContextChars: MAX_CONTEXT_CHARS,
    },
    sessions: {
      store: sessionStore,
      runtimeRegistry,
      interactionRegistry,
    },
    tickIntervalMs: TICK_INTERVAL_MS,
  };

  const port = opts.port ?? PORT;
  const httpServer = http.createServer((req, res) => handleHttpRequest(req, res, { store: sessionStore }));
  const wss = new WebSocketServer({ server: httpServer });

  wss.on('connection', ws => {
    const ctx = new ConnectionContext(ws, { tickIntervalMs: TICK_INTERVAL_MS, runtimeRegistry });
    log.debug('connection:open', { connId: ctx.connId });
    ctx.event('connect.challenge', { nonce: randomUUID(), ts: Date.now() });
    ws.on('message', async buf => {
      try {
        await handleFrame(ctx, buf.toString(), dispatcherDeps);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        log.error('frame:threw', { connId: ctx.connId, error: message });
        try {
          ws.send(JSON.stringify({ type: 'event', event: 'server.error', payload: { message } }));
        } catch { /* ignore send errors on bad socket */ }
        if (ctx.noteFrameError()) {
          log.warn('connection:frame-error-flood', { connId: ctx.connId });
          try { ws.close(1008, 'frame-error-flood'); } catch { /* ignore */ }
        }
      }
    });
    ws.on('close', () => {
      log.debug('connection:close', { connId: ctx.connId });
      ctx.dispose();
    });
  });

  httpServer.listen(port);
  const actualPort = (httpServer.address() as { port?: number } | null)?.port ?? port;
  return {
    port: actualPort,
    store: sessionStore,
    cronStore,
    cronScheduler,
    mcpStore,
    skillsStore,
    commandsStore,
    close: async () => {
      await cronScheduler.stop();
      await cronStore.flush();
      await mcpStore.flush();
      interactionRegistry.stopScanner();
      runtimeRegistry.shutdown();
      await new Promise<void>((resolve, reject) => {
        wss.close(err => (err ? reject(err) : resolve()));
      });
      await new Promise<void>((resolve, reject) => {
        httpServer.close(err => (err ? reject(err) : resolve()));
      });
    },
  };
}

const IS_ENTRY_POINT = (() => {
  const entry = process.argv[1] ?? '';
  return entry.endsWith('server.ts') || entry.endsWith('server.js');
})();

if (IS_ENTRY_POINT) {
  const logFile = setupFileLogger();
  const server = startGatewayServer(PORT);
  console.log(`claude-code-gateway gateway ws: ws://127.0.0.1:${server.port} (bridge=${USE_SDK_BRIDGE ? 'sdk' : 'cli'}, debug=${process.env.CLAUDE_CODE_GATEWAY_DEBUG ?? process.env.TEAMCLAW_AICODING_RELAY_DEBUG ?? process.env.AIX_DEBUG ?? 'off'}${logFile ? `, log=${logFile.path}` : ''})`);

  const shutdown = async () => {
    console.log('\nShutting down gateway...');
    await server.store.flush();
    await server.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
