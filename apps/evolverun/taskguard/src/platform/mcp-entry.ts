#!/usr/bin/env node
/**
 * MCP Server Entry Point — ClawMind as a standalone MCP Server.
 *
 * Supports two transport modes (controlled by MCP_TRANSPORT env var):
 * - "stdio" (default): stdio transport for 1:1 local mode
 * - "http-sse": HTTP/SSE transport for remote/N:1 deployment
 *
 * TeClaw integration:
 * - Channel 1 (MCP): tools/list + tools/call via MCP transport
 * - Channel 2 (WebSocket): Agent Loop + chatInject + abort + approval via /ws/v1/chat
 *
 * Usage:
 *   node dist/esm/platform/mcp-entry.js                          (stdio, default)
 *   MCP_TRANSPORT=http-sse node dist/esm/platform/mcp-entry.js   (HTTP/SSE)
 *   node dist/esm/platform/mcp-entry.js --init                   (SessionStart hook mode)
 *
 * Or configure in Claude Code's MCP settings:
 *   {
 *     "mcpServers": {
 *       "clawmind": {
 *         "command": "node",
 *         "args": ["/path/to/clawmind/dist/esm/platform/mcp-entry.js"],
 *         "env": { "SKILL_ROOT": "/path/to/workflows" }
 *       }
 *     }
 *   }
 *
 * --init mode:
 *   Called by the SessionStart hook (hooks.json). Loads packs + DB facade
 *   bindings, generates commands/{facade}.md and skills/{facade}/SKILL.md
 *   into the plugin directory (CLAUDE_PLUGIN_ROOT), and outputs a facade
 *   summary to stdout for context injection.
 *
 * @module platform/mcp-entry
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";
import { randomUUID } from "crypto";
import { dirname, resolve as resolvePath } from "node:path";
import { existsSync } from "node:fs";
import { createServer as createHttpsServer } from "https";
import { generateSelfSignedCert } from "./self-signed-cert.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createMcpServerAdapter, type McpServerAdapterOptions } from "./mcp-adapter.js";
import { registerWorkflowTools, type AdapterFactory, type AdapterContext, type WorkflowToolDeps, setTeClawSessionContext, deleteTeClawSessionContext } from "./mcp-tools.js";
import { createMcpServerBase, VERSION } from "./mcp-server-factory.js";
import { createTeClawProviderFromEnv, createTeClawProviderFromConfig } from "./teclaw-provider.js";
import { createPerSessionEmbeddedAgentFn } from "./mcp-agent-runner.js";
import { loadConfig } from "../config/loader.js";
import { loadWorkflowPackCatalog } from "../packs/resolver.js";
import { buildFacadeRegistry, loadDbFacadeBindings, loadApiFacadeBindings, type DbFacadeBinding } from "../facades/registry.js";
import { createDatabase } from "../db/factory.js";
import { ApiClient } from "../db/api-client.js";
import { generateFacadeCommands, formatFacadeSummary } from "./facade-command-generator.js";
import { loadBotId, loadOwnerId } from "../credentials.js";

// ── Transport Configuration ──

/** Supported MCP transport types. */
export type McpTransportType = "stdio" | "http-sse";

/** Valid MCP_TRANSPORT values (for validation). */
export const MCP_TRANSPORT_VALUES: readonly string[] = ["stdio", "http-sse"];

/** Resolved transport configuration. */
export interface TransportConfig {
  type: McpTransportType;
  /** Port for HTTP/SSE transport. Default: 3100. */
  port: number;
  /** Whether to enable HTTPS (auto self-signed cert). Default: false. */
  tls: boolean;
}

/**
 * Resolve transport configuration from environment variables.
 *
 * - MCP_TRANSPORT: "stdio" (default) or "http-sse"
 * - MCP_PORT: port number for HTTP/SSE (default: 3100)
 * - MCP_TLS: "1" or "true" to enable HTTPS with auto self-signed cert
 */
export function resolveTransportConfig(): TransportConfig {
  const rawType = process.env.MCP_TRANSPORT ?? "stdio";
  if (!MCP_TRANSPORT_VALUES.includes(rawType)) {
    throw new Error(
      `Invalid MCP_TRANSPORT="${rawType}". Must be one of: ${MCP_TRANSPORT_VALUES.join(", ")}`,
    );
  }
  const rawTls = process.env.MCP_TLS?.toLowerCase() ?? "";
  const tls = rawTls === "1" || rawTls === "true" || rawTls === "yes";
  return {
    type: rawType as McpTransportType,
    port: parseInt(process.env.MCP_PORT ?? "3100", 10),
    tls,
  };
}

// ── Main ──

async function main() {
  console.error("[clawmind:mcp] Starting MCP Server v" + VERSION);

  const transportConfig = resolveTransportConfig();

  // ── Load application config (configs/application.yaml + env var overrides) ──
  const { app: appConfig } = loadConfig();
  const teclawConfig = appConfig.teclaw;

  // ── Shared initialization (API client, packs, action registry, sampling) ──
  const { server, toolDeps } = await createMcpServerBase({
    name: "clawmind",
    logPrefix: "[clawmind:mcp]",
    systemPromptPrefix: "You are a workflow step executor in the ClawMind workflow engine.",
    teclawConfig,
  });

  // ── TeClaw Channel 2: WebSocket Provider (config file preferred, env var fallback) ──
  // TeClaw WS is for TeClaw/BCS platforms only — it MUST NOT be used when:
  //   1. CLAUDE_CODE_EXECUTABLE is set (Claude Code + Agent SDK path preferred)
  //   2. Running in stdio transport mode — the WS "sole path" design does NOT
  //      degrade on failure, so ECONNREFUSED on 127.0.0.1:8080 kills the workflow.
  //      stdio mode is used by Claude Code plugins, which should use Agent SDK instead.
  // The CLAUDE_CODE_EXECUTABLE check is retained as the primary safety net, but
  // stdio mode is added as a secondary guard: if the env var is missing or empty
  // (e.g., claude CLI not found by install.sh), stdio transport still prevents
  // the TeClaw WS from being activated.
  const hasClaudeCodeExecutable = !!process.env.CLAUDE_CODE_EXECUTABLE;
  const isStdioTransport = transportConfig.type === "stdio";
  const effectiveTeclawEnabled = (hasClaudeCodeExecutable || isStdioTransport)
    ? false
    : teclawConfig.enabled;
  if ((hasClaudeCodeExecutable || isStdioTransport) && teclawConfig.enabled) {
    const reason = hasClaudeCodeExecutable
      ? "CLAUDE_CODE_EXECUTABLE detected"
      : "stdio transport mode (no TeClaw WS in stdio)";
    console.error(`[clawmind:mcp] ${reason} — forcing TeClaw OFF (Agent SDK / sampling path preferred)`);
  }
  const teclawProvider = effectiveTeclawEnabled
    ? createTeClawProviderFromConfig(teclawConfig) ?? createTeClawProviderFromEnv()
    : undefined;
  if (teclawProvider) {
    console.error("[clawmind:mcp] TeClaw WebSocket: " + teclawProvider.wsUrl);
  } else if (teclawConfig.chatInjectUrl) {
    console.error("[clawmind:mcp] chat/inject HTTP: " + teclawConfig.chatInjectUrl);
    console.error("[clawmind:mcp] WARNING: chatInjectUrl set but wsUrl not set. Agent Loop via WebSocket unavailable.");
  } else {
    console.error("[clawmind:mcp] TeClaw WebSocket: not configured (set teclaw.wsUrl in application.yaml or TECLAW_WS_URL env var)");
  }
  if (toolDeps.agentRunner) {
    console.error("[clawmind:mcp] agent runner: " + (teclawProvider ? "TeClaw WebSocket (sole path)" : "sampling (stdio mode)"));
  }

  // ── Adapter factory ──
  const adapterFactory: AdapterFactory = (context: AdapterContext) => {
    // TeClaw config: prefer config file values, fall back to env vars
    const teclawWsHeaders = teclawConfig.wsHeaders && Object.keys(teclawConfig.wsHeaders).length > 0
      ? teclawConfig.wsHeaders
      : undefined;

    const adapterOptions: McpServerAdapterOptions = {
      sessionKey: context.sessionKey,
      sessionId: context.sessionId,
      user: context.user,
      // TeClaw headers from x-teclaw-session-key / x-teclaw-bot-id
      teclawSessionKey: context.teclawSessionKey,
      teclawBotId: context.teclawBotId,
      abortSignal: context.abortSignal,
      skillRoot: process.env.SKILL_ROOT || process.cwd(),
      flowRunApiRepo: toolDeps.flowRunApiRepo,
      // Always pass the MCP server instance for chatInject notifications.
      mcpServer: server,
      // TeClaw config from application.yaml (preferred over individual fields)
      teclawConfig,
      // TeClaw chat/inject HTTP endpoint (from config, env var as fallback)
      chatInjectUrl: teclawConfig.chatInjectUrl || process.env.TECLAW_CHAT_INJECT_URL,
      chatInjectKey: teclawConfig.chatInjectKey || process.env.TECLAW_CHAT_INJECT_KEY,
      // TeClaw base URL (deprecated, from config or env var)
      teclawBaseUrl: teclawConfig.baseUrl || process.env.TECLAW_BASE_URL,
      // TeClaw WebSocket Channel 2 (from config, env var as fallback)
      // When teclaw is disabled (e.g., TECLAW_ENABLED=false or CLAUDE_CODE_EXECUTABLE set),
      // skip passing wsUrl/wsToken so mcp-adapter doesn't create a provider
      teclawWsUrl: effectiveTeclawEnabled ? (teclawConfig.wsUrl || process.env.TECLAW_WS_URL) : undefined,
      teclawWsToken: effectiveTeclawEnabled ? (teclawConfig.wsToken || process.env.TECLAW_WS_TOKEN) : undefined,
      teclawWsHeaders,
      onProgress: (text, details) => {
        console.error(`[clawmind:mcp] progress: ${text}`, details ?? "");
      },
    };
    const { adapter, wsTeClawProvider } = createMcpServerAdapter(adapterOptions);

    // ── Per-session embeddedAgentFn ──
    // When a per-session TeClawProvider is available (has x-target-bot-id from the
    // current request), create an agentRunner that uses it. This ensures chat.send
    // is routed to the correct bot session — the global provider lacks per-request
    // headers and will fail with "Not connected" / HTTP 400.
    const embeddedAgentFn = createPerSessionEmbeddedAgentFn({
      teclawProvider: wsTeClawProvider,
      chatInject: adapter.chatInject,
      globalAgentRunner: toolDeps.agentRunner,
      samplingAgent: toolDeps.samplingAgent,
    });
    if (wsTeClawProvider) {
      console.error(`[clawmind:mcp] adapterFactory: using per-session TeClaw WS provider for agentRunner (botId=${context.teclawBotId ?? "none"})`);
    } else {
      console.error(`[clawmind:mcp] adapterFactory: no per-session TeClaw WS provider, using ${toolDeps.agentRunner ? "global agentRunner" : "samplingAgent"}`);
    }

    return { adapter, embeddedAgentFn };
  };

  // ── Register shared workflow tools ──
  registerWorkflowTools(server, adapterFactory, toolDeps as WorkflowToolDeps);

  // ── Start server with selected transport ──
  if (transportConfig.type === "http-sse") {
    await startHttpSseServer(server, transportConfig, adapterFactory, toolDeps as WorkflowToolDeps);
  } else {
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("[clawmind:mcp] MCP Server connected on stdio");
  }

  // ── Graceful shutdown: flush pending run_logs before exit ──
  const gracefulShutdown = async () => {
    console.error("[clawmind:mcp] Shutting down — flushing run_logs...");
    try {
      await toolDeps.runLogUploader?.flushAll();
    } catch { /* best-effort */ }
    process.exit(0);
  };
  process.on("SIGINT", gracefulShutdown);
  process.on("SIGTERM", gracefulShutdown);
}

// ── HTTP/SSE Server ──

/**
 * Per-SSE-connection state: each connection gets its own McpServer + transport
 * because the MCP SDK only allows one transport per McpServer instance.
 * Shared state (toolDeps, adapterFactory) is reused across connections.
 */
interface SseConnState {
  transport: SSEServerTransport;
  server: import("@modelcontextprotocol/sdk/server/mcp.js").McpServer;
}

async function startHttpSseServer(
  mcpServer: import("@modelcontextprotocol/sdk/server/mcp.js").McpServer,
  transportConfig: TransportConfig,
  adapterFactory: AdapterFactory,
  toolDeps: WorkflowToolDeps,
): Promise<void> {
  const { port, tls } = transportConfig;
  const app = express();

  // NOTE: No global express.json() middleware — it would consume the raw request
  // stream before route-level express.raw({ type: "*/*" }) can read it, causing
  // "stream is not readable" errors on /messages and /mcp POST endpoints.
  // Instead, each route applies the body parser it needs explicitly.

  // Health check endpoint
  app.get("/health", (_req: express.Request, res: express.Response) => {
    res.json({ status: "ok", service: "clawmind", version: VERSION, transport: "http-sse" });
  });

  // Per-connection state registry (keyed by sessionId)
  const sseConnections = new Map<string, SseConnState>();

  // SSE endpoint — TeClaw connects here for Channel 1.
  // Each GET /sse creates a new McpServer + SSEServerTransport pair so that
  // multiple concurrent clients can be served (the MCP SDK only allows one
  // transport per McpServer instance).
  app.get("/sse", (req: express.Request, res: express.Response) => {
    console.error(`[clawmind:mcp] New SSE connection from ${req.ip} user-agent=${req.headers["user-agent"] ?? "unknown"}`);
    const transport = new SSEServerTransport("/messages", res);
    console.error(`[clawmind:mcp] SSE session created: ${transport.sessionId}`);

    // Create a fresh McpServer for this connection and register all tools.
    const connServer = new McpServer({
      name: "clawmind",
      version: VERSION,
    });
    registerWorkflowTools(connServer, adapterFactory, toolDeps);

    const connState: SseConnState = { transport, server: connServer };
    sseConnections.set(transport.sessionId, connState);
    console.error(`[clawmind:mcp] SSE active sessions: ${sseConnections.size}`);

    transport.onclose = () => {
      sseConnections.delete(transport.sessionId);
      console.error(`[clawmind:mcp] SSE connection closed: ${transport.sessionId}`);
    };

    connServer.connect(transport).catch((err: unknown) => {
      console.error("[clawmind:mcp] Error connecting SSE transport:", err);
    });
  });

  // Messages endpoint — MCP JSON-RPC requests
  // express.raw() consumes the stream and puts the result in req.body (Buffer).
  // We must pass req.body as the parsedBody argument to handlePostMessage,
  // otherwise getRawBody() inside the SDK tries to re-read the already-consumed
  // stream and throws "stream is not readable".
  app.post("/messages", express.raw({ type: "*/*" }), (req: express.Request, res: express.Response) => {
    const sessionId = req.query.sessionId as string;
    const rawBody = Buffer.isBuffer(req.body) ? req.body.toString("utf-8") : typeof req.body === "string" ? req.body : "";
    const bodyPreview = rawBody.substring(0, 500);
    console.error(`[clawmind:mcp] POST /messages sessionId=${sessionId ?? "missing"} content-type=${req.headers["content-type"] ?? "none"}`);
    console.error(`[clawmind:mcp] POST /messages body: ${bodyPreview}`);
    const connState = sessionId ? sseConnections.get(sessionId) : undefined;
    if (!connState) {
      console.error(`[clawmind:mcp] POST /messages rejected: unknown sessionId=${sessionId ?? "missing"}, active=[${[...sseConnections.keys()].join(",")}]`);
      res.status(400).json({ error: "Unknown or missing sessionId" });
      return;
    }
    // express.raw() sets req.body to a Buffer; the SDK expects a UTF-8 string.
    const bodyStr = rawBody || undefined;
    connState.transport.handlePostMessage(req, res, bodyStr).catch((err: unknown) => {
      console.error("[clawmind:mcp] Error handling POST message:", err);
    });
  });

  // ── Streamable HTTP endpoint (/mcp) ──
  // Supports the MCP Streamable HTTP transport (POST for requests, GET for SSE
  // notifications, DELETE for session teardown). TeClaw's default HttpMcpTransport
  // uses this protocol, so providing /mcp means zero-config interop — no need to
  // set transport=Sse on the teclaw side.
  //
  // Each client session gets its own StreamableHTTPServerTransport + McpServer pair
  // (mirroring the per-connection SSE pattern above). This avoids the "Server already
  // initialized" (-32600) error that occurs when a second client's initialize request
  // hits a transport that was already initialized by the first client.

  interface StreamableConnState {
    transport: StreamableHTTPServerTransport;
    server: import("@modelcontextprotocol/sdk/server/mcp.js").McpServer;
    /** TeClaw context captured from HTTP headers at session creation time. */
    teclawContext?: { teclawBotId?: string; teclawSessionKey?: string };
  }

  const streamableSessions = new Map<string, StreamableConnState>();

  // Middleware: ensure Accept header includes both MIME types required by MCP spec.
  // Some clients (e.g., teclaw's HttpMcpTransport) may omit the Accept header,
  // causing the SDK to reject the request with "Not Acceptable". Patch it here
  // so the transport always sees a conformant Accept value.
  app.use("/mcp", (req: express.Request, _res: express.Response, next: express.NextFunction) => {
    const accept = req.headers.accept ?? "";
    const hasJson = accept.includes("application/json");
    const hasSse = accept.includes("text/event-stream");
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    console.error(`[clawmind:mcp] ${req.method} /mcp ip=${req.ip} accept="${accept}" sessionId=${sessionId ?? "none"} content-type=${req.headers["content-type"] ?? "none"}`);

    // Log TeClaw custom headers (x-teclaw-bot-id, x-teclaw-session-key)
    // injected by teclaw's inject_tfe_headers (enterprise mode).
    // These are critical for WS chat.inject routing and session identification.
    const teclawBotId = req.headers["x-teclaw-bot-id"] as string | undefined;
    const teclawSessionKey = req.headers["x-teclaw-session-key"] as string | undefined;
    if (teclawBotId || teclawSessionKey) {
      console.error(`[clawmind:mcp] TeClaw headers received: x-teclaw-bot-id=${teclawBotId ?? "none"} x-teclaw-session-key=${teclawSessionKey ?? "none"}`);
      // Store on the request object so the POST /mcp handler can capture them
      // when creating new sessions. The MCP SDK may not pass custom headers
      // through to extra.requestInfo.headers, so this is our authoritative source.
      (req as express.Request & { _teclawBotId?: string; _teclawSessionKey?: string })._teclawBotId = teclawBotId;
      (req as express.Request & { _teclawBotId?: string; _teclawSessionKey?: string })._teclawSessionKey = teclawSessionKey;
    } else {
      console.error(`[clawmind:mcp] TeClaw headers: none (non-enterprise teclaw or missing inject_tfe_headers)`);
    }

    if (!hasJson || !hasSse) {
      console.error(`[clawmind:mcp] Patching Accept header: "${accept}" → "application/json, text/event-stream"`);
      req.headers.accept = "application/json, text/event-stream";
    }
    next();
  });

  // POST /mcp — JSON-RPC requests.
  // - initialize: create a fresh transport+server pair for the new session.
  // - other methods: route to the existing session via Mcp-Session-Id header.
  app.post("/mcp", express.raw({ type: "*/*" }), async (req: express.Request, res: express.Response) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    // Convert raw Buffer/string from express.raw() into a parsed JSON object.
    let parsedBody: unknown;
    try {
      const raw = Buffer.isBuffer(req.body) ? req.body.toString("utf-8") : typeof req.body === "string" ? req.body : JSON.stringify(req.body);
      parsedBody = JSON.parse(raw);
    } catch {
      parsedBody = undefined;
    }
    const method = (parsedBody as Record<string, unknown>)?.method ?? "unknown";
    const params = (parsedBody as Record<string, unknown>)?.params;
    const bodyPreview = JSON.stringify(parsedBody).substring(0, 500);
    console.error(`[clawmind:mcp] POST /mcp method=${method} sessionId=${sessionId ?? "none"}`);
    console.error(`[clawmind:mcp] POST /mcp body: ${bodyPreview}`);
    if (method === "tools/call" && params) {
      const p = params as Record<string, unknown>;
      console.error(`[clawmind:mcp] POST /mcp tools/call: name=${p.name ?? "?"} args=${JSON.stringify(p.arguments)?.substring(0, 300) ?? "none"}`);
    }

    // ── New session: initialize request ──
    if (method === "initialize") {
      // Capture TeClaw context from HTTP headers before the MCP SDK takes over.
      // The SDK may not forward custom headers to extra.requestInfo.headers,
      // so we store them here as the authoritative source.
      const reqTeclaw = req as express.Request & { _teclawBotId?: string; _teclawSessionKey?: string };
      const capturedTeclaw = {
        teclawBotId: reqTeclaw._teclawBotId,
        teclawSessionKey: reqTeclaw._teclawSessionKey,
      };
      if (capturedTeclaw.teclawBotId || capturedTeclaw.teclawSessionKey) {
        console.error(`[clawmind:mcp] Captured TeClaw context for new session: botId=${capturedTeclaw.teclawBotId ?? "none"} sessionKey=${capturedTeclaw.teclawSessionKey ?? "none"}`);
      }

      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (sid: string) => {
          streamableSessions.set(sid, { transport, server: connServer, teclawContext: capturedTeclaw });
          // Also store in the module-level registry so extractTeClawHeaders()
          // can find them as a fallback when SDK headers are missing.
          if (capturedTeclaw.teclawBotId || capturedTeclaw.teclawSessionKey) {
            setTeClawSessionContext(sid, capturedTeclaw);
          }
          console.error(`[clawmind:mcp] Streamable HTTP session initialized: ${sid}, active=${streamableSessions.size} teclawBotId=${capturedTeclaw.teclawBotId ?? "none"}`);
        },
      });
      const connServer = new McpServer({ name: "clawmind", version: VERSION });
      registerWorkflowTools(connServer, adapterFactory, toolDeps);

      transport.onclose = () => {
        // Remove from map by finding the matching transport
        for (const [sid, state] of streamableSessions) {
          if (state.transport === transport) {
            streamableSessions.delete(sid);
            deleteTeClawSessionContext(sid);
            console.error(`[clawmind:mcp] Streamable HTTP session closed: ${sid}, active=${streamableSessions.size}`);
            break;
          }
        }
      };
      transport.onerror = (err: Error) => {
        console.error("[clawmind:mcp] Streamable HTTP transport error:", err);
      };

      // Connect server to transport before handling the request
      await connServer.connect(transport);
      // Now the transport can process the initialize request
      await transport.handleRequest(req, res, parsedBody);
      return;
    }

    // ── Existing session: route by Mcp-Session-Id header ──
    if (sessionId) {
      const session = streamableSessions.get(sessionId);
      if (session) {
        // Update TeClaw context from headers on every request — teclaw
        // sends x-teclaw-bot-id and x-teclaw-session-key on each MCP call.
        // This ensures the latest values are available even if the initial
        // initialize request didn't have them (e.g., first request from a
        // new conversation that didn't have a session_key yet).
        const reqTeclaw = req as express.Request & { _teclawBotId?: string; _teclawSessionKey?: string };
        if (reqTeclaw._teclawBotId || reqTeclaw._teclawSessionKey) {
          session.teclawContext = {
            teclawBotId: reqTeclaw._teclawBotId,
            teclawSessionKey: reqTeclaw._teclawSessionKey,
          };
          // Also update the module-level registry for extractTeClawHeaders() fallback
          setTeClawSessionContext(sessionId, session.teclawContext);
          console.error(`[clawmind:mcp] Updated TeClaw context for session ${sessionId}: botId=${reqTeclaw._teclawBotId ?? "none"} sessionKey=${reqTeclaw._teclawSessionKey ?? "none"}`);
        }
        await session.transport.handleRequest(req, res, parsedBody);
        return;
      }
      // Session not found (expired or stale)
      console.error(`[clawmind:mcp] POST /mcp unknown sessionId=${sessionId}, active=[${[...streamableSessions.keys()].join(",")}]`);
      res.status(400).json({
        jsonrpc: "2.0",
        error: { code: -32600, message: `Unknown or expired session: ${sessionId}` },
        id: (parsedBody as Record<string, unknown>)?.id ?? null,
      });
      return;
    }

    // ── No session ID on non-initialize request ──
    console.error(`[clawmind:mcp] POST /mcp missing session ID for method=${method}`);
    res.status(400).json({
      jsonrpc: "2.0",
      error: { code: -32600, message: "Missing Mcp-Session-Id header for non-initialize request" },
      id: (parsedBody as Record<string, unknown>)?.id ?? null,
    });
  });

  // GET /mcp — SSE subscription for server-to-client notifications.
  app.get("/mcp", async (req: express.Request, res: express.Response) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    console.error(`[clawmind:mcp] GET /mcp (SSE subscription) sessionId=${sessionId ?? "none"}`);
    if (sessionId) {
      const session = streamableSessions.get(sessionId);
      if (session) {
        await session.transport.handleRequest(req, res);
        return;
      }
    }
    res.status(400).json({ error: "Unknown or missing session ID" });
  });

  // DELETE /mcp — session teardown.
  app.delete("/mcp", async (req: express.Request, res: express.Response) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    console.error(`[clawmind:mcp] DELETE /mcp (session teardown) sessionId=${sessionId ?? "none"}`);
    if (sessionId) {
      const session = streamableSessions.get(sessionId);
      if (session) {
        await session.transport.handleRequest(req, res);
        // onclose callback will clean up the map entry
        return;
      }
    }
    res.status(400).json({ error: "Unknown or missing session ID" });
  });

  if (tls) {
    console.error(`[clawmind:mcp] TLS mode enabled, generating self-signed cert...`);
    const { cert, key } = generateSelfSignedCert();
    const httpsServer = createHttpsServer({ cert, key, requestCert: false, rejectUnauthorized: false }, app);
    httpsServer.on("tlsClientError", (err, tlsSocket) => {
      const remote = (tlsSocket as unknown as { remoteAddress?: string; remotePort?: number });
      const code = (err as Error & { code?: string }).code ?? "UNKNOWN";
      console.error(`[clawmind:mcp] TLS client error: ${code} ${err.message} remote=${remote.remoteAddress ?? "?"}:${remote.remotePort ?? "?"}`);
    });
    httpsServer.on("connection", (socket) => {
      console.error(`[clawmind:mcp] HTTPS new connection from ${socket.remoteAddress}:${socket.remotePort}`);
    });
    httpsServer.on("secureConnection", (tlsSocket) => {
      const proto = (tlsSocket as unknown as { getProtocol?: () => string; alpnProtocol?: string; authorized?: boolean; remoteAddress?: string; remotePort?: number });
      const protocol = proto.getProtocol?.() ?? "unknown";
      const alpn = proto.alpnProtocol ?? "none";
      console.error(`[clawmind:mcp] TLS handshake OK: ${proto.remoteAddress ?? "?"}:${proto.remotePort ?? "?"} protocol=${protocol} alpn=${alpn}`);
    });
    httpsServer.on("clientError", (err) => {
      const code = (err as Error & { code?: string }).code ?? "UNKNOWN";
      console.error(`[clawmind:mcp] HTTPS client error: ${code} ${err.message}`);
    });
    httpsServer.listen(port, () => {
      console.error(`[clawmind:mcp] MCP Server listening on https://127.0.0.1:${port} (http-sse + TLS)`);
      console.error(`[clawmind:mcp] SSE endpoint:      https://127.0.0.1:${port}/sse`);
      console.error(`[clawmind:mcp] Streamable HTTP:   https://127.0.0.1:${port}/mcp`);
      console.error(`[clawmind:mcp] Health check:      https://127.0.0.1:${port}/health`);
      console.error(`[clawmind:mcp] TLS: self-signed cert (auto-generated, rejectUnauthorized=false)`);
    });
  } else {
    app.listen(port, () => {
      console.error(`[clawmind:mcp] MCP Server listening on http://127.0.0.1:${port} (http-sse)`);
      console.error(`[clawmind:mcp] SSE endpoint:      http://127.0.0.1:${port}/sse`);
      console.error(`[clawmind:mcp] Streamable HTTP:   http://127.0.0.1:${port}/mcp`);
      console.error(`[clawmind:mcp] Health check:      http://127.0.0.1:${port}/health`);
    });
  }
}

// ── Init Mode (SessionStart hook) ──

/**
 * Handle `--init` mode for the SessionStart hook.
 *
 * When Claude Code starts a new session, it calls `mcp-entry.js --init`
 * via the SessionStart hook defined in hooks.json. This mode:
 *
 * 1. Loads workflow packs + DB facade bindings
 * 2. Builds the facade registry (same logic as the MCP server)
 * 3. Generates `commands/{facade}.md` and `skills/{facade}/SKILL.md`
 *    into the plugin directory so Claude Code registers them as slash commands
 *    on the next startup
 * 4. Outputs a facade summary to stdout — Claude Code captures this as
 *    `<system-reminder>` context so the LLM knows which facades are available
 *    in the current session
 */
/**
 * Infer the plugin root directory from the script's own path.
 *
 * Claude Code's hook system substitutes `${CLAUDE_PLUGIN_ROOT}` in the hook
 * command string before execution — the variable appears in `process.argv`
 * as a resolved absolute path — but it does NOT set `CLAUDE_PLUGIN_ROOT`
 * as an environment variable.  So when `mcp-entry.js --init` runs, we need
 * to derive the plugin root from the script location instead.
 *
 * Layout:
 *   {pluginRoot}/dist/esm/platform/mcp-entry.js
 *   {pluginRoot}/commands/{facade}.md
 *   {pluginRoot}/skills/{facade}/SKILL.md
 *
 * So from `import.meta.dirname` we go up 3 levels:
 *   platform → esm → dist → {pluginRoot}
 */
function resolvePluginRootFromScriptPath(): string | undefined {
  // import.meta.dirname is available in Node 21.2+
  const scriptDir = import.meta.dirname;
  if (!scriptDir) return undefined;

  // Walk upward from the script directory looking for the plugin root.
  //
  // Marketplace install layout:
  //   {pluginRoot}/dist/esm/platform/mcp-entry.js
  //   {pluginRoot} has .claude-plugin/ and commands/ + hooks/
  //   → 3 levels up from script lands directly on pluginRoot
  //
  // Local dev layout:
  //   ClawMind/dist/esm/platform/mcp-entry.js  (dist is a real dir, not via clawmind-plugin)
  //   ClawMind/clawmind-plugin/ has .claude-plugin/ and commands/ + hooks/
  //   → clawmind-plugin is a SIBLING of dist/, not an ancestor — upward walk
  //     never reaches it. So after each upward step, also check if there's
  //     a `clawmind-plugin/` child directory that qualifies.
  //
  // Strategy: at each level going up, check:
  //   1. Does THIS directory have .claude-plugin/ or (commands/ + hooks/)? → return it
  //   2. Does THIS directory have a `clawmind-plugin/` child with markers? → return child
  const isPluginRoot = (dir: string): boolean =>
    existsSync(resolvePath(dir, ".claude-plugin")) ||
    (existsSync(resolvePath(dir, "commands")) && existsSync(resolvePath(dir, "hooks")));

  let dir: string = scriptDir;
  for (let i = 0; i < 8; i++) {
    dir = resolvePath(dir, "..");
    if (isPluginRoot(dir)) {
      return dir;
    }
    // Check for a clawmind-plugin/ child (local dev layout)
    const child = resolvePath(dir, "clawmind-plugin");
    if (isPluginRoot(child)) {
      return child;
    }
  }

  // No marker found — fall back to 3-level-up assumption (marketplace layout)
  return resolvePath(scriptDir, "..", "..", "..");
}

async function handleInitMode(): Promise<void> {
  console.error("[clawmind:mcp:init] Running --init mode...");

  // 1. Load workflow packs
  let packCount = 0;
  let workflowCount = 0;
  let catalog: ReturnType<typeof loadWorkflowPackCatalog> | undefined;
  try {
    catalog = loadWorkflowPackCatalog();
    packCount = catalog.packs.length;
    workflowCount = catalog.workflows.length;
    console.error(`[clawmind:mcp:init] Loaded ${packCount} packs, ${workflowCount} workflows`);
  } catch (err) {
    console.error(`[clawmind:mcp:init] Warning: Failed to load packs: ${err instanceof Error ? err.message : String(err)}`);
  }

  // 2. Load DB facade bindings (graceful degradation)
  let dbBindings: DbFacadeBinding[] = [];
  const DB_TIMEOUT_MS = 3000;
  try {
    const { database: dbCfg } = loadConfig();
    const dbType = dbCfg.type ?? process.env.DATABASE_MODE ?? "sqlite";

    if (dbType === "api" && dbCfg.api) {
      const apiClient = new ApiClient(dbCfg.api);
      // Read bot credentials to filter facade bindings by permission.
      // In --init mode (Claude Code session start), we only want facades
      // for workflows the current bot has view access to.
      const botId = loadBotId() ?? undefined;
      const botOwnerId = loadOwnerId() ?? undefined;
      if (botId || botOwnerId) {
        console.error(`[clawmind:mcp:init] Filtering facade bindings by bot: owner=${botOwnerId ?? "-"}, bot=${botId ?? "-"}`);
      }
      dbBindings = await loadApiFacadeBindings(apiClient, botId, botOwnerId);
      console.error(`[clawmind:mcp:init] Loaded ${dbBindings.length} DB facade bindings (API mode, bot-filtered)`);
    } else if (dbType !== "noop") {
      // Create DB with timeout; if timeout fires first, we still need to
      // close the DB if it eventually resolves to avoid leaking connections.
      let dbInstance: Awaited<ReturnType<typeof createDatabase>> | null = null;
      const dbPromise = createDatabase();
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("DB connection timeout")), DB_TIMEOUT_MS),
      );
      try {
        const db = await Promise.race([dbPromise, timeoutPromise]);
        dbInstance = db;
        dbBindings = await loadDbFacadeBindings(db);
        console.error(`[clawmind:mcp:init] Loaded ${dbBindings.length} DB facade bindings`);
      } finally {
        // Close the DB we actually got (even if timeout won the race and
        // dbPromise resolved later, we need to clean it up).
        if (!dbInstance) {
          // Timeout won — try to close the DB if it eventually resolved.
          dbPromise.then(
            (db) => { db.close().catch(() => {}); },
            () => { /* createDatabase itself failed, nothing to close */ },
          );
        } else {
          try { await dbInstance.close(); } catch { /* ignore close errors */ }
        }
      }
    }
  } catch (err) {
    console.error(`[clawmind:mcp:init] Warning: DB bindings unavailable: ${err instanceof Error ? err.message : String(err)}`);
  }

  // 3. Build facade registry
  const packs = catalog?.packs ?? [];
  const registry = buildFacadeRegistry(packs, dbBindings);
  console.error(`[clawmind:mcp:init] Registry: ${registry.commands().join(", ") || "(no facades)"}`);

  // 4. Generate command/skill files
  // Two modes:
  //   A) No-plugin mode (--commands-dir): write commands/*.md directly to a
  //      specified directory (e.g. .claude/commands/). Used when ClawMind is
  //      registered as MCP via mcporter.json without the Claude Code Plugin system.
  //   B) Plugin mode (--plugin-root / CLAUDE_PLUGIN_ROOT): write to
  //      pluginRoot/commands/ and pluginRoot/skills/. Used when installed as a
  //      Claude Code plugin via `claude plugin install`.
  const commandsDirIdx = args.indexOf("--commands-dir");
  const cliCommandsDir = commandsDirIdx !== -1 && args[commandsDirIdx + 1]
    ? args[commandsDirIdx + 1]
    : undefined;

  const pluginRootIdx = args.indexOf("--plugin-root");
  const cliPluginRoot = pluginRootIdx !== -1 && args[pluginRootIdx + 1]
    ? args[pluginRootIdx + 1]
    : undefined;
  const pluginRoot =
    cliPluginRoot ||
    process.env.CLAUDE_PLUGIN_ROOT ||
    resolvePluginRootFromScriptPath();

  if (cliCommandsDir) {
    // No-plugin mode: write commands directly to the specified directory
    try {
      const result = generateFacadeCommands(registry, pluginRoot ?? cliCommandsDir, {
        commandsDirOverride: cliCommandsDir,
      });
      console.error(
        `[clawmind:mcp:init] Generated ${result.commandsGenerated} commands` +
        (result.cleaned > 0 ? `, cleaned ${result.cleaned} stale files` : "") +
        ` → ${cliCommandsDir} (no-plugin mode)`,
      );
      if (result.commandNames.length > 0) {
        console.error(`[clawmind:mcp:init] Commands: ${result.commandNames.join(", ")}`);
      }
    } catch (err) {
      console.error(`[clawmind:mcp:init] Warning: Failed to generate command files: ${err instanceof Error ? err.message : String(err)}`);
      console.error("[clawmind:mcp:init] Falling back to context-only mode (no command files written)");
    }
  } else if (pluginRoot) {
    // Plugin mode: write to pluginRoot/commands/ and pluginRoot/skills/
    try {
      const result = generateFacadeCommands(registry, pluginRoot);
      console.error(
        `[clawmind:mcp:init] Generated ${result.commandsGenerated} commands, ${result.skillsGenerated} skills` +
        (result.cleaned > 0 ? `, cleaned ${result.cleaned} stale files` : "") +
        ` → ${pluginRoot}`,
      );
      if (result.commandNames.length > 0) {
        console.error(`[clawmind:mcp:init] Commands: ${result.commandNames.join(", ")}`);
      }
    } catch (err) {
      console.error(`[clawmind:mcp:init] Warning: Failed to generate command files: ${err instanceof Error ? err.message : String(err)}`);
      console.error("[clawmind:mcp:init] Falling back to context-only mode (no command files written)");
    }
  } else {
    console.error("[clawmind:mcp:init] CLAUDE_PLUGIN_ROOT not set — skipping command file generation");
    console.error("[clawmind:mcp:init] Facades will be available via context injection and MCP dispatch only");
  }

  // 5. Facade summary — log to stderr (for developer diagnostics only).
  //    Do NOT output facade details to stdout, because Claude Code captures
  //    stdout as <system-reminder> context and the LLM would treat the
  //    ClawMind facade list as the definitive MCP capability description,
  //    preventing it from consulting mcporter.json to discover other MCP servers.
  //    Facade availability is already registered via command/skill files (step 4).
  const summary = formatFacadeSummary(registry);
  console.error(summary);

  // 6. Output a minimal hint to stdout — Claude Code captures this as
  //    <system-reminder> context to guide the LLM toward mcporter for MCP access.
  //    mcporter is registered as an MCP stdio gateway in settings.json,
  //    so all backend MCP tools are accessible as mcp__mcporter__*.
  console.log(`[taskguard] MCP tools accessible via mcporter gateway (mcp__mcporter__*). Rules: (1) All MCP calls must go through mcp__mcporter__* tools; (2) Use mcp__mcporter__list to discover available servers and tools; (3) Always list first before calling.`);

  console.error("[taskguard:mcp:init] Done.");
}

// ── Run ──

const args = process.argv.slice(2);
if (args.includes("--init")) {
  handleInitMode().catch((err) => {
    console.error("[clawmind:mcp:init] Fatal error:", err);
    process.exit(1);
  });
} else {
  main().catch((err) => {
    console.error("[clawmind:mcp] Fatal error:", err);
    process.exit(1);
  });
}
