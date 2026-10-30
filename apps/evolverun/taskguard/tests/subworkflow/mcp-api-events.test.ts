import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createServer } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createMcpServerBase } from "../../src/platform/mcp-server-factory.js";
import { executeSubworkflowChildForTest, setEventRepository, setFlowRunRepository, setNodeExecutionRepository, setRunLogUploader, type ControllerDeps } from "../../src/controller.js";
import { clearCachedConfig, getCachedConfig, setCachedConfig } from "../../src/config/config-cache.js";
import { loadConfig } from "../../src/config/loader.js";
import type { FlowState, WorkflowSpec } from "../../src/types.js";

const originalHome = process.env.HOME;
const home = mkdtempSync(join(tmpdir(), "taskguard-mcp-events-"));
process.env.HOME = home;
after(async () => {
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (originalHome === undefined) delete process.env.HOME;
  else process.env.HOME = originalHome;
  rmSync(home, { recursive: true, force: true });
});

test("MCP API bootstrap posts linked and finished events using the API repository", { timeout: 10000 }, async () => {
  const posts: Record<string, unknown>[] = [];
  const http = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (req.method === "POST" && req.url === "/api/internal/events") {
        posts.push(JSON.parse(Buffer.concat(chunks).toString()));
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ success: true, data: [] }));
    });
  });
  await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  assert.ok(address && typeof address !== "string");
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const oldConfig = getCachedConfig();
  const config = loadConfig();
  setCachedConfig({ ...config, app: { ...config.app, api: { ...config.app.api, baseUrl, clawwebUrl: "", privateKeyB64: "", iamtoken: "" } } });
  const envKeys = ["CLAWWEB_API_URL", "CLAWWEB_API_PRIVATE_KEY", "CLAWMIND_PRIVATE_KEY", "CLAWMIND_IAMTOKEN", "CLAWWEB_URL"] as const;
  const oldEnv = Object.fromEntries(envKeys.map((key) => [key, process.env[key]]));
  for (const key of envKeys) delete process.env[key];
  process.env.CLAWWEB_API_URL = baseUrl;
  let base: Awaited<ReturnType<typeof createMcpServerBase>> | undefined;
  try {
    setEventRepository(null);
    base = await createMcpServerBase({
      name: "test", logPrefix: "[test]", systemPromptPrefix: "test",
      workflowCatalog: { packs: [], workflows: [], failedWorkflows: [] },
      samplingAgent: async () => ({ text: "" } as never),
      agentRunner: async () => ({ text: "" } as never),
    });
    const parent = {
      workflowId: "parent", workflowVersion: "1", params: {}, executionMode: "auto",
      businessStatus: "INIT", currentPhase: "P1", activeNodes: [], nodeStates: {},
      workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [],
    } as FlowState;
    const child = { id: "child", title: "Child", version: "1", nodes: [] } as unknown as WorkflowSpec;
    const deps = { boundTaskFlow: { createManaged: async () => ({ flowId: "child-flow" }) } } as unknown as ControllerDeps;
    // Stop execution after creation; the existing crash path still emits the terminal event.
    Object.defineProperty(deps, "chatInject", { get() { throw new Error("child crash"); } });
    const result = await executeSubworkflowChildForTest(deps, child, { privateParam: "not-in-link" }, parent, "parent-node", "parent-flow", 1);
    assert.equal(result.status, "failed");
    const deadline = Date.now() + 3000;
    while ((!posts.some((p) => p.event_type === "subworkflow_linked") || !posts.some((p) => p.event_type === "subworkflow_finished")) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    const linked = posts.find((p) => p.event_type === "subworkflow_linked");
    const finished = posts.find((p) => p.event_type === "subworkflow_finished");
    assert.ok(linked, "linked event was not posted to the events API");
    assert.ok(finished, "finished event was not posted to the events API");
    assert.equal(linked.flow_id, "parent-flow");
    assert.equal(linked.workflow_id, "parent");
    assert.equal(linked.node_id, "parent-node");
    assert.deepEqual(JSON.parse(String(linked.data_json)), { childFlowId: "child-flow", childWorkflowId: "child", depth: 1 });
    assert.equal(JSON.stringify(linked).includes("not-in-link"), false);
    assert.equal(JSON.parse(String(finished.data_json)).status, "failed");
  } finally {
    (base?.toolDeps.runLogUploader as { shutdown?: () => void } | undefined)?.shutdown?.();
    setRunLogUploader(null);
    setNodeExecutionRepository(null);
    setFlowRunRepository(null);
    setEventRepository(null);
    await base?.server.close();
    for (const key of envKeys) {
      if (oldEnv[key] === undefined) delete process.env[key];
      else process.env[key] = oldEnv[key];
    }
    if (oldConfig) setCachedConfig(oldConfig);
    else clearCachedConfig();
    http.closeAllConnections();
    await new Promise<void>((resolve) => http.close(() => resolve()));
  }
});
