import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { RecoverySessionReader } from "../session-reader.js";

const id = "run-original", key = `agent:main:cron:daily:run:${id}`;
const target = { environment: "prod" as const, ownerId: "owner", botId: "default", provider: "arca", bindingId: "1", deviceId: "device", activeEngine: "openclaw" };
let home: string, directory: string, reader: RecoverySessionReader;
const input = { target, sessionKey: key, deliveryKey: "send", fromLine: 1 };
beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), "recovery-cron-read-"));
  directory = join(home, "agents/main/sessions"); await mkdir(directory, { recursive: true });
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId: "next-run" } }));
  await writeFile(join(directory, `${id}.jsonl`), [
    { type: "session", id }, { type: "message", id: "fault", message: { role: "assistant", content: "original failure" } },
  ].map(value => JSON.stringify(value)).join("\n") + "\n");
  reader = new RecoverySessionReader({ executeShell: async command => {
    try {
      const output = await promisify(execFile)("/bin/bash", ["-c", command.command], { env: { ...process.env, OPENCLAW_STATE_DIR: home }, timeout: 10000 });
      return { status: "success", exitCode: 0, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
    } catch (error) {
      const output = error as { stdout: string; stderr: string };
      return { status: "failed", exitCode: 1, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
    }
  } });
});
afterEach(async () => { await rm(home, { recursive: true, force: true }); });

it("reads the exact historic Cron run even when the base index has advanced, without changing it", async () => {
  const before = await readFile(join(directory, "sessions.json"), "utf8");
  expect(await reader.read(input)).toMatchObject({ session_id: id, messages: [{ line_no: 1 }, { line_no: 2, record: { id: "fault" } }] });
  expect(await readFile(join(directory, "sessions.json"), "utf8")).toBe(before);
});
it("uses an authoritative physical ID for an unindexed ordinary session", async () => {
  expect(await reader.read({ ...input, sessionKey: "agent:main:session:ordinary", expectedSessionId: id })).toMatchObject({ session_id: id });
});
it.each([
  { type: "session", id: "another-run" }, { type: "message", id, message: { role: "user", content: "unproven" } },
])("rejects an unindexed transcript without a matching session header: %j", async header => {
  await writeFile(join(directory, `${id}.jsonl`), JSON.stringify(header) + "\n");
  await expect(reader.read(input)).rejects.toMatchObject({ code: "original_session_unavailable" });
});
it("does not substitute a current index entry for the frozen physical ID", async () => {
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ [key]: { sessionId: "next-run" } }));
  await expect(reader.read({ ...input, expectedSessionId: id })).rejects.toMatchObject({ code: "original_session_unavailable" });
});
it("rejects a physical ID that conflicts with the Cron run key", async () => {
  await expect(reader.read({ ...input, expectedSessionId: "another-run" })).rejects.toMatchObject({ code: "original_session_unavailable" });
});
it("does not use a historic file as permission to create a new message session", async () => {
  await expect(reader.read({ ...input, requireIndex: true })).rejects.toMatchObject({ code: "original_session_unavailable" });
});
it("rejects a transcript symlink outside the exact agent directory", async () => {
  await rm(join(directory, `${id}.jsonl`));
  await writeFile(join(home, "private.jsonl"), JSON.stringify({ type: "session", id }) + "\n");
  await symlink(join(home, "private.jsonl"), join(directory, `${id}.jsonl`));
  await expect(reader.read(input)).rejects.toMatchObject({ code: "original_session_unavailable" });
});
it("does not infer a physical ID from an arbitrary session key or a path", async () => {
  await expect(reader.read({ ...input, sessionKey: "agent:main:session:unmapped" })).rejects.toMatchObject({ code: "original_session_unavailable" });
  await expect(reader.read({ ...input, expectedSessionId: "../private" })).rejects.toMatchObject({ code: "original_session_unavailable" });
});

it("cuts historical pages at the original node even after index rotation and subsequent user messages", async () => {
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ [key]: { sessionId: "next-run" } }));
  await writeFile(join(directory, `${id}.jsonl`), [
    { type: 'session', id }, { type: 'message', id: 'fault', timestamp: 9000, message: { role: 'assistant', content: 'original failure' } },
    { type: 'message', id: 'future', timestamp: 20000, message: { role: 'user', content: 'later answer' } },
  ].map(row => JSON.stringify(row)).join('\n'));
  const bounded = { ...input, expectedSessionId: id, cutoff: { messageId: 'fault', atMs: 10000 }, limit: 1 };
  expect(await reader.read(bounded)).toMatchObject({ messages: [{ line_no: 1 }], next_line: 2, historical_boundary: { end_line: 2 } });
  const last = await reader.read({ ...bounded, fromLine: 2 });
  expect(last).toMatchObject({ messages: [{ record: { id: 'fault' } }], next_line: null });
  expect(JSON.stringify(last)).not.toContain('later answer');
  expect(await reader.read({ ...bounded, fromLine: 3 })).toMatchObject({ messages: [], next_line: null });
  await expect(reader.read({ ...bounded, cutoff: { messageId: 'missing', atMs: 10000 } })).rejects.toMatchObject({ code: 'original_session_unavailable' });
  await expect(reader.read({ ...bounded, cutoff: { messageId: 'fault', atMs: 8000 } })).rejects.toMatchObject({ code: 'original_session_unavailable' });
});

it("resolves a direct Cron execution to the indexed original conversation before delivery", async () => {
  const base = "agent:main:cron:daily";
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ [base]: { sessionId: id }, [key]: { sessionId: "wrong-alias" } }));
  const before = await readFile(join(directory, "sessions.json"), "utf8");
  expect(await reader.read({ ...input, requireIndex: true })).toMatchObject({ session_key: base, session_id: id });
  expect(await readFile(join(directory, "sessions.json"), "utf8")).toBe(before);
});
it("retains a genuinely indexed run key when no matching job-level conversation exists", async () => {
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ [key]: { sessionId: id } }));
  expect(await reader.read({ ...input, requireIndex: true })).toMatchObject({ session_key: key, session_id: id });
});
it("does not strip a nested subagent key or redirect it to a Cron conversation", async () => {
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId: id } }));
  await expect(reader.read({ ...input, sessionKey: key + ":subagent:worker", expectedSessionId: id, requireIndex: true }))
    .rejects.toMatchObject({ code: "original_session_unavailable" });
});
