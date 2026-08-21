/**
 * Hermes MCP Server Entry Point — SSE transport variant.
 *
 * Starts an MCP server with SSE transport for Hermes platform integration.
 * Reuses the same workflow tool registrations as mcp-entry.ts but:
 * - Uses SSEServerTransport (HTTP + Server-Sent Events)
 * - Creates HermesAdapter with SSE/approval/multi-tenant support
 * - Extracts tenantId/teamId from request metadata
 *
 * Usage:
 *   node dist/esm/platform/hermes-entry.js --port 3100
 *
 * Note: SSEServerTransport is deprecated in favor of StreamableHTTPServerTransport
 * in @modelcontextprotocol/sdk v1.x. When upgrading, replace the SSE setup
 * with StreamableHTTPServerTransport following the SDK migration guide.
 *
 * @module platform/hermes-entry
 */

import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import express from "express";
import { createHermesAdapter } from "./hermes-adapter.js";
import {
  registerWorkflowTools,
  type AdapterFactory,
  type AdapterContext,
  type HermesContext,
  type WorkflowToolDeps,
} from "./mcp-tools.js";
import { createMcpServerBase, VERSION } from "./mcp-server-factory.js";

// ── CLI argument parsing ──

function parseArgs(argv: string[]): { port: number } {
  const portArg = argv.find((a) => a.startsWith("--port="));
  if (portArg) {
    return { port: parseInt(portArg.split("=")[1], 10) };
  }
  const portIdx = argv.indexOf("--port");
  if (portIdx !== -1 && portIdx + 1 < argv.length) {
    return { port: parseInt(argv[portIdx + 1], 10) };
  }
  return { port: 3100 };
}

/**
 * Create and configure the Hermes MCP server with SSE transport.
 *
 * This function:
 * 1. Creates shared MCP infrastructure via createMcpServerBase()
 * 2. Sets up the AdapterFactory using createHermesAdapter
 * 3. Registers all workflow tools via registerWorkflowTools
 * 4. Returns the server, deps, and a connect helper
 *
 * The caller is responsible for binding the server to an HTTP transport
 * (SSE or StreamableHTTP) and starting the listener.
 */
export async function createHermesServer(deps?: Partial<WorkflowToolDeps>) {
  // ── Shared initialization (API client, packs, action registry, sampling) ──
  const { server, toolDeps } = await createMcpServerBase({
    name: "clawmind-hermes",
    logPrefix: "[clawmind:hermes]",
    systemPromptPrefix: "You are a workflow step executor in the ClawMind workflow engine (Hermes SSE mode).",
    platformType: "hermes",
    actionRegistry: deps?.actionRegistry,
    workflowCatalog: deps?.workflowCatalog,
    flowRunApiRepo: deps?.flowRunApiRepo,
    samplingAgent: deps?.samplingAgent,
  });

  // ── Adapter factory for Hermes ──
  // Each tool invocation creates a fresh HermesAdapter with per-request context.
  // tenantId/teamId are extracted from HermesContext (properly typed, no `as` casts).
  const adapterFactory: AdapterFactory<HermesContext> = (context) => {
    const { tenantId, teamId, sseSend, approvalRequest } = context;

    const adapter = createHermesAdapter({
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
      tenantId,
      teamId,
      sseSend,
      approvalRequest,
    });

    return { adapter };
  };

  // ── Register shared workflow tools ──
  registerWorkflowTools(server, adapterFactory, {
    actionRegistry: toolDeps.actionRegistry,
    workflowCatalog: toolDeps.workflowCatalog,
    flowRunApiRepo: toolDeps.flowRunApiRepo,
    samplingAgent: toolDeps.samplingAgent,
    facadeRegistry: toolDeps.facadeRegistry,
    db: toolDeps.db,
    workflowSpecApiRepo: toolDeps.workflowSpecApiRepo,
  }, {
    logPrefix: "[clawmind:hermes]",
  });

  return { server, toolDeps };
}

/**
 * Create an adapter context with Hermes-specific metadata.
 *
 * This helper injects the SSE/approval/tenant info into the adapter context
 * so the adapterFactory can create a properly configured HermesAdapter.
 * Returns a HermesContext with all fields properly typed.
 */
export function createHermesAdapterContext(base: AdapterContext, options?: {
  tenantId?: string;
  teamId?: string;
  sseSend?: (event: string, data: unknown) => void;
  approvalRequest?: (flowId: string, nodeId: string, message: string) => Promise<{ approved: boolean; note?: string }>;
}): HermesContext {
  return {
    ...base,
    tenantId: options?.tenantId,
    teamId: options?.teamId,
    sseSend: options?.sseSend,
    approvalRequest: options?.approvalRequest,
  };
}

// ── Main (SSE server) ──

async function main() {
  const { port } = parseArgs(process.argv);
  console.error(`[clawmind:hermes] Starting Hermes MCP Server v${VERSION} on port ${port}`);

  const { server } = await createHermesServer();

  // ── Express + SSE transport ──
  const app = express();

  // Health check endpoint
  app.get("/health", (_req, res) => {
    res.json({ status: "ok", service: "clawmind-hermes", version: VERSION });
  });

  // SSE endpoint — client connects here to receive events
  const sseConnections = new Map<string, SSEServerTransport>();
  app.get("/sse", (req, res) => {
    console.error(`[clawmind:hermes] New SSE connection from ${req.ip}`);
    const transport = new SSEServerTransport("/messages", res);
    sseConnections.set(transport.sessionId, transport);

    transport.onclose = () => {
      sseConnections.delete(transport.sessionId);
      console.error(`[clawmind:hermes] SSE connection closed: ${transport.sessionId}`);
    };

    server.connect(transport).catch((err) => {
      console.error("[clawmind:hermes] Error connecting SSE transport:", err);
    });
  });

  // Message endpoint — client POSTs messages here
  app.post("/messages", express.raw({ type: "*/*" }), (req, res) => {
    // Find the transport by session ID from the query string
    const sessionId = req.query.sessionId as string;
    const transport = sessionId ? sseConnections.get(sessionId) : undefined;
    if (!transport) {
      res.status(400).json({ error: "Unknown or missing sessionId" });
      return;
    }
    transport.handlePostMessage(req, res).catch((err: unknown) => {
      console.error("[clawmind:hermes] Error handling POST message:", err);
    });
  });

  // Start HTTP server
  app.listen(port, () => {
    console.error(`[clawmind:hermes] HTTP server listening on http://127.0.0.1:${port}`);
    console.error(`[clawmind:hermes] SSE endpoint: http://127.0.0.1:${port}/sse`);
    console.error(`[clawmind:hermes] Messages endpoint: http://127.0.0.1:${port}/messages`);
  });

  // Graceful shutdown
  process.on("SIGINT", () => {
    console.error("[clawmind:hermes] Shutting down...");
    process.exit(0);
  });
}

// ── Run ──
main().catch((err) => {
  console.error("[clawmind:hermes] Fatal error:", err);
  process.exit(1);
});