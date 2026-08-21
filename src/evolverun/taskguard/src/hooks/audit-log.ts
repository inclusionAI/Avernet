#!/usr/bin/env node
/**
 * Audit Log Hook — logs ClawMind workflow tool calls for traceability.
 *
 * Triggered by PostToolUse hook when the tool name matches `mcp__clawmind__*`.
 * Reads the tool call context from stdin (JSON) and writes a structured audit
 * record to the ClawMind SQLite database.
 *
 * Input (stdin JSON from Claude Code hooks):
 * {
 *   "tool_name": "mcp__clawmind__workflow_engine_dispatch",
 *   "tool_input": { "action": "run", "workflowId": "..." },
 *   "tool_result": "...",
 *   "session_id": "...",
 *   "timestamp": "2026-06-30T12:00:00.000Z"
 * }
 *
 * @module hooks/audit-log
 */

import { createDatabase } from "../db/factory.js";
import type { IDatabase } from "../db/types.js";

interface AuditLogInput {
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_result?: string;
  session_id?: string;
  timestamp?: string;
}

async function main(): Promise<void> {
  // Read input from stdin
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  const rawInput = Buffer.concat(chunks).toString("utf-8");

  let input: AuditLogInput;
  try {
    input = JSON.parse(rawInput) as AuditLogInput;
  } catch {
    // Not JSON — nothing to audit
    return;
  }

  // Only audit ClawMind MCP tool calls
  if (!input.tool_name?.startsWith("mcp__clawmind__")) {
    return;
  }

  // Connect to SQLite and write audit record
  let db: IDatabase | undefined;
  try {
    const sqlitePath = process.env.SQLITE_PATH ?? "~/.openclaw/workflow/engine.db";
    const expandedPath = sqlitePath.replace(/^~/, process.env.HOME ?? "/tmp");
    db = await createDatabase({ mode: "sqlite", sqlitePath: expandedPath });

    // Ensure audit table exists
    await db.exec(`
      CREATE TABLE IF NOT EXISTS tool_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT NOT NULL,
        tool_input TEXT,
        tool_result_preview TEXT,
        session_id TEXT,
        timestamp TEXT NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
      )
    `);

    // Insert audit record
    const toolInputJson = input.tool_input ? JSON.stringify(input.tool_input) : null;
    const resultPreview = typeof input.tool_result === "string"
      ? input.tool_result.slice(0, 500)
      : null;
    const timestamp = input.timestamp ?? new Date().toISOString();

    await db.exec(
      `INSERT INTO tool_audit_log (tool_name, tool_input, tool_result_preview, session_id, timestamp)
       VALUES (?, ?, ?, ?, ?)`,
      [input.tool_name, toolInputJson, resultPreview, input.session_id ?? null, timestamp],
    );
  } catch (err) {
    // Audit logging is best-effort — never fail the hook
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`[clawmind:audit-log] Warning: ${msg.slice(0, 200)}\n`);
  } finally {
    await db?.close?.();
  }
}

main().catch(() => {
  // Silent exit — hooks must not crash
});