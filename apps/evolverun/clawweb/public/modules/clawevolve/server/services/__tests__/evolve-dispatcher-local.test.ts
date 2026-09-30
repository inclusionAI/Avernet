import type { LaunchStorage } from "../evolve/runner-launch.js";
let storage: LaunchStorage;
import { buildRunnerLaunch } from "../evolve-dispatcher.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { buildDirectRunnerMessage as buildMessage, cancelEvolveExecution, dispatchEvolveTaskLogArchive,
  resolveEvolveTransport, type EvolveDispatchInput } from "../evolve-dispatcher.js";

vi.mock("@avernet/clawweb-shared/server/db", () => ({ resolveBaasConfig: () => ({
  environments: { dev: { baseUrl: "http://127.0.0.1:8890", apiKey: "local-test" } },
  evolveScriptPaths: { dev: "/runner/clawevolve_async_runner.sh" },
  commandTenant: "clawevolve", commandTimeoutSeconds: 30,
}) }));

const request = (provider: "baas" | "arca", env = "dev"): EvolveDispatchInput => ({
  taskId: "EV-LOCAL", stepPk: 1, stepId: "STEP-LOCAL", stepType: "hardening",
  userId: "owner", botId: "bot", mode: "run",
  command: "/clawevolve-hardening --task-id EV-LOCAL --step-id STEP-LOCAL",
  runtime: { provider, env },
  ...(env === "dev" ? { transport: "message" as const, runnerEnvironment: "local" as const } : {}),
});

beforeEach(() => {
  vi.stubEnv("SERVER_ENV", "dev");
  storage = { artifactStore: { putObject: async () => ({ etag: null }),
    getObject: async () => { throw new Error("unused"); },
    createSignedUrl: async (key) => `https://artifacts.example/${key}` } };
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each(["baas", "arca"] as const)("routes local %s and supplies its Runner environment", async (provider) => {
  const input = request(provider);
  expect(resolveEvolveTransport(input)).toBe("message");
  const message = await buildDirectRunnerMessage(input);
  expect(message).toContain("CLAWEVOLVE_RUNNER_ENVIRONMENT=local");
  expect(message).not.toContain("OPENCLAW_WORKSPACE=");
  expect(message).not.toContain("ENGINE_RUNTIME_LAYOUT_HOME=");
  expect(message).toContain("--launch-url");
  expect(message).not.toContain("--args-base64");
  expect(message).toBe(await buildDirectRunnerMessage(request(provider === "baas" ? "arca" : "baas")));
});

it.each(["pre", "prod"])("retains online %s BaaS command and ARCA message routing", async (env) => {
  expect(resolveEvolveTransport(request("baas", env))).toBe("baas_execute_command");
  expect(resolveEvolveTransport(request("arca", env))).toBe("message");
  expect(await buildDirectRunnerMessage(request("arca", env))).not.toContain("SECBAAS_SANDBOX_BACKEND");
});

it("does not infer local topology from the deployment environment", () => {
  const { transport, runnerEnvironment, ...input } = request("baas");
  expect(resolveEvolveTransport(input)).toBe("baas_execute_command");
});

it.each(["baas", "arca"] as const)("uses local %s Message and the same environment for log collection", async (provider) => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: 0, data: { run_id: "log", session_id: "session" } })));
  vi.stubGlobal("fetch", fetchMock);
  const input = request(provider);
  const result = await dispatchEvolveTaskLogArchive({ ...input, archiveId: "LOG-LOCAL",
    runtime: input.runtime!, callbackUrl: "http://127.0.0.1:5196/callback", clawwebUrl: "http://127.0.0.1:5196" });
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toContain("/openapi/v1/messages");
  const body = JSON.parse(String(init.body));
  expect(body.message).toContain("CLAWEVOLVE_RUNNER_ENVIRONMENT=local");
  expect(body.message).toContain("clawevolve_task_log_runner.sh");
  expect(result.platformResponse.evolve_dispatch).toMatchObject({ provider, transport: "message" });
});

it("sends the local stop command with the same Runner environment", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: 0, data: { success: true } })));
  vi.stubGlobal("fetch", fetchMock);
  await cancelEvolveExecution({ ...request("baas"), sessionId: "session",
    platformResponse: { evolve_dispatch: { transport: "message", runner_mode: "direct" } } });
  const body = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body));
  expect(body.message).toContain("--stage stop");
  expect(body.message).toContain("CLAWEVOLVE_RUNNER_ENVIRONMENT=local");
});

function buildDirectRunnerMessage(input: EvolveDispatchInput, config?: Parameters<typeof buildMessage>[1]) {
  return buildMessage(input, config, storage);
}
