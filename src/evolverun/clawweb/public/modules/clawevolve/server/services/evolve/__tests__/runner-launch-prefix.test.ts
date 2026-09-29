import { createHash } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { resolveBaasConfig } from "@avernet/clawweb-shared/server/db";
import { buildDirectRunnerMessage, type EvolveDispatchInput } from "../../evolve-dispatcher.js";
import { freezeRunnerLaunch, type LaunchStorage, type RunnerLaunch } from "../runner-launch.js";

afterEach(() => vi.unstubAllEnvs());

function restrictedStorage() {
  const objects = new Map<string, Buffer>();
  const putObject = vi.fn(async (key: string, value: Buffer | Uint8Array | string) => {
    if (!key.startsWith("evolution/")) throw new Error("Object is outside the allowed prefix");
    objects.set(key, Buffer.from(value));
    return { etag: null };
  });
  const createSignedUrl = vi.fn(async (key: string) => {
    if (!objects.has(key)) throw new Error("Cannot sign a missing launch");
    return `https://downloads.example.test/${key}`;
  });
  const storage: LaunchStorage = {
    artifactStore: { putObject, getObject: async () => { throw new Error("unused"); },
      createSignedUrl: async () => { throw new Error("Use the download store"); } },
    artifactUrlStore: { createSignedUrl },
  };
  return { storage, objects, putObject, createSignedUrl };
}

const launch: RunnerLaunch = {
  schemaVersion: "clawevolve.runner-launch.v1", taskId: "EV-1", stepId: "STEP-1",
  stage: "stage-execute", invocationId: "STEP-1", runtimeMaintenance: false,
  args: "--task-id EV-1 --step-id STEP-1",
};

it.each([
  ["diagnose", "/clawevolve-diagnose --judge-backend subagent"],
  ["skill_prepare", "/clawevolve-stage-execute"],
] as const)("starts an ARCA %s with prefix-restricted storage and the separate download signer", async (stepType, command) => {
  vi.stubEnv("SERVER_ENV", "pre");
  const { storage, objects, createSignedUrl } = restrictedStorage();
  const input: EvolveDispatchInput = {
    taskId: "EV-1", stepPk: 1, stepId: "STEP-1", stepType,
    userId: "user-1", botId: "bot-1", mode: "message",
    command: `${command} --task-id EV-1 --step-id STEP-1`,
    runtime: { activeEngine: "openclaw", botType: "personal", hasServiceBot: false,
      botStatus: "active", bindingId: 1, provider: "arca", deviceId: "device-1",
      bindingStatus: "active", env: "prod" },
  };
  const message = await buildDirectRunnerMessage(input, {
    ...resolveBaasConfig(),
    evolveScriptPaths: { pre: "/opt/clawevolve/pre/clawevolve_async_runner.sh" },
  }, storage);
  expect(objects.size).toBe(1);
  const [key, content] = [...objects][0];
  const digest = createHash("sha256").update(content).digest("hex");
  expect(key).toBe(`evolution/EV-1/runner-launches/${digest}.json`);
  expect(createSignedUrl).toHaveBeenCalledWith(key, "GET", 3600);
  expect(message).toContain(`--launch-url 'https://downloads.example.test/${key}' --launch-sha256 ${digest}`);
  expect(JSON.parse(content.toString())).toMatchObject({ taskId: "EV-1", stepId: "STEP-1" });
});

it("isolates tasks and HITL invocations while keeping identical retries idempotent", async () => {
  const { storage, objects } = restrictedStorage();
  const first = await freezeRunnerLaunch(launch, storage);
  expect(await freezeRunnerLaunch(launch, storage)).toEqual(first);
  const resumed = await freezeRunnerLaunch({ ...launch, invocationId: "STEP-1:hitl:HITL-2" }, storage);
  const other = await freezeRunnerLaunch({ ...launch, taskId: "EV-2", stepId: "STEP-2",
    invocationId: "STEP-2", args: "--task-id EV-2 --step-id STEP-2" }, storage);
  expect(first.url).toContain("/evolution/EV-1/runner-launches/");
  expect(resumed.url).toContain("/evolution/EV-1/runner-launches/");
  expect(resumed.url).not.toBe(first.url);
  expect(other.url).toContain("/evolution/EV-2/runner-launches/");
  expect(objects.size).toBe(3);
});

it.each(["", "..", "../EV-1", "EV-1/../../outside", "EV-1\\outside", "EV-1%2foutside"])(
  "rejects a task ID that could escape its object directory: %s", async (taskId) => {
    const { storage, putObject, createSignedUrl } = restrictedStorage();
    await expect(freezeRunnerLaunch({ ...launch, taskId }, storage)).rejects.toThrow("invalid task ID");
    expect(putObject).not.toHaveBeenCalled();
    expect(createSignedUrl).not.toHaveBeenCalled();
  },
);
