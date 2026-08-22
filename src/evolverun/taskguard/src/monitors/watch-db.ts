#!/usr/bin/env node
/**
 * Workflow State Change Monitor — watches for flow state changes and notifies.
 *
 * Polls the ClawMind SQLite database for changes to flow_runs and
 * node_executions tables. Tracks last-seen timestamps to detect new events
 * and outputs formatted notifications to stdout (one per line).
 *
 * Output format:
 *   [taskguard] 工作流 "{name}" ({flowId}) 状态: {status}
 *   [taskguard] 工作流 "{name}" ({flowId}) 节点 "{node}" {status}
 *
 * Intended to be run as a Plugin Monitor via `monitors/monitors.json`.
 *
 * @module monitors/watch-db
 */

import { createDatabase } from "../db/factory.js";
import type { IDatabase } from "../db/types.js";

// ── Types ──

interface FlowRunRow {
  flow_id: string;
  workflow_id: string;
  workflow_title: string;
  status: string;
  current_phase: string;
  gmt_modified: number;
}

interface NodeExecutionRow {
  flow_id: string;
  node_id: string;
  status: string;
  gmt_modified: number;
}

// ── State ──

let lastFlowTimestamp = 0;
let lastNodeTimestamp = 0;
const seenFlowStatuses = new Map<string, string>();
const seenNodeStatuses = new Map<string, string>();

// ── Polling ──

async function poll(db: IDatabase): Promise<void> {
  // Check flow_runs for status changes
  try {
    const flows = await db.query<FlowRunRow>(
      `SELECT flow_id, workflow_id, workflow_title, status, current_phase, gmt_modified
       FROM flow_runs
       WHERE gmt_modified > ?
       ORDER BY gmt_modified ASC`,
      [lastFlowTimestamp],
    );

    for (const flow of flows) {
      const prevStatus = seenFlowStatuses.get(flow.flow_id);
      if (prevStatus !== flow.status) {
        const name = flow.workflow_title || flow.workflow_id || "未命名";
        const statusText = translateStatus(flow.status);
        // eslint-disable-next-line no-console
        console.log(`[taskguard] 工作流 "${name}" (${flow.flow_id}) 状态: ${statusText}`);
        seenFlowStatuses.set(flow.flow_id, flow.status);
      }
      if (flow.gmt_modified > lastFlowTimestamp) {
        lastFlowTimestamp = flow.gmt_modified;
      }
    }
  } catch {
    // Table may not exist yet — silent
  }

  // Check node_executions for status changes
  try {
    const nodes = await db.query<NodeExecutionRow>(
      `SELECT flow_id, node_id, status, gmt_modified
       FROM node_executions
       WHERE gmt_modified > ?
       ORDER BY gmt_modified ASC`,
      [lastNodeTimestamp],
    );

    for (const node of nodes) {
      const key = `${node.flow_id}:${node.node_id}`;
      const prevStatus = seenNodeStatuses.get(key);
      if (prevStatus !== node.status) {
        const nodeId = node.node_id || "未知节点";
        const statusText = translateStatus(node.status);
        // eslint-disable-next-line no-console
        console.log(`[taskguard] 工作流运行 ${node.flow_id} 节点 "${nodeId}" ${statusText}`);
        seenNodeStatuses.set(key, node.status);
      }
      if (node.gmt_modified > lastNodeTimestamp) {
        lastNodeTimestamp = node.gmt_modified;
      }
    }
  } catch {
    // Table may not exist yet — silent
  }
}

function translateStatus(status: string): string {
  const map: Record<string, string> = {
    running: "运行中",
    waiting: "等待中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    paused: "已暂停",
    skipped: "已跳过",
    pending: "待执行",
  };
  return map[status] ?? status;
}

// ── Main Loop ──

async function main(): Promise<void> {
  const sqlitePath = process.env.SQLITE_PATH ?? "~/.openclaw/workflow/engine.db";
  const expandedPath = sqlitePath.replace(/^~/, process.env.HOME ?? "/tmp");
  const intervalMs = parseInt(process.env.CLAWMIND_MONITOR_INTERVAL ?? "5000", 10);

  let db: IDatabase | undefined;
  try {
    db = await createDatabase({ mode: "sqlite", sqlitePath: expandedPath });

    // Initialize last-seen timestamps to now so we only see new events
    const now = Math.floor(Date.now() / 1000);
    lastFlowTimestamp = now;
    lastNodeTimestamp = now;

    // eslint-disable-next-line no-console
    console.log(`[taskguard] 监控已启动 (间隔: ${intervalMs}ms, 数据库: ${expandedPath})`);

    // Poll loop
    while (true) {
      await poll(db);
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`[clawmind:monitor] Error: ${msg}\n`);
  } finally {
    await db?.close?.();
  }
}

main().catch(() => {
  process.exit(1);
});