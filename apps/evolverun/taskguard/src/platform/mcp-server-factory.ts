/**
 * MCP Server Factory — shared initialization for all MCP entry points.
 *
 * Both mcp-entry.ts (stdio) and hermes-entry.ts (SSE) need the same
 * setup sequence: API client init, pack loading, action registry, MCP server
 * creation, and sampling agent configuration. This module extracts that
 * shared logic so each entry point only provides its transport and adapter
 * factory.
 *
 * @module platform/mcp-server-factory
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { loadWorkflowPackCatalog } from "../packs/resolver.js";
import { createActionRegistry } from "../actions/registry.js";
import { registerPackPythonActions } from "../actions/pack-python.js";
import { getMcpSamplingAgent } from "./mcp-sampling-agent.js";
import { getMcpAgentRunner } from "./mcp-agent-runner.js";
import type { McpAgentLoopCapable } from "./mcp-agent-runner.js";
import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import { createTeClawProviderFromEnv, createTeClawProviderFromConfig } from "./teclaw-provider.js";
import { getAgentSdkRunner } from "./agent-sdk-runner.js";
import { createClawmindInlineServer, createInlineServerDepsFromTaskFlow } from "./agent-sdk-inline-server.js";
import type { TeClawConfig } from "../config/types.js";
import { ApiClient } from "../db/api-client.js";
import { FlowRunApiRepository } from "../db/api-repositories/flow-run-api-repository.js";
import { NodeExecutionApiRepository } from "../db/api-repositories/node-execution-api-repository.js";
import { HttpCallbackConfigApiRepository } from "../db/api-repositories/http-callback-config-api-repository.js";
import { HttpCallbackLogApiRepository } from "../db/api-repositories/http-callback-log-api-repository.js";
import { buildFacadeRegistry, loadDbFacadeBindings, loadApiFacadeBindings, type DbFacadeBinding } from "../facades/registry.js";
import { createDatabase } from "../db/factory.js";
import type { IDatabase } from "../db/types.js";
import { WorkflowSpecApiRepository } from "../db/api-repositories/workflow-spec-api-repository.js";
import { createLogger } from "./logger.js";
import { setFlowRunRepository, setNodeExecutionRepository, setWorkflowNotificationConfig, setHttpCallbackRepositories, setHttpCallbackLogRepository, reloadHttpCallbackConfigs, setEngineName, setRunLogUploader, setGuardianAgent } from "../controller.js";
import { loadConfig } from "../config/loader.js";
import { resolveEngineName } from "./types.js";

// ── Types ──

/**
 * Configuration for creating an MCP server with shared infrastructure.
 *
 * Each entry point provides its own `name` and `logPrefix`, then
 * optionally overrides dependencies (e.g., for testing).
 */
export interface McpServerConfig {
  /** Server name displayed to MCP clients (e.g., "clawmind" or "clawmind-hermes"). */
  name: string;
  /** Log prefix for console messages (e.g., "[clawmind:mcp]" or "[clawmind:hermes]"). */
  logPrefix: string;
  /** System prompt prefix for MCP sampling agent. */
  systemPromptPrefix: string;
  /** Server version. */
  version?: string;
  /** Override action registry (for testing). */
  actionRegistry?: ReturnType<typeof createActionRegistry>;
  /** Override workflow catalog (for testing). */
  workflowCatalog?: ReturnType<typeof loadWorkflowPackCatalog>;
  /** Override API repository (for testing). */
  flowRunApiRepo?: FlowRunApiRepository;
  /** Override sampling agent (for testing or when pre-configured). */
  samplingAgent?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  /** Override agent runner (for testing or when pre-configured). */
  agentRunner?: (params: Record<string, unknown>) => Promise<import("./mcp-adapter.js").EmbeddedAgentResult>;
  /** TeClaw config from application.yaml. When provided, takes priority over env vars. */
  teclawConfig?: TeClawConfig;
  /** Pre-created database instance (for testing or when caller manages DB lifecycle). */
  db?: IDatabase;
  /** Platform type for engine name resolution. Defaults to "mcp-server". */
  platformType?: import("./types.js").PlatformType;
}

/**
 * Result of creating an MCP server with shared infrastructure.
 * The caller is responsible for:
 * 1. Creating an AdapterFactory
 * 2. Calling registerWorkflowTools()
 * 3. Binding to a transport (stdio or SSE)
 */
export interface McpServerBase {
  /** The configured MCP server instance. */
  server: McpServer;
  /** Shared tool dependencies (partially initialized). */
  toolDeps: {
    actionRegistry: ReturnType<typeof createActionRegistry>;
    workflowCatalog?: ReturnType<typeof loadWorkflowPackCatalog>;
    facadeRegistry?: ReturnType<typeof buildFacadeRegistry>;
    flowRunApiRepo?: FlowRunApiRepository;
    samplingAgent?: (params: Record<string, unknown>) => Promise<import("./mcp-adapter.js").EmbeddedAgentResult>;
    agentRunner?: (params: Record<string, unknown>) => Promise<import("./mcp-adapter.js").EmbeddedAgentResult>;
    /** Database instance for DB-first facade and workflow spec resolution. */
    db?: IDatabase;
    /** API-backed workflow spec repository (api database mode). */
    workflowSpecApiRepo?: WorkflowSpecApiRepository;
    /** Run log uploader instance — call flushAll() before process exit. */
    runLogUploader?: { flushAll(): Promise<void> };
  };
}

// ── Helpers ──

/** Race a promise against a timeout, rejecting with a descriptive error on expiry. */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(`Timed out after ${ms}ms`)), ms)
    ),
  ]);
}

// ── Version ──

/** Shared version constant — read once from package.json at build time, fallback to hardcoded. */
export const VERSION = "0.1.0";

// ── Factory ──

/**
 * Create an MCP server with all shared infrastructure initialized.
 *
 * This handles:
 * 1. API client initialization (from CLAWWEB_API_URL + CLAWWEB_API_PRIVATE_KEY env vars)
 * 2. Workflow pack catalog loading
 * 3. Action registry creation
 * 4. MCP server instantiation
 * 5. MCP sampling agent configuration
 *
 * @param config - Server configuration
 * @returns The server and tool deps — caller registers tools and binds transport
 */
export async function createMcpServerBase(config: McpServerConfig): Promise<McpServerBase> {
  const log = createLogger(config.logPrefix.replace(/^\[|\]$/g, ""));
  const version = config.version ?? VERSION;

  // ── 1. Initialize API client for TaskFlow persistence ──
  let flowRunApiRepo: FlowRunApiRepository | undefined = config.flowRunApiRepo;
  let apiClient: ApiClient | undefined;
  if (!flowRunApiRepo) {
    // Priority: env vars > application.yaml api config
    const apiBaseUrl = process.env.CLAWWEB_API_URL;
    const apiPrivateKeyB64 = process.env.CLAWWEB_API_PRIVATE_KEY || process.env.CLAWMIND_PRIVATE_KEY;
    let resolvedBaseUrl = apiBaseUrl;
    let resolvedPrivateKeyB64 = apiPrivateKeyB64;
    if (!resolvedBaseUrl || !resolvedPrivateKeyB64) {
      try {
        const { app: appConfig } = loadConfig();
        resolvedBaseUrl = resolvedBaseUrl || appConfig.api.baseUrl;
        resolvedPrivateKeyB64 = resolvedPrivateKeyB64 || appConfig.api.privateKeyB64;
      } catch {
        // loadConfig may fail in standalone mode — env vars only
      }
    }
    if (resolvedBaseUrl) {
      try {
        // Read iamtoken from env var or application.yaml for Cookie-based auth with clawweb.
        // Only set iamtoken on macOS (local dev) when the value is present in
        // application.yaml — production Linux uses Ed25519 signing, no Cookie auth.
        let resolvedIamtoken: string | undefined;
        const isMacOS = process.platform === "darwin";
        if (isMacOS) {
          resolvedIamtoken = process.env.CLAWMIND_IAMTOKEN;
          if (!resolvedIamtoken) {
            try { resolvedIamtoken = loadConfig().app.api.iamtoken; } catch { /* ignore */ }
          }
        }
        // When privateKeyB64 is empty, create ApiClient without signing — the
        // corp extension's implementation will skip Ed25519 signing and send
        // unsigned requests. This allows API persistence to work without a key.
        apiClient = new ApiClient({
          baseUrl: resolvedBaseUrl,
          privateKeyB64: resolvedPrivateKeyB64 ?? undefined,
          iamtoken: resolvedIamtoken || undefined,
          timeout: parseInt(process.env.CLAWWEB_API_TIMEOUT ?? "5000", 10),
          maxRetries: 3,
        });
        flowRunApiRepo = new FlowRunApiRepository(apiClient);
        setFlowRunRepository(flowRunApiRepo);
        const nodeExecApiRepo = new NodeExecutionApiRepository(apiClient);
        setNodeExecutionRepository(nodeExecApiRepo);
        if (resolvedPrivateKeyB64) {
          log.info(`TaskFlow persistence: API mode → ${resolvedBaseUrl}/api/internal/runs (signed)`);
          log.info(`Node execution persistence: API mode → ${resolvedBaseUrl}/api/internal/node-executions (signed)`);
        } else {
          log.warn(`TaskFlow persistence: API mode → ${resolvedBaseUrl}/api/internal/runs (unsigned — no privateKeyB64 configured)`);
          log.warn(`Node execution persistence: API mode → ${resolvedBaseUrl}/api/internal/node-executions (unsigned — no privateKeyB64 configured)`);
        }
      } catch (err) {
        log.warn(`API client init failed, using in-memory TaskFlow:`, err instanceof Error ? err.message : String(err));
      }
    } else {
      log.info(`TaskFlow persistence: in-memory mode (set CLAWWEB_API_URL or api.baseUrl in application.yaml)`);
    }
  } else {
    setFlowRunRepository(flowRunApiRepo);
  }

  // ── 1a-2. Resolve and set engine name for flow_runs.engine tracking ──
  {
      const { app: appCfg } = loadConfig();
      const platformType = config.platformType ?? "mcp-server";
      const teclawEnabled = config.teclawConfig?.enabled ?? (process.env.TECLAW_ENABLED === "true");
      const engineName = resolveEngineName(platformType, {
        configEngine: appCfg.engine,
        teclawEnabled,
        hasClaudeCodeExecutable: !!process.env.CLAUDE_CODE_EXECUTABLE,
      });
      setEngineName(engineName);
      log.info(`Engine identity: ${engineName}`);
  }

  // ── 1a-3. Guardian Agent (node failure analysis at retry time) ──
  // Note: GuardianAgent needs api and sessionKey which are only available
  // per-command in the MCP server path. The guardian is initialized in
  // index.ts buildDeps for the plugin path. For MCP server path, we set
  // it to null here — guardian will be initialized when tools are registered
  // with toolCtx. This is a safe no-op.
  setGuardianAgent(null);

  // ── 1b. Initialize workflow notification dispatcher ──
  // Pushes node events and completion notifications to clawweb.
  {
    const notifyUrl = process.env.CLAWWEB_URL
      || (flowRunApiRepo ? (() => { try { const { app: ac } = loadConfig(); return ac.api.clawwebUrl || ac.api.baseUrl; } catch { return ""; } })() : "");
    if (notifyUrl) {
      setWorkflowNotificationConfig(notifyUrl);
      log.info(`Workflow notifications → ${notifyUrl}`);
    } else {
      log.info(`Workflow notifications: disabled (no clawweb URL configured)`);
    }
  }

  // ── 1c. Initialize HTTP callback dispatcher ──
  // Loads callback configs from clawweb API (API mode) or local DB (sqlite mode).
  // Required for HTTP callback notifications to external subsystems.
  // (Deferred to after DB/workflow catalog init — see section 2c below)

  // ── 2. Load workflow packs ──
  let workflowCatalog: ReturnType<typeof loadWorkflowPackCatalog> | undefined = config.workflowCatalog;
  if (!workflowCatalog) {
    try {
      workflowCatalog = loadWorkflowPackCatalog();
      log.info(`Loaded ${workflowCatalog.packs.length} packs, ${workflowCatalog.workflows.length} workflows`);
    } catch (err) {
      log.error(`Warning: Failed to load workflow packs:`, err instanceof Error ? err.message : String(err));
    }
  }

  // ── 2b. Resolve DB facade bindings (DB-first, Pack-fallback) ──
  let dbBindings: DbFacadeBinding[] = [];
  let dbInstance: IDatabase | undefined = config.db;
  let workflowSpecApiRepo: WorkflowSpecApiRepository | undefined;

  // Load facade bindings from DB or API, with graceful fallback to Pack-only
  if (apiClient) {
    // API mode: use apiClient for facade bindings and workflow spec resolution
    try {
      dbBindings = await withTimeout(loadApiFacadeBindings(apiClient), 5000);
      workflowSpecApiRepo = new WorkflowSpecApiRepository(apiClient);
      log.info(`Loaded ${dbBindings.length} API facade bindings`);
    } catch (err) {
      log.warn(`API facade bindings load failed (timeout or error), using Pack-only:`, err instanceof Error ? err.message : String(err));
      dbBindings = [];
    }
  } else if (dbInstance) {
    // Pre-created DB instance from config — use it for facade bindings
    try {
      dbBindings = await withTimeout(loadDbFacadeBindings(dbInstance), 5000);
      log.info(`Loaded ${dbBindings.length} DB facade bindings (pre-created instance)`);
    } catch (err) {
      log.warn(`DB facade bindings load failed, using Pack-only:`, err instanceof Error ? err.message : String(err));
      dbBindings = [];
    }
  } else {
    // Try to create a DB instance from DATABASE_MODE config (sqlite/prod only)
    const dbMode = process.env.DATABASE_MODE
      ?? (() => { try { const { database: dc } = loadConfig(); return dc.type === "mysql" ? "prod" : dc.type; } catch { return ""; } })();
    if (dbMode === "sqlite" || dbMode === "prod") {
      try {
        dbInstance = await createDatabase({ fallbackOnFailure: true });
        if (dbInstance.dbType !== "noop") {
          dbBindings = await withTimeout(loadDbFacadeBindings(dbInstance), 5000);
          log.info(`Loaded ${dbBindings.length} DB facade bindings (mode=${dbMode})`);
        } else {
          log.info(`DB mode=${dbMode} but createDatabase fell back to NoOp — using Pack-only facades`);
          dbInstance = undefined;
        }
      } catch (err) {
        log.warn(`DB creation or facade bindings load failed, using Pack-only:`, err instanceof Error ? err.message : String(err));
        dbInstance = undefined;
        dbBindings = [];
      }
    }
  }

  // Build facade registry with both Pack and DB bindings
  const packs = workflowCatalog?.packs ?? [];
  const facadeRegistry = (packs.length > 0 || dbBindings.length > 0)
    ? buildFacadeRegistry(packs, dbBindings)
    : undefined;
  if (facadeRegistry) {
    const dbFacadeCount = facadeRegistry.commands().filter(cmd => {
      const resolved = facadeRegistry.resolve(cmd);
      return resolved?.source === "db";
    }).length;
    log.info(`Facade registry: ${facadeRegistry.commands().join(", ") || "(no facades)"}${dbFacadeCount > 0 ? ` (${dbFacadeCount} from DB)` : ""}`);
  }

  // ── 2b-2. Initialize run log uploader (for run archive) ──
  // Must run AFTER dbInstance is resolved (section 2b above).
  // RunLogUploader collects structured run logs via enqueueRunLog() and
  // batch-uploads them to the DB every 30 seconds.
  // Priority: API mode (RunLogApiRepository) > direct DB mode (RunLogRepository).
  // If neither is available, the uploader is NOT created — creating it with a null
  // repo would cause silent failures in uploadLoop() every 30 seconds.
  let runLogUploader: import("../run-archive/run-log-uploader.js").RunLogUploader | undefined;
  {
    try {
      const { RunLogUploader } = await import("../run-archive/run-log-uploader.js");
      let runLogRepo: import("../db/repositories/types.js").IRunLogRepository | null = null;
      if (apiClient) {
        const { RunLogApiRepository } = await import("../db/api-repositories/run-log-api-repository.js");
        runLogRepo = new RunLogApiRepository(apiClient);
        log.info("Run log uploader: API mode (run archive)");
      } else if (dbInstance && dbInstance.dbType !== "noop") {
        const { RunLogRepository } = await import("../db/repositories/run-log-repository.js");
        runLogRepo = new RunLogRepository(dbInstance);
        log.info(`Run log uploader: DB mode (${dbInstance.dbType})`);
      }
      if (runLogRepo) {
        runLogUploader = new RunLogUploader(runLogRepo, { maxEntriesPerFlow: 500 });
        setRunLogUploader(runLogUploader);
        runLogUploader.start();
      } else {
        log.info("Run log uploader: disabled (no API client or DB — run_logs will not be persisted)");
      }
    } catch (err) {
      log.warn(`Run log uploader init failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // ── 2c. Initialize HTTP callback dispatcher ──
  // Loads callback configs from clawweb API (API mode) or local DB (sqlite mode).
  // Required for HTTP callback notifications to external subsystems configured via clawweb UI.
  // NOTE: setHttpCallbackLogRepository MUST be called before setHttpCallbackRepositories,
  // because setHttpCallbackRepositories creates a new HttpCallbackDispatcher that
  // captures the current _httpCallbackLogRepo value. If logRepo is null at that point,
  // audit logs will be silently skipped.
  {
    // Initialize audit log repository first
    if (apiClient) {
      setHttpCallbackLogRepository(new HttpCallbackLogApiRepository(apiClient));
    } else if (dbInstance && dbInstance.dbType !== "noop") {
      const { HttpCallbackLogRepository } = await import("../db/repositories/http-callback-log-repository.js");
      setHttpCallbackLogRepository(new HttpCallbackLogRepository(dbInstance));
    } else {
      setHttpCallbackLogRepository(null);
    }

    if (apiClient) {
      const httpCallbackConfigRepo = new HttpCallbackConfigApiRepository(apiClient);
      setHttpCallbackRepositories(httpCallbackConfigRepo);
      log.info(`HTTP callback configs: API mode`);
    } else if (dbInstance && dbInstance.dbType !== "noop") {
      const { HttpCallbackConfigRepository } = await import("../db/repositories/http-callback-config-repository.js");
      const httpCallbackConfigRepo = new HttpCallbackConfigRepository(dbInstance);
      setHttpCallbackRepositories(httpCallbackConfigRepo);
      log.info(`HTTP callback configs: DB mode (${dbInstance.dbType})`);
    } else {
      log.info(`HTTP callback configs: disabled (no API client or DB)`);
    }

    // Load configs into dispatcher cache (async, non-blocking)
    (async () => {
      try {
        const catalog = workflowCatalog ?? loadWorkflowPackCatalog();
        const yamlSpecs = new Map<string, import("../types.js").WorkflowSpec>();
        for (const wf of catalog.workflows) {
          if (wf.spec?.id) yamlSpecs.set(wf.spec.id, wf.spec);
        }
        await reloadHttpCallbackConfigs(yamlSpecs);
        log.info(`HTTP callback configs loaded from DB + YAML`);
      } catch (err) {
        log.warn(`HTTP callback config reload failed (non-fatal):`, err instanceof Error ? err.message : String(err));
      }
    })();
  }

  // ── 3. Build action registry ──
  // pack python actions 必须注册到这里:action 节点执行时(default-executor.ts)
  // 调 actionRegistry.execute(actionName),注册名为 manifest.commands 的键(如 "odps.fetch_context")。
  // 原先 fallback 到 createActionRegistry() 是空 registry —— packs 已在 workflowCatalog 加载(L204-262),
  // 却没人拿来填 actionRegistry,导致 MCP/Hermes 路径下所有 action 节点报 "Unknown action"。
  // 对齐 index.ts createDefaultActionRegistry 的做法。
  const actionRegistry = config.actionRegistry ?? (() => {
    const registry = createActionRegistry();
    registerPackPythonActions(registry, workflowCatalog?.packs ?? []);
    return registry;
  })();

  // ── 4. Create MCP Server ──
  // Declare Claude Code Channels capability so that
  // notifications/claude/channel events are delivered as
  // <channel source="clawmind" ...> tags in Claude's context.
  // Without the --channels startup flag, events are silently discarded
  // (backward compatible — equivalent to old notifications/message behavior).
  const server = new McpServer({
    name: config.name,
    version,
  }, {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions:
      "Workflow progress events arrive as <channel source=\"clawmind\" ...> tags. " +
      "Each event has meta attributes: flow_id, node_id, workflow_id, event_type. " +
      "Read the events and inform the user about workflow progress, node completions, " +
      "and failures.\n\n" +
      "IMPORTANT: When you start a workflow with workflow_engine_dispatch (run), the " +
      "workflow may execute asynchronously. After starting a workflow:\n" +
      "1. Use workflow_recent_events to check for recent progress events (pass flowId to filter)\n" +
      "2. Use workflow_inspect to get the full execution state of a flow (auto cross-session)\n" +
      "3. Poll workflow_recent_events every 30-60 seconds until the workflow completes or " +
      "reaches a wait node requiring user action\n" +
      "Channel notifications (if available) will also arrive, but polling is the reliable fallback.",
  });

  // ── 5. MCP Sampling Agent ──
  let samplingAgent: ((params: Record<string, unknown>) => Promise<import("./mcp-adapter.js").EmbeddedAgentResult>) | undefined = config.samplingAgent;
  if (!samplingAgent) {
    try {
      samplingAgent = getMcpSamplingAgent(server as unknown as import("./mcp-sampling-agent.js").McpSamplingCapable, {
        systemPromptPrefix: config.systemPromptPrefix,
      });
      log.info("MCP sampling agent configured for embedded-agent/subagent support");
    } catch {
      log.error("Warning: MCP sampling not available — embedded-agent/subagent nodes will not work");
    }
  }

  // ── MCP Agent Runner (TeClaw WebSocket > Agent SDK > sampling) ──
  let agentRunner: ((params: Record<string, unknown>) => Promise<import("./mcp-adapter.js").EmbeddedAgentResult>) | undefined = config.agentRunner;
  if (!agentRunner) {
    try {
      // TeClaw WebSocket Provider (Channel 2) — config file takes priority over env vars
      const teclawProvider = config.teclawConfig
        ? createTeClawProviderFromConfig(config.teclawConfig)
        : createTeClawProviderFromEnv();

      // Agent SDK runner (Path C) — 进程内函数调用, multi-turn with deterministic tools
      // The claude CLI authenticates on its own (OAuth, stored credentials).
      // ANTHROPIC_API_KEY is optional — only passed through if explicitly set.
      let agentSdkRunner: ((params: Record<string, unknown>) => Promise<EmbeddedAgentResult>) | null = null;
      const apiKey = process.env.ANTHROPIC_API_KEY;
      log.info(`Agent SDK init: ANTHROPIC_API_KEY=${apiKey ? "set (value redacted)" : "not set (claude CLI will use its own auth)"} CLAUDE_CODE_EXECUTABLE=${process.env.CLAUDE_CODE_EXECUTABLE ?? "not set"} TECLAW_ENABLED=${config.teclawConfig?.enabled ?? "undefined"}`);
      try {
        // Inline MCP server with workflow_state and workflow_runs tools.
        // Uses a deferred TaskFlowAdapter — the actual adapter is created per-session
        // in mAdapterFactory, so we pass a lazy wrapper that delegates at call time.
        const inlineServer = createClawmindInlineServer({
            getFlowState: async (flowId: string) => {
              // At this point the inline server tools are called from within
              // an Agent SDK query() loop, which runs during a workflow node
              // execution. This is the explicit privileged process-local read path.
              // TODO: Pass the actual TaskFlowAdapter from the running session's controllerDeps
              //       for better accuracy (especially API mode). For now, the static
              //       Map in DatabaseTaskFlowAdapter covers the common case.
              const { DatabaseTaskFlowAdapter } = await import("./database-taskflow.js");
              return DatabaseTaskFlowAdapter.getGlobalFlow(flowId);
            },
            listFlows: async (opts) => {
              const { DatabaseTaskFlowAdapter } = await import("./database-taskflow.js");
              let flows = DatabaseTaskFlowAdapter.listGlobalFlows();
              if (opts.workflowId) {
                flows = flows.filter(f => String(f.goal ?? "").includes(opts.workflowId!));
              }
              if (typeof opts.limit === "number" && opts.limit > 0) {
                flows = flows.slice(0, opts.limit);
              }
              return flows;
            },
          });
          agentSdkRunner = getAgentSdkRunner({
            apiKey: apiKey || undefined,
            inlineMcpServer: inlineServer,
          });
          log.info(`Agent SDK runner initialized (进程内函数调用, apiKey=${apiKey ? "provided" : "not set — claude CLI will use its own auth"})`);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          log.warn(`Agent SDK runner init failed: ${msg.slice(0, 200)}`);
        }

      agentRunner = getMcpAgentRunner(
        server as unknown as McpAgentLoopCapable,
        {
          fallbackSamplingAgent: samplingAgent,
          systemPromptPrefix: config.systemPromptPrefix,
          teclawProvider,
          agentSdkRunner,
        },
      );

      // Log the 3-path execution model
      if (teclawProvider) {
        log.info("MCP agent runner: TeClaw WS > Agent SDK > sampling");
      } else if (agentSdkRunner) {
        log.info("MCP agent runner: Agent SDK > sampling (no TeClaw WS)");
      } else {
        log.info("MCP agent runner: sampling only (no TeClaw WS, no ANTHROPIC_API_KEY)");
      }
    } catch {
      log.warn("MCP agent runner not available — will use sampling agent fallback");
    }
  }

  return {
    server,
    toolDeps: {
      actionRegistry,
      workflowCatalog,
      facadeRegistry,
      flowRunApiRepo,
      samplingAgent,
      agentRunner,
      db: dbInstance,
      workflowSpecApiRepo,
      runLogUploader,
    },
  };
}
