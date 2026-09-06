import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildArcaRunnerMessage,
  buildBaasEvolveCommand,
  cancelEvolveExecution,
  dispatchEvolveCommand,
  dispatchEvolveTaskLogArchive,
  resolveEvolveBaasTargetConfig,
  resolveEvolveRunnerConfig,
  resolveEvolveTransport,
  parseArcaRunnerCallback,
  usesArcaDirectRunner,
  type EvolveDispatchInput,
} from "../evolve-dispatcher.js";
import { normalizeEvolutionGoal, quoteCommandArgument } from "../evolve/command.js";

vi.mock("@avernet/clawweb-shared/server/db", () => ({
  resolveBaasConfig: () => ({
    apiKey: "prod-api-key",
    iamtoken: "test-iam-token",
    baseUrl: "https://baas.example.com",
    environments: {
      pre: {
        apiKey: "pre-api-key",
        baseUrl: "https://baas-pre.example.com",
      },
      prod: {
        apiKey: "prod-api-key",
        baseUrl: "https://baas.example.com",
      },
    },
    evolveScriptPaths: {
      dev: "/opt/clawevolve/pre/clawevolve_async_runner.sh",
      pre: "/opt/clawevolve/pre/clawevolve_async_runner.sh",
      prod: "/opt/clawevolve/prod/clawevolve_async_runner.sh",
    },
    commandTenant: "clawevolve",
    commandTimeoutSeconds: 30,
  }),
}));

beforeEach(() => {
  vi.stubEnv("SERVER_ENV", "pre");
});

describe("dispatchEvolveTaskLogArchive", () => {
  it("uses the dedicated runner for BaaS without runtime maintenance", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { exit_code: 0, stdout: '{"ok":true,"status":"started"}' },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveTaskLogArchive({
      taskId: "EV-LOG", archiveId: "LOG-001", userId: "197444", botId: "bot-001",
      callbackUrl: "https://pre.clawevolve.example.com/callback",
      clawwebUrl: "https://pre.clawevolve.example.com", runtime: runtime("pre", "baas"),
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.cmd).toBe("bash /opt/clawevolve/pre/clawevolve_task_log_runner.sh --task-id EV-LOG --archive-id LOG-001 --clawweb-url 'https://pre.clawevolve.example.com'");
    expect(body.cmd).not.toContain("RUNTIME_MAINTENANCE");
    expect(result.platformResponse.evolve_dispatch?.transport).toBe("baas_execute_command");
  });

  it("sends ARCA one strict exec message for the same dedicated runner", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { run_id: "run-log", session_id: "session-log" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchEvolveTaskLogArchive({
      taskId: "EV-LOG", archiveId: "LOG-001", userId: "197444", botId: "bot-001",
      callbackUrl: "https://pre.clawevolve.example.com/callback",
      clawwebUrl: "https://pre.clawevolve.example.com", runtime: runtime("pre", "arca"),
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.message).toContain("clawevolve_task_log_runner.sh");
    expect(body.message).toContain("必须使用 exec 工具原样执行");
    expect(body.message).not.toContain("clawevolve_async_runner.sh");
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("evolution goal command argument", () => {
  it("normalizes whitespace and safely quotes shell-like content", () => {
    const goal = normalizeEvolutionGoal("  修复  工具失败\n并保留 '$(whoami)'  ");
    expect(goal).toBe("修复 工具失败 并保留 '$(whoami)'");
    expect(quoteCommandArgument(goal)).toBe(`'修复 工具失败 并保留 '"'"'$(whoami)'"'"''`);
  });

  it("rejects invalid or oversized goals", () => {
    expect(() => normalizeEvolutionGoal(123)).toThrow("goal 必须是字符串");
    expect(() => normalizeEvolutionGoal("a\0b")).toThrow("NUL");
    expect(() => normalizeEvolutionGoal("目".repeat(2001))).toThrow("2000");
  });
});

function input(overrides: Partial<EvolveDispatchInput> = {}): EvolveDispatchInput {
  return {
    taskId: "EV-001", stepPk: 1, stepId: "STEP-001", stepType: "optimize",
    userId: "197444", botId: "bot-001", mode: "message", callbackUrl: "http://callback",
    command: "/clawevolve-workflow --stage optimize --train-bench-domain-id train-01 --test-bench-domain-id test-01 --task-id EV-001 --step-id STEP-001 --round 2",
    ...overrides,
  };
}

function runtime(env: string, provider: "baas" | "arca" = "baas") {
  return {
    activeEngine: "openclaw",
    botType: "personal",
    hasServiceBot: true,
    botStatus: "active",
    bindingId: 1,
    provider,
    deviceId: `DEVICE-${env}-1`,
    bindingStatus: "active",
    env,
  };
}

function runnerStdout(taskId = "EV-001", stepId = "STEP-001", pid = 123): string {
  return JSON.stringify({ ok: true, status: "started", pid, task_id: taskId, step_id: stepId });
}

describe("buildBaasEvolveCommand", () => {
  it("routes by stage and sends only rendered arguments to the runner", () => {
    const result = buildBaasEvolveCommand(input(), "/opt/clawevolve/clawevolve_async_runner.sh");
    const payload = result.match(/--args-base64 '([^']*)'/)?.[1];
    expect(result).toMatch(/^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/clawevolve_async_runner\.sh --stage optimize --args-base64 /);
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe(input().command.split(" ").slice(3).join(" "));
  });

  it("keeps additional slash-command arguments", () => {
    const command = `${input().command} --model gpt-4o-mini --test_a 111`;
    const result = buildBaasEvolveCommand(input({ command }), "/runner.sh");
    const payload = result.match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe(command.split(" ").slice(3).join(" "));
  });

  it("accepts rendered commands longer than the former 1000 character limit", () => {
    const goal = "提升工具调用稳定性".repeat(150);
    const command = `${input().command} --goal "${goal}"`;
    expect(command.length).toBeGreaterThan(1000);
    const result = buildBaasEvolveCommand(input({ command }), "/runner.sh");
    const payload = result.match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe(command.split(" ").slice(3).join(" "));
  });

  it("decouples the Message command name from the BaaS stage", () => {
    const result = buildBaasEvolveCommand(input({ command: "/clawevolve-workflow --stage optimize --goal test" }), "/runner.sh");
    expect(result).toContain("--stage optimize");
    const payload = result.match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe("--goal test");
  });

  it("rejects a non-claw command or multiline content", () => {
    expect(() => buildBaasEvolveCommand(input({ command: "echo unsafe" }), "/runner.sh"))
      .toThrow("指令非法");
    expect(() => buildBaasEvolveCommand(input({ command: "/clawevolve-optimize --round 1\nwhoami" }), "/runner.sh"))
      .toThrow("指令非法");
  });

  it("supports plan and diagnose commands through the same runner", () => {
    for (const [stepType, command] of [
      ["plan", "/clawevolve-plan --strategy conservative --task-id EV-001 --step-id STEP-001"],
      ["diagnose", "/clawevolve-diagnose --model gpt-4o-mini --task-id EV-001 --step-id STEP-001"],
    ] as const) {
      const result = buildBaasEvolveCommand(input({ stepType, command }), "/runner.sh");
      const payload = result.match(/--args-base64 '([^']*)'/)?.[1];
      expect(result).toContain(`--stage clawevolve-${stepType}`);
      expect(Buffer.from(payload!, "base64").toString("utf8")).toBe(command.split(" ").slice(1).join(" "));
    }
  });

  it("routes Bench commands to the clawevolve-bench stage", () => {
    const command = "/clawevolve-bench --task-id EV-001 --step-id STEP-001 --domain-id blog-writing";
    const result = buildBaasEvolveCommand(input({ stepType: "bench", command }), "/runner.sh");
    expect(result).toContain("--stage clawevolve-bench");
  });

  it.each([
    ["pack", "/clawevolve-pack --mode pack --task-id EV-001 --step-id STEP-001", "clawevolve-pack"],
    ["restore", "/clawevolve-pack --mode restore --task-id EV-001 --step-id STEP-001 --source-task-id EV-SOURCE --source-kind snapshot", "clawevolve-pack"],
  ])("routes %s commands to its BaaS stage", (stepType, command, stage) => {
    const result = buildBaasEvolveCommand(input({ stepType, command }), "/runner.sh");
    expect(result).toContain(`--stage ${stage}`);
    expect(result).toContain("CLAWEVOLVE_RUNTIME_MAINTENANCE=false");
  });

  it("allows ordinary tasks to disable runtime maintenance", () => {
    const result = buildBaasEvolveCommand(input({ runtimeMaintenance: false }), "/runner.sh");
    expect(result).toMatch(/^CLAWEVOLVE_RUNTIME_MAINTENANCE=false bash /);
  });
});

describe("resolveEvolveTransport", () => {
  const baasRuntime = { provider: "baas" as const, botType: "personal", hasServiceBot: false };
  const arcaRuntime = { provider: "arca" as const };

  it("uses execute-command for BaaS Optimize, Bench, Pack, Restore and Runtime Cleanup", () => {
    expect(resolveEvolveTransport({ stepType: "optimize", runtime: baasRuntime })).toBe("baas_execute_command");
    expect(resolveEvolveTransport({ stepType: "bench", runtime: baasRuntime })).toBe("baas_execute_command");
    expect(resolveEvolveTransport({ stepType: "pack", runtime: baasRuntime })).toBe("baas_execute_command");
    expect(resolveEvolveTransport({ stepType: "restore", runtime: baasRuntime })).toBe("baas_execute_command");
    expect(resolveEvolveTransport({ stepType: "runtime_cleanup", runtime: baasRuntime })).toBe("baas_execute_command");
  });

  it.each(["diagnose", "plan"])("uses execute-command for personal BaaS %s", (stepType) => {
    expect(resolveEvolveTransport({ stepType, runtime: baasRuntime })).toBe("baas_execute_command");
  });

  it("routes service Bot Diagnose by its BaaS provider instead of bot type", () => {
    expect(resolveEvolveTransport({
      stepType: "diagnose",
      runtime: { ...runtime("pre"), botType: "service" },
    })).toBe("baas_execute_command");
  });

  it("keeps subsequent service Bot stages on the BaaS runner route", () => {
    expect(resolveEvolveTransport({
      stepType: "plan",
      runtime: { ...runtime("pre"), botType: "service" },
    })).toBe("baas_execute_command");
  });

  it("does not let the legacy forceMessage flag bypass the BaaS runner", () => {
    expect(resolveEvolveTransport({ stepType: "optimize", runtime: baasRuntime, forceMessage: true })).toBe("baas_execute_command");
  });

  it.each(["diagnose", "plan", "optimize", "apply", "bench"])("uses Message for ARCA %s", (stepType) => {
    expect(resolveEvolveTransport({ stepType, runtime: arcaRuntime })).toBe("message");
  });
});

describe("ARCA direct-runner coverage", () => {
  it.each(["diagnose", "plan", "optimize", "bench", "bench_plan", "pack", "restore", "runtime_cleanup"])(
    "dispatches %s directly without an initializer",
    (businessStepType) => {
      expect(usesArcaDirectRunner({ runtime: { provider: "ARCA" }, stepType: businessStepType })).toBe(true);
    },
  );

  it.each([
    ["diagnose", "/clawevolve-diagnose --judge-backend subagent --task-id EV-001 --step-id STEP-001", "clawevolve-diagnose", true, "--judge-backend subagent --task-id EV-001 --step-id STEP-001"],
    ["plan", "/clawevolve-plan --task-id EV-001 --step-id STEP-001", "clawevolve-plan", true, "--task-id EV-001 --step-id STEP-001"],
    ["bench", "/clawevolve-bench --task-id EV-001 --step-id STEP-001", "clawevolve-bench", true, "--task-id EV-001 --step-id STEP-001"],
    ["bench_plan", "/clawevolve-workflow --stage bench-plan --task-id EV-001 --step-id STEP-001", "bench-plan", true, "--task-id EV-001 --step-id STEP-001"],
    ["optimize", "/clawevolve-workflow --stage optimize --task-id EV-001 --step-id STEP-001 --round 1", "optimize", true, "--task-id EV-001 --step-id STEP-001 --round 1"],
    ["pack", "/clawevolve-pack --mode pack --task-id EV-001 --step-id STEP-001", "clawevolve-pack", false, "--mode pack --task-id EV-001 --step-id STEP-001"],
    ["restore", "/clawevolve-pack --mode restore --task-id EV-001 --step-id STEP-001", "clawevolve-pack", false, "--mode restore --task-id EV-001 --step-id STEP-001"],
    ["runtime_cleanup", "/clawevolve-runtime-cleanup --task-id EV-001 --step-id STEP-001", "runtime-cleanup", false, "--task-id EV-001 --step-id STEP-001"],
  ] as const)("wraps %s in the registered Runner stage", (stepType, command, stage, maintenance, expectedArgs) => {
    const message = buildArcaRunnerMessage(input({
      stepType, command, runtime: runtime("pre", "arca"),
    }));
    expect(message).toContain("必须使用 exec 工具原样执行下面唯一一条命令");
    expect(message).toContain(`CLAWEVOLVE_RUNTIME_MAINTENANCE=${maintenance} bash /opt/clawevolve/pre/clawevolve_async_runner.sh --stage ${stage}`);
    const payload = message.match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe(expectedArgs);
    expect(message.split("\n").at(-1)).not.toContain("\n");
  });

  it("rejects secrets, API Judge, debug mode and unsupported steps", () => {
    expect(() => buildArcaRunnerMessage(input({
      stepType: "diagnose", command: "/clawevolve-diagnose --judge-backend api",
      runtime: runtime("pre", "arca"), secrets: { diagnoseApiKey: "secret" },
    }))).toThrow("禁止传递");
    expect(() => buildArcaRunnerMessage(input({
      stepType: "diagnose", command: "/clawevolve-diagnose --judge-backend api",
      runtime: runtime("pre", "arca"),
    }))).toThrow("只支持 Agent Judge");
    expect(() => buildArcaRunnerMessage(input({
      stepType: "plan", command: "/clawevolve-plan --debug true",
      runtime: runtime("pre", "arca"),
    }))).toThrow("--debug true");
    expect(() => buildArcaRunnerMessage(input({
      stepType: "apply", command: "/clawevolve-apply",
      runtime: runtime("pre", "arca"),
    }))).toThrow("不支持 apply");
  });

  it("parses the Runner result from common Callback result shapes", () => {
    const expected = { taskId: "EV-001", stepId: "STEP-001" };
    expect(parseArcaRunnerCallback(runnerStdout(), null, expected)?.status).toBe("started");
    expect(parseArcaRunnerCallback({ payloads: [{ text: runnerStdout() }] }, null, expected)?.pid).toBe(123);
    expect(parseArcaRunnerCallback({ payloads: [{ text: runnerStdout("wrong") }] }, null, expected)).toBeNull();
  });
});

describe("dispatchEvolveCommand environment routing", () => {
  it("allows Task Guard messages to omit the legacy platform callback", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { message_id: "message-task-guard", session_id: "session-task-guard" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchEvolveCommand(input({
      stepType: "suggestion_apply",
      command: "[clawmind-task-guard-apply:v1]",
      callbackUrl: undefined,
      runtime: runtime("pre", "arca"),
      forceMessage: true,
    }));

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body.message).toBe("[clawmind-task-guard-apply:v1]");
    expect(body).not.toHaveProperty("callback_url");
  });

  it("waits up to 60 seconds for Bot Message dispatch and reports a clear timeout", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("This operation was aborted", "AbortError")));
    }));
    vi.stubGlobal("fetch", fetchMock);

    const dispatched = dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      runtime: runtime("pre", "arca"),
    }));
    const timeoutExpectation = expect(dispatched).rejects.toThrow("Bot 平台投递超时（60秒）");

    await vi.advanceTimersByTimeAsync(59_999);
    expect(fetchMock).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    await timeoutExpectation;
    vi.useRealTimers();
  });

  it("dispatches a selected service Bot through its BaaS runner", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "diagnose",
      command: "/clawevolve-diagnose --judge-backend subagent --task-id EV-001 --step-id STEP-001 --source service_export",
      mode: "run",
      runtime: { ...runtime("prod"), botType: "service" },
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(String(url)).toBe("https://baas.example.com/api/v1/bots/clawevolve/DEVICE-prod-1/execute-command?device_affinity=EV-001");
    expect(body.cmd).toContain("clawevolve_async_runner.sh --stage clawevolve-diagnose");
    expect(body).not.toHaveProperty("message");
    expect(result.platformResponse.evolve_dispatch).toEqual(expect.objectContaining({
      provider: "baas", transport: "baas_execute_command", environment: "prod",
    }));
  });

  it("dispatches a pre Plan command through the pre BaaS endpoint", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      mode: "run",
      runtime: runtime("pre"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("https://baas-pre.example.com/api/v1/bots/clawevolve/DEVICE-pre-1/execute-command?device_affinity=EV-001");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)?.Authorization).toBe("Bearer pre-api-key");
    expect(JSON.parse(String(init?.body)).cmd).toMatch(/^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/pre\/clawevolve_async_runner\.sh /);
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("pre");
    expect(result.platformResponse.evolve_dispatch?.release_lane).toBe("pre");
  });

  it("dispatches a prod Plan command through the prod BaaS endpoint", async () => {
    vi.stubEnv("SERVER_ENV", "prod");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      mode: "run",
      runtime: runtime("prod"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("https://baas.example.com/api/v1/bots/clawevolve/DEVICE-prod-1/execute-command?device_affinity=EV-001");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)?.Authorization).toBe("Bearer prod-api-key");
    expect(JSON.parse(String(init?.body)).cmd).toMatch(/^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/prod\/clawevolve_async_runner\.sh /);
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("prod");
    expect(result.platformResponse.evolve_dispatch?.release_lane).toBe("prod");
  });

  it("uses the pre runner lane when pre ClawWeb dispatches to a prod Bot", async () => {
    vi.stubEnv("SERVER_ENV", "pre");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      mode: "run",
      runtime: runtime("prod"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://baas.example.com/api/v1/bots/clawevolve/DEVICE-prod-1/execute-command?device_affinity=EV-001",
    );
    expect((init?.headers as Record<string, string>)?.Authorization).toBe("Bearer prod-api-key");
    expect(JSON.parse(String(init?.body)).cmd).toMatch(
      /^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/pre\/clawevolve_async_runner\.sh /,
    );
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("prod");
    expect(result.platformResponse.evolve_dispatch?.release_lane).toBe("pre");
  });

  it("uses the prod runner lane when prod ClawWeb dispatches to a pre Bot", async () => {
    vi.stubEnv("SERVER_ENV", "prod");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      mode: "run",
      runtime: runtime("pre"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("https://baas-pre.example.com/");
    expect((init?.headers as Record<string, string>)?.Authorization).toBe("Bearer pre-api-key");
    expect(JSON.parse(String(init?.body)).cmd).toMatch(
      /^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/prod\/clawevolve_async_runner\.sh /,
    );
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("pre");
    expect(result.platformResponse.evolve_dispatch?.release_lane).toBe("prod");
  });

  it("routes a collaborator Message task to the Bot actual owner", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { message_id: "message-collab-1", session_id: "session-collab-1" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchEvolveCommand(input({
      stepType: "plan",
      runtime: { ...runtime("prod", "arca"), ownerId: "bot-owner-2", accessType: "collaborator" },
    }));

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(JSON.parse(String(init?.body))).toEqual(expect.objectContaining({
      bot_id: "bot-001:bot-owner-2",
    }));
  });

  it("dispatches ARCA through a direct Runner Message and records the additive marker", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { message_id: "message-arca-1", session_id: "session-arca-1" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001 --goal '测试目标'",
      runtime: runtime("prod", "arca"),
    }));

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body.message).toContain("CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash /opt/clawevolve/pre/clawevolve_async_runner.sh --stage clawevolve-plan");
    expect(body.message).not.toContain("\n/clawevolve-plan ");
    expect(body.metadata?.bot_options).toEqual({ lifecycle_stage: "draft" });
    expect(result.platformResponse.evolve_dispatch).toEqual(expect.objectContaining({
      provider: "arca", transport: "message", runner_mode: "direct", release_lane: "pre",
    }));
  });

  it("ignores legacy forceMessage and keeps BaaS on its Runner", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await dispatchEvolveCommand(input({
      stepType: "optimize", runtime: runtime("pre", "baas"), forceMessage: true,
    }));
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(String(url)).toContain("/execute-command?");
    expect(body.cmd).toContain("clawevolve_async_runner.sh --stage optimize");
    expect(body).not.toHaveProperty("message");
  });

  it("dispatches a pre BaaS command through the pre execute-command endpoint", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      runtime: runtime("pre"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://baas-pre.example.com/api/v1/bots/clawevolve/DEVICE-pre-1/execute-command?device_affinity=EV-001",
    );
    expect(init).toEqual(expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ Authorization: "Bearer pre-api-key" }),
    }));
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("pre");
  });

  it("stops a pre BaaS command through the same pre runner lane", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0 },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await cancelEvolveExecution({
      taskId: "EV-001",
      stepId: "STEP-001",
      stepType: "diagnose",
      userId: "197444",
      botId: "bot-001",
      sessionId: null,
      platformResponse: { evolve_dispatch: { transport: "baas_execute_command" } },
      runtime: runtime("pre"),
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://baas-pre.example.com/api/v1/bots/clawevolve/DEVICE-pre-1/execute-command?device_affinity=EV-001",
    );
    const body = JSON.parse(String(init?.body));
    expect(body.cmd).toMatch(
      /^bash \/opt\/clawevolve\/pre\/clawevolve_async_runner\.sh --stage stop /,
    );
  });

  it("uses the BaaS runtime for stop even when an old Step recorded Message transport", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0 },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await cancelEvolveExecution({
      taskId: "EV-001",
      stepId: "STEP-001",
      stepType: "diagnose",
      userId: "197444",
      botId: "bot-service",
      sessionId: "legacy-message-session",
      platformResponse: { evolve_dispatch: { provider: "baas", transport: "message" } },
      runtime: { ...runtime("pre"), botType: "service" },
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/execute-command?");
    expect(JSON.parse(String(init?.body)).cmd).toContain("--stage stop");
    expect(result.transport).toBe("baas_execute_command");
  });

  it("stops an ARCA direct Runner with a strict stop exec Message", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { message_id: "stop-1", session_id: "session-arca-1" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await cancelEvolveExecution({
      taskId: "EV-001", stepId: "STEP-001", stepType: "diagnose", userId: "197444", botId: "bot-001",
      sessionId: "session-arca-1",
      platformResponse: { evolve_dispatch: { provider: "arca", transport: "message", runner_mode: "direct" } },
      runtime: runtime("pre", "arca"),
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    const message = body.message as string;
    expect(message).toContain("bash /opt/clawevolve/pre/clawevolve_async_runner.sh --stage stop");
    expect(body.metadata?.bot_options).toEqual({ lifecycle_stage: "draft" });
    const payload = message.match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).toBe("--task-id EV-001 --step-id STEP-001");
  });

  it("injects the Diagnose LLM key through env without putting it in command arguments", async () => {
    const secret = "diagnose-secret-key";
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "diagnose",
      command: "/clawevolve-diagnose --task-id EV-001 --step-id STEP-001 --intent test",
      runtime: { ...runtime("pre"), botType: "personal", hasServiceBot: false },
      secrets: { diagnoseApiKey: secret },
    }));

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const requestBody = JSON.parse(String(init?.body));
    expect(requestBody.env).toEqual({ OPENAI_API_KEY: secret });
    expect(requestBody.cmd).not.toContain(secret);
    const payload = String(requestBody.cmd).match(/--args-base64 '([^']*)'/)?.[1];
    expect(Buffer.from(payload!, "base64").toString("utf8")).not.toContain("--api-key");
    expect(JSON.stringify(result.platformResponse)).not.toContain(secret);
  });

  it("rejects BaaS Diagnose before dispatch when the LLM key is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(dispatchEvolveCommand(input({
      stepType: "diagnose",
      command: "/clawevolve-diagnose --task-id EV-001 --step-id STEP-001 --intent test",
      runtime: { ...runtime("pre"), botType: "personal", hasServiceBot: false },
    }))).rejects.toThrow("缺少 LLM API Key");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("runs Subagent Judge Diagnose on BaaS without API key env injection", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchEvolveCommand(input({
      stepType: "diagnose",
      command: "/clawevolve-diagnose --judge-backend subagent --task-id EV-001 --step-id STEP-001 --intent test",
      runtime: { ...runtime("pre"), botType: "personal", hasServiceBot: false },
    }));

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const requestBody = JSON.parse(String(init?.body));
    expect(requestBody.env).toBeUndefined();
  });

  it.each([undefined, "dev", "unknown"])("fails closed for unsupported runtime env %s", async (env) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      runtime: runtime(env as string),
    }))).rejects.toThrow("禁止回落到生产消息平台");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when the target environment API key is missing", () => {
    expect(() => resolveEvolveBaasTargetConfig({
      apiKey: "prod-api-key",
      iamtoken: "",
      baseUrl: "https://baas.example.com",
      environments: {
        pre: {
          apiKey: "", baseUrl: "https://baas-pre.example.com",
        },
        prod: {
          apiKey: "prod-api-key", baseUrl: "https://baas.example.com",
        },
      },
      evolveScriptPaths: {
        dev: "/opt/clawevolve/pre/clawevolve_async_runner.sh",
        pre: "/opt/clawevolve/pre/clawevolve_async_runner.sh",
        prod: "/opt/clawevolve/prod/clawevolve_async_runner.sh",
      },
      commandTenant: "clawevolve",
      commandTimeoutSeconds: 30,
    }, "pre")).toThrow("未配置 pre apiKey");
  });

  it.each([
    ["pre", ""],
    ["pre", "/opt/clawevolve/prod/clawevolve_async_runner.sh"],
    ["prod", "/opt/clawevolve/pre/clawevolve_async_runner.sh"],
    ["prod", "/opt/clawevolve/prod/../pre/clawevolve_async_runner.sh"],
  ])("fails closed for invalid %s evolveScriptPath %s", (environment, evolveScriptPath) => {
    expect(() => resolveEvolveRunnerConfig({
      apiKey: "prod-api-key",
      iamtoken: "",
      baseUrl: "https://baas.example.com",
      environments: {
        pre: {
          apiKey: "pre-api-key", baseUrl: "https://baas-pre.example.com",
        },
        prod: {
          apiKey: "prod-api-key", baseUrl: "https://baas.example.com",
        },
      },
      evolveScriptPaths: {
        dev: "/opt/clawevolve/pre/clawevolve_async_runner.sh",
        pre: environment === "pre"
          ? evolveScriptPath : "/opt/clawevolve/pre/clawevolve_async_runner.sh",
        prod: environment === "prod"
          ? evolveScriptPath : "/opt/clawevolve/prod/clawevolve_async_runner.sh",
      },
      commandTenant: "clawevolve",
      commandTimeoutSeconds: 30,
    }, environment as "pre" | "prod")).toThrow(`未配置合法的 ${environment} evolveScriptPath`);
  });

  it("uses the configured pre runner by default in the dev ClawWeb environment", async () => {
    vi.stubEnv("SERVER_ENV", "dev");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: { exit_code: 0, stdout: runnerStdout() },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dispatchEvolveCommand(input({
      stepType: "plan",
      command: "/clawevolve-plan --task-id EV-001 --step-id STEP-001",
      runtime: runtime("prod"),
    }));

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("https://baas.example.com/");
    expect(JSON.parse(String(init?.body)).cmd).toMatch(
      /^CLAWEVOLVE_RUNTIME_MAINTENANCE=true bash \/opt\/clawevolve\/pre\/clawevolve_async_runner\.sh /,
    );
    expect(result.platformResponse.evolve_dispatch?.environment).toBe("prod");
    expect(result.platformResponse.evolve_dispatch?.release_lane).toBe("dev");
  });
});
