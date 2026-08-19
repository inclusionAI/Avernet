#!/usr/bin/env node
/**
 * Quick debug script to query flow data via clawweb API.
 * Usage: node scripts/query-flow-debug.mjs <flowId>
 */
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Load config
const configPath = join(__dirname, "..", "configs", "application.yaml");
const configText = readFileSync(configPath, "utf-8");
const config = parseYaml(configText);

const BASE_URL = config.api?.baseUrl || "https://clawweb.antgroup-inc.cn";
const IAM_TOKEN = process.env.IAM_TOKEN || config.api?.iamtoken || "";
const PRIVATE_KEY_B64 = config.api?.privateKeyB64 || "";

if (!IAM_TOKEN) {
  console.error("No iamtoken found in config");
  process.exit(1);
}

// Load Ed25519 private key
const privateKey = crypto.createPrivateKey({
  key: Buffer.from(PRIVATE_KEY_B64, "base64"),
  type: "pkcs8",
  format: "der",
});

function sign(body) {
  const timestamp = Date.now().toString();
  const message = `${timestamp}.${body}`;
  const signature = crypto.sign(null, Buffer.from(message), privateKey);
  return {
    signature: signature.toString("base64"),
    timestamp,
  };
}

async function apiGet(path) {
  const url = `${BASE_URL}/api/internal${path}`;
  const { signature, timestamp } = sign("");
  const headers = {
    "Content-Type": "application/json",
    "X-Signature": signature,
    "X-Timestamp": timestamp,
    "Cookie": `iam_token=${IAM_TOKEN}`,
  };

  const response = await fetch(url, { method: "GET", headers });
  if (!response.ok) {
    const text = await response.text();
    console.error(`API GET ${path} failed: ${response.status} ${text.substring(0, 500)}`);
    return null;
  }
  const raw = await response.json();
  // clawweb wraps responses in { success: true, data: ... }
  if (raw && typeof raw === "object" && raw.success === true && "data" in raw) {
    return raw.data;
  }
  return raw;
}

async function main() {
  const flowId = process.argv[2] || "e12bbc17-269c-45e4-a623-c5130922af8f";

  console.log(`\n=== Querying flow: ${flowId} ===\n`);

  // 1. Query node_executions
  console.log("--- Node Executions ---");
  const nodeExecs = await apiGet(`/node-executions?flowId=${encodeURIComponent(flowId)}&limit=50`);
  if (nodeExecs && Array.isArray(nodeExecs)) {
    for (const row of nodeExecs) {
      console.log(`  id=${row.id} node=${row.node_id} executor=${row.executor_type} status=${row.status} attempt=${row.attempt} started=${row.started_at} completed=${row.completed_at} error=${(row.error_text || "").substring(0, 80)}`);
    }
  } else {
    console.log("  No data or error:", JSON.stringify(nodeExecs)?.substring(0, 300));
  }

  // 2. Query flow_events
  console.log("\n--- Flow Events ---");
  const events = await apiGet(`/events?flowId=${encodeURIComponent(flowId)}&limit=100`);
  if (events && Array.isArray(events)) {
    for (const row of events) {
      const dataPreview = row.data_json ? JSON.stringify(JSON.parse(row.data_json)).substring(0, 120) : "";
      console.log(`  id=${row.id} type=${row.event_type} node=${row.node_id || "-"} attempt=${row.attempt || "-"} time=${row.time} error=${(row.error_text || "").substring(0, 60)} data=${dataPreview}`);
    }
  } else {
    console.log("  No data or error:", JSON.stringify(events)?.substring(0, 300));
  }

  // 3. Query flow_runs (for stateJson)
  console.log("\n--- Flow Run (stateJson) ---");
  const flowRun = await apiGet(`/runs/${encodeURIComponent(flowId)}`);
  if (flowRun) {
    const flow = Array.isArray(flowRun) ? flowRun[0] : flowRun;
    console.log("  Flow status:", flow.status);
    console.log("  Workflow:", flow.workflow_title, flow.workflow_id);
    
    // Extract stateJson from params_json._clawmind_state
    let state = null;
    if (flow.params_json) {
      try {
        const params = JSON.parse(flow.params_json);
        if (params._clawmind_state) {
          const stateStr = typeof params._clawmind_state === "string" ? params._clawmind_state : JSON.stringify(params._clawmind_state);
          state = JSON.parse(stateStr);
        }
      } catch (e) {
        console.log("  Failed to parse params_json:", e.message);
      }
    }
    
    if (!state && flow.result_json) {
      console.log("  result_json (first 200 chars):", flow.result_json.substring(0, 200));
    }
    
    if (state) {
      console.log("  Current phase:", state.currentPhase);
      console.log("  Node states:");
      if (state.nodeStates) {
        for (const [nodeId, nodeState] of Object.entries(state.nodeStates)) {
          const ns = nodeState;
          console.log(`    ${nodeId}: status=${ns.status} attempts=${ns.attempts} manualRetries=${ns.manualRetries ?? 0} error=${(ns.error || "").substring(0, 80)} completedAt=${ns.completedAt}`);
        }
      }
      console.log("  Audit log (last 30):");
      if (state.auditLog) {
        const entries = state.auditLog.slice(-30);
        for (const entry of entries) {
          console.log(`    [${entry.phase || ""}] ${entry.nodeId || "-"} ${entry.action} ${(entry.detail || "").substring(0, 120)}`);
        }
      }
    } else {
      console.log("  No _clawmind_state found in params_json");
      // Try printing raw params_json for inspection
      if (flow.params_json) {
        console.log("  params_json (first 500 chars):", flow.params_json.substring(0, 500));
      }
    }
    
    // Also print result_json for flow failure reason
    if (flow.result_json) {
      try {
        const result = JSON.parse(flow.result_json);
        console.log("\n  result_json:");
        console.log("    nodeId:", result.nodeId);
        console.log("    error:", (result.error || "").substring(0, 200));
        if (result.report) console.log("    report (first 200 chars):", result.report.substring(0, 200));
      } catch (e) {
        console.log("  result_json (first 200):", flow.result_json.substring(0, 200));
      }
    }
  } else {
    console.log("  No flow run data");
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});