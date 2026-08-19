/**
 * Query API server test script.
 *
 * Starts the API server and runs basic health checks against
 * each endpoint. Useful for verifying API connectivity and
 * authentication without a full integration test suite.
 *
 * Usage:
 *   WORKFLOW_API_KEY=test-key node --import tsx scripts/test-api-server.ts
 *
 * The server will start on port 3210 (or WORKFLOW_API_PORT env var).
 * Press Ctrl+C to stop.
 */
import { createApp, startApiServer } from "../src/api/server.js";
import type { ApiRepositories } from "../src/api/server.js";
import { createDatabase } from "../src/db/factory.js";
import { FlowRunRepository } from "../src/db/repositories/flow-run-repository.js";
import { FlowEventRepository } from "../src/db/repositories/event-repository.js";
import { NodeExecutionRepository } from "../src/db/repositories/node-execution-repository.js";
import { FlowMetricsRepository } from "../src/db/repositories/metrics-repository.js";
import { TriggeredAlertRepository } from "../src/db/repositories/alert-repository.js";
import type { ApiConfig } from "../src/config/types.js";

async function main(): Promise<void> {
  console.log("\n=== Query API Server Test ===\n");

  // Create database
  const db = await createDatabase({ fallbackOnFailure: true });
  console.log(`Database: type=${db.dbType}`);

  const repos: ApiRepositories = {
    flowRunRepository: db.dbType !== "noop" ? new FlowRunRepository(db) : null,
    eventRepository: db.dbType !== "noop" ? new FlowEventRepository(db) : null,
    nodeExecutionRepository: db.dbType !== "noop" ? new NodeExecutionRepository(db) : null,
    metricsRepository: db.dbType !== "noop" ? new FlowMetricsRepository(db) : null,
    alertRepository: db.dbType !== "noop" ? new TriggeredAlertRepository(db) : null,
    facadeBindingRepository: null,
  };

  const port = parseInt(process.env.WORKFLOW_API_PORT ?? "3210", 10);
  const apiKey = process.env.WORKFLOW_API_KEY ?? "test-key";

  const config: ApiConfig = {
    enabled: true,
    port,
    host: "127.0.0.1",
    apiKey,
    baseUrl: "http://localhost:3001",
    privateKeyB64: "",
    iamtoken: "",
    timeout: 5000,
    maxRetries: 3,
    clawwebUrl: "http://localhost:3001",
    corpId: "",
  };

  // Start server
  const server = startApiServer(config, repos);
  if (!server) {
    console.error("✗ Failed to start API server (config.enabled is false)");
    process.exit(1);
  }

  // Wait for server to be ready
  await new Promise<void>((resolve) => server.on("listening", resolve));
  console.log(`✓ API server listening on http://127.0.0.1:${port}`);

  // Run endpoint checks
  const baseUrl = `http://127.0.0.1:${port}`;
  const headers: Record<string, string> = { "X-API-Key": apiKey };

  type CheckResult = { endpoint: string; status: number; ok: boolean };
  const checks: CheckResult[] = [];

  // Health check (no auth required)
  {
    const res = await fetch(`${baseUrl}/health`);
    checks.push({ endpoint: "GET /health", status: res.status, ok: res.status === 200 });
  }

  // Auth rejection check (no key)
  {
    const res = await fetch(`${baseUrl}/api/flows`);
    checks.push({ endpoint: "GET /api/flows (no auth)", status: res.status, ok: res.status === 401 });
  }

  // Authenticated endpoints
  const endpoints: [string, string][] = [
    ["GET /api/flows", `${baseUrl}/api/flows`],
    ["GET /api/metrics?workflowId=test-wf", `${baseUrl}/api/metrics?workflowId=test-wf`],
    ["GET /api/alerts?workflowId=test-wf", `${baseUrl}/api/alerts?workflowId=test-wf`],
  ];

  for (const [name, url] of endpoints) {
    const res = await fetch(url, { headers });
    checks.push({ endpoint: name, status: res.status, ok: res.status === 200 || res.status === 503 });
  }

  // Report results
  console.log("\nEndpoint checks:");
  for (const c of checks) {
    const icon = c.ok ? "✓" : "✗";
    console.log(`  ${icon} ${c.endpoint} → ${c.status}`);
  }

  const allOk = checks.every((c) => c.ok);
  console.log(allOk ? "\n✓ All checks passed." : "\n✗ Some checks failed.");

  // Graceful shutdown
  server.close();
  await db.close();
  console.log("Server stopped.");
  process.exit(allOk ? 0 : 1);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});