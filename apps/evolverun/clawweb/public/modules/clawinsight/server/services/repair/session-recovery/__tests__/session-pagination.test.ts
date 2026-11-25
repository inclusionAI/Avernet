import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { RecoverySessionReader } from "../session-reader.js";

it.each(["openclaw", "claude_code"] as const)("keeps an unread user message at the byte boundary for %s", async activeEngine => {
  const root = await mkdtemp(join(tmpdir(), "recovery-pagination-"));
  try {
    const key = "agent:main:session:original:user:owner";
    const directory = join(root, activeEngine === "openclaw" ? "oc/agents/main/sessions" : "cc/projects/project");
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, "sessions.json"), JSON.stringify({ [key]: { sessionId: "original" } }));
    const rows = Array.from({ length: 25 }, (_, i) => ({ type: "message", id: `message-${i + 1}`,
      message: { role: i === 20 ? "user" : "assistant", content: "x".repeat(3850) } }));
    await writeFile(join(directory, "original.jsonl"), rows.map(row => JSON.stringify(row)).join("\n") + "\n");
    const reader = new RecoverySessionReader({ executeShell: async input => {
      const output = await promisify(execFile)("/bin/bash", ["-c", input.command], { env: { ...process.env,
        OPENCLAW_STATE_DIR: join(root, "oc"), CLAUDE_CONFIG_DIR: join(root, "cc") }, maxBuffer: 1024 * 1024 });
      return { status: "success", exitCode: 0, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
    } });
    const target = { environment: "prod" as const, ownerId: "owner", botId: "bot", provider: "arca", deviceId: "fixture", bindingId: "fixture", activeEngine };
    const first = await reader.read({ target, sessionKey: key, deliveryKey: "fixture", fromLine: 1, limit: 100 });
    expect(first.messages).toHaveLength(20);
    expect(first.next_line).toBe(21);
    const next = await reader.read({ target, sessionKey: key, deliveryKey: "fixture", fromLine: first.next_line as number, limit: 100 });
    expect(next.messages).toEqual(expect.arrayContaining([expect.objectContaining({ line_no: 21, record: expect.objectContaining({ message: expect.objectContaining({ role: "user" }) }) })]));
  } finally { await rm(root, { recursive: true, force: true }); }
});
