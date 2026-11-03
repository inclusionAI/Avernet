import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, readFile, rm, realpath } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, it, vi } from "vitest";
import { RecoverySessionReader } from "../session-reader.js";

it("reads the exact CC SDK file through Shell without using a session API or changing the file", async () => {
  const home = await mkdtemp(join(tmpdir(), "recovery-cc-read-"));
  try {
    const project = join(home, ".claude/projects/project");
    await mkdir(project, { recursive: true });
    const file = join(project, "original.jsonl");
    const contents = [
      { type: "assistant", uuid: "quote", message: { role: "assistant", content: "[会话自愈：send]\n引用" } },
      { type: "user", uuid: "actual", message: { role: "user", content: "[会话自愈：send]\n继续" } },
      { type: "assistant", uuid: "reply", message: { role: "assistant", content: "已恢复原动作" } },
    ].map(record => JSON.stringify(record)).join("\n") + "\n";
    await writeFile(file, contents);
    const request = vi.fn();
    const reader = new RecoverySessionReader({ request, executeShell: async input => {
      const output = await promisify(execFile)("/bin/bash", ["-c", input.command], { env: { ...process.env, HOME: home, CLAUDE_CONFIG_DIR: join(home, ".claude") } });
      return { status: "success", exitCode: 0, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
    } });
    const target = { environment: "prod" as const, ownerId: "owner", botId: "bot", provider: "baas", deviceId: "device", bindingId: "1", activeEngine: "claude_code" };
    const result = await reader.read({ target, sessionKey: "agent:main:session:original:user:owner", deliveryKey: "send" });
    expect(result).toMatchObject({ session_id: "original", source: await realpath(file), delivery_confirmed: true, delivery: { message_id: "actual" } });
    expect(request).not.toHaveBeenCalled();
    expect(await readFile(file, "utf8")).toBe(contents);
    await expect(reader.read({ target, sessionKey: "agent:main:session:original:user:owner", expectedSessionId: "different", deliveryKey: "send" })).rejects.toMatchObject({ code: "original_session_unavailable" });
    await expect(reader.read({ target, sessionKey: "agent:main:session:unmapped:user:owner", deliveryKey: "send" })).rejects.toMatchObject({ code: "original_session_unavailable", message: expect.stringContaining("CC session mapping unavailable or ambiguous") });
  } finally { await rm(home, { recursive: true, force: true }); }
});
