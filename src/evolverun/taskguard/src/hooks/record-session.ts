#!/usr/bin/env node
/**
 * Record Session Hook — captures Claude Code session_id for flow run traceability.
 *
 * Triggered by the Stop hook when a Claude Code session ends.
 * Reads the session transcript and extracts the session_id so that
 * flow_runs.origin_session_id can be updated for traceability.
 *
 * This is a backup mechanism — origin_session_id is also passed directly
 * through ControllerDeps when the flow is created. This hook handles the
 * case where the session ID becomes available only after the session ends.
 *
 * Input (stdin JSON from Claude Code Stop hook):
 * {
 *   "session_id": "...",
 *   "transcript_path": "/path/to/session.jsonl",
 *   "stop_reason": "user_exit" | "task_complete" | "error"
 * }
 *
 * @module hooks/record-session
 */

interface StopHookInput {
  session_id?: string;
  transcript_path?: string;
  stop_reason?: string;
}

async function main(): Promise<void> {
  // Read input from stdin
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  const rawInput = Buffer.concat(chunks).toString("utf-8");

  let input: StopHookInput;
  try {
    input = JSON.parse(rawInput) as StopHookInput;
  } catch {
    return;
  }

  const sessionId = input.session_id;
  if (!sessionId) {
    return;
  }

  // The session_id is now known. We could update flow_runs that were
  // created in this session but don't have origin_session_id set yet.
  // For now, just log it for observability — the Controller already
  // sets origin_session_id at flow creation time via ControllerDeps.
  const sqlitePath = process.env.SQLITE_PATH ?? "~/.openclaw/workflow/engine.db";
  const expandedPath = sqlitePath.replace(/^~/, process.env.HOME ?? "/tmp");

  try {
    const { createDatabase } = await import("../db/factory.js");
    const db = await createDatabase({ mode: "sqlite", sqlitePath: expandedPath });

    // Update any flow runs that originated from this session but don't have
    // origin_session_id set yet (race condition recovery)
    await db.exec(
      `UPDATE flow_runs
       SET origin_session_id = ?
       WHERE origin_session_key = (
         SELECT origin_session_key FROM flow_runs WHERE origin_session_id IS NULL LIMIT 1
       )
       AND origin_session_id IS NULL`,
      [sessionId],
    );

    await db.close?.();
  } catch {
    // Best-effort — never fail the stop hook
  }
}

main().catch(() => {
  // Silent exit — hooks must not crash
});