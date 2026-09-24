import { createHash } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { freezeRunnerLaunch as freeze, type RunnerLaunch, type LaunchStorage } from "../runner-launch.js";

const launch: RunnerLaunch = { schemaVersion: "clawevolve.runner-launch.v1", taskId: "EV-1",
  stepId: "STEP-1", stage: "clawevolve-hardening", invocationId: "STEP-1", runtimeMaintenance: false,
  args: "--task-id EV-1 --step-id STEP-1 --goal '中文与 $() 和引号'" };
let storage: LaunchStorage;
const freezeRunnerLaunch = (input: RunnerLaunch) => freeze(input, storage);
const objects = new Map<string, Buffer>();
const putObject = vi.fn(async (key: string, content: Buffer | Uint8Array | string) => {
  objects.set(key, Buffer.from(content)); return { etag: null };
});
const createSignedUrl = vi.fn(async (key: string) => `https://artifacts.example/${key}?signature=read-only`);

beforeEach(() => {
  objects.clear(); vi.clearAllMocks();
  storage = { artifactStore: { putObject, createSignedUrl,
    getObject: async (key) => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }) } };
});


it("freezes exact bytes before issuing a GET-only reference, including long Unicode input", async () => {
  const input = { ...launch, args: launch.args + " 加固".repeat(5000) };
  const reference = await freezeRunnerLaunch(input);
  const key = `runner-launches/${reference.sha256}.json`;
  const content = objects.get(key)!;
  expect(JSON.parse(content.toString())).toEqual(input);
  expect(createHash("sha256").update(content).digest("hex")).toBe(reference.sha256);
  expect(createSignedUrl).toHaveBeenCalledWith(key, "GET", 3600);
  expect(putObject.mock.invocationCallOrder[0]).toBeLessThan(createSignedUrl.mock.invocationCallOrder[0]);
});

it("reuses identical launches and isolates each HITL resume without changing Task or Step", async () => {
  const first = await freezeRunnerLaunch(launch);
  const repeated = await freezeRunnerLaunch(launch);
  const resumed = await freezeRunnerLaunch({ ...launch, invocationId: "STEP-1:hitl:HITL-2" });
  expect(repeated).toEqual(first);
  expect(resumed.sha256).not.toBe(first.sha256);
  expect(objects.size).toBe(2);
});

it("propagates storage failure and never issues a usable reference", async () => {
  putObject.mockRejectedValueOnce(new Error("disk unavailable"));
  await expect(freezeRunnerLaunch(launch)).rejects.toThrow("disk unavailable");
  expect(createSignedUrl).not.toHaveBeenCalled();
  storage = {};
  await expect(freezeRunnerLaunch(launch)).rejects.toThrow("未配置");
});

it.each(["file:///tmp/input", "http://remote.example/input", "https://example.com/a'$(id)",
  "https://user:password@example.com/file", "https://example.com/file#fragment"])(
  "rejects unsafe launch URLs: %s", async (url) => {
    createSignedUrl.mockResolvedValueOnce(url);
    await expect(freezeRunnerLaunch(launch)).rejects.toThrow("地址不合法");
  },
);

it("isolates storage even when two module dispatchers interleave", async () => {
  const otherPut = vi.fn(async () => ({ etag: null }));
  const other = { artifactStore: { ...storage.artifactStore!, putObject: otherPut,
    createSignedUrl: async (key: string) => `https://other.example/${key}` } };
  const first = await freeze(launch, storage);
  const second = await freeze(launch, other);
  expect(first.url).toContain("artifacts.example");
  expect(second.url).toContain("other.example");
  expect((await freeze(launch, storage)).url).toBe(first.url);
  expect(otherPut).toHaveBeenCalledTimes(1);
});
