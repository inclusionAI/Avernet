import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import type { AgentToolContext } from "../contracts.js";
import {
  buildAgentRuntimeCommand,
  AgentRuntimeTool,
} from "../runtime-tool.js";
import { BotRuntimeClient, BaasRuntimeProvider } from "@avernet/clawweb-shared/server/services/bot-runtime";

afterEach(() => vi.unstubAllGlobals());

function baasConfig(): ResolvedBaasConfig {
  return {
    apiKey: "prod-api-key",
    iamtoken: "",
    baseUrl: "https://secbaas-prod.example.test",
    environments: {
      pre: {
        apiKey: "pre-api-key",
        baseUrl: "https://secbaas-pre.example.test",
      },
      prod: {
        apiKey: "prod-api-key",
        baseUrl: "https://secbaas-prod.example.test",
      },
    },
    evolveScriptPaths: {
      dev: "/runner/dev.sh",
      pre: "/runner/pre.sh",
      prod: "/runner/prod.sh",
    },
    commandTenant: "team_claw",
    commandTimeoutSeconds: 30,
  };
}

function runtimeContext(provider = "baas"): AgentToolContext {
  return {
    taskId: "REPAIR-001",
    issue: {
      traceId: null,
      relatedTaskId: null,
      errorText: null,
      timeRange: { from: 1_786_000_000, to: 1_786_000_060 },
    },
    target: {
      environment: "pre",
      ownerId: "user-001",
      botId: "bot-001",
      botType: "personal",
      botStatus: "active",
      bindingId: "binding-001",
      bindingStatus: "active",
      provider,
      deviceId: "BOT-runtime-001",
      ...(provider === "arca" ? { sandboxId: "ARCA-SANDBOX-123" } : {}),
      observedAt: "2026-08-19T00:00:00.000Z",
      source: "ocb_backend_current",
    },
  };
}

describe("buildAgentRuntimeCommand", () => {
  it("builds broad read-only filesystem commands with a remote realpath guard", () => {
    const command = buildAgentRuntimeCommand({
      operation: "fs_search",
      path: "/home/admin/.openclaw/openclaw.json",
      pattern: "gateway",
      maxMatches: 50,
    });
    expect(command).toContain("realpath -e");
    expect(command).not.toContain("/home/admin/*");
    expect(command).toContain("find -P");
    expect(command).toContain("grep -nHF");
    expect(command).toContain("NR <= 50");
  });

  it("allows absolute readable paths while still rejecting raw shell fields on structured operations", () => {
    expect(buildAgentRuntimeCommand({ operation: "fs_read", path: "/var/log/messages" }))
      .toContain("/var/log/messages");
    expect(() => buildAgentRuntimeCommand({
      operation: "process_list",
      command: "rm -rf /",
    } as never)).toThrow("不接受原始 command");
  });

  it("wraps an explicitly authorized diagnostic shell command without interpolating it", () => {
    const command = buildAgentRuntimeCommand({
      operation: "shell_exec",
      command: "git -C /tmp/openclaw status --short\nprintf 'done\\n'",
    });
    expect(command).toContain("base64 -d | bash --noprofile --norc");
    expect(command).not.toContain("git -C /tmp/openclaw");
  });

  it("only permits HTTP GET to a validated loopback port and path", () => {
    expect(buildAgentRuntimeCommand({ operation: "http_get", port: 18789, path: "/readyz" }))
      .toContain("http://127.0.0.1:18789/readyz");
    expect(() => buildAgentRuntimeCommand({
      operation: "http_get", port: 18789, path: "https://attacker.example",
    })).toThrow("loopback");
  });
});

describe("AgentRuntimeTool", () => {
  it("executes an approved ordinary-file action through the generic command transport", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "accepted\n",
        stderr: "",
        execution_time_ms: 9,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const command = "printf repaired > /home/admin/.openclaw/workspace/memory.md";

    await expect(new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } })).applyApprovedAction(
      runtimeContext(),
      {
        actionId: "update-memory",
        type: "container_command",
        summary: "更新普通记忆文件",
        risk: "接口参数错误会导致操作失败",
        verification: "通过原始业务路径验证结果",
        rollback: null,
        dependsOn: [],
        rollbackActionId: null,
        command,
      },
    )).resolves.toMatchObject({ status: "success", exitCode: 0 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body.cmd).toContain(Buffer.from(command, "utf8").toString("base64"));
    expect(body.cmd).toContain("base64 -d | bash");
  });

  it("executes once through the logical Bot route without resolving physical instances", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "ok\n",
        stderr: "",
        execution_time_ms: 12,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } })).inspect(
      runtimeContext(),
      { operation: "process_list" },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "https://secbaas-pre.example.test/api/v1/bots/team_claw/BOT-runtime-001/execute-command?device_affinity=REPAIR-001",
    );
    expect(String(url)).not.toContain("/devices");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body.timeout_seconds).toBe(30);
    expect(body.cmd).toContain("ps -ef");
    expect(result).toMatchObject({
      status: "success",
      operation: "process_list",
      target: {
        environment: "pre",
        bindingId: "binding-001",
        deviceId: "BOT-runtime-001",
      },
      exitCode: 0,
      stdout: "ok\n",
      durationMs: 12,
    });
  });

  it("keeps safe stdout lines when one line contains authentication material", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "document heading\nCookie: SSO=secret-value\nconfiguration details\n",
        stderr: "",
        execution_time_ms: 12,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } })).inspect(
      runtimeContext(),
      { operation: "fs_read", path: "/home/admin/reference.md" },
    );

    expect(result.stdout).toBe(
      "document heading\n[REDACTED_SECRET_TEXT]\nconfiguration details\n",
    );
    expect(JSON.stringify(result)).not.toContain("secret-value");
  });

  it("publishes typed locators from process output but not deep shell output", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 0,
        data: { exit_code: 0, stdout: "admin 101 node /opt/runtime/server.js\n", stderr: "", execution_time_ms: 4 },
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 0,
        data: { exit_code: 0, stdout: "/home/admin\n", stderr: "", execution_time_ms: 2 },
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const tool = new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } }));

    await expect(tool.inspect(runtimeContext(), { operation: "process_list" }))
      .resolves.toMatchObject({ evidenceLocators: ["/opt/runtime/server.js"] });
    await expect(tool.inspect(runtimeContext(), { operation: "shell_exec", command: "pwd" }))
      .resolves.toMatchObject({ evidenceLocators: [], shellObservedLocators: ["/home/admin"] });
  });

  it("publishes only direct regular children from fs_list", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: [
          "f\t/opt/logs/openclaw.log",
          "d\t/opt/logs/archive",
          "l\t/opt/logs/current",
          "f\t/opt/logs/nested/escape.log",
          "f\t/etc/passwd",
          "",
        ].join("\n"),
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } })).inspect(
      runtimeContext(),
      { operation: "fs_list", path: "/opt/logs" },
    )).resolves.toMatchObject({
      evidenceLocators: ["/opt/logs", "/opt/logs/openclaw.log", "/opt/logs/archive"],
    });
  });

  it("fails closed for a non-BaaS provider before making a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(new AgentRuntimeTool(new BotRuntimeClient({ providers: { baas: new BaasRuntimeProvider(baasConfig()) } })).inspect(
      runtimeContext("teclaw"),
      { operation: "port_list" },
    )).rejects.toMatchObject({ status: 422, code: "unsupported_runtime_provider" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("routes a legacy ARCA target through the injected server transport", async () => {
    const execute = vi.fn(async () => ({
      status: "success" as const,
      exitCode: 0,
      stdout: "arca-ok\n",
      stderr: "",
      durationMs: 8,
    }));
    const tool = new AgentRuntimeTool({ executeShell: execute });

    await expect(tool.inspect(
      runtimeContext("arca"),
      { operation: "process_list", pattern: "openclaw" },
    )).resolves.toMatchObject({
      status: "success",
      operation: "process_list",
      target: { sandboxId: "ARCA-SANDBOX-123" },
      stdout: "arca-ok\n",
    });
    expect(execute).toHaveBeenCalledWith(expect.objectContaining({
      target: expect.objectContaining({ environment: "pre", bindingId: "binding-001", sandboxId: "ARCA-SANDBOX-123" }),
      command: expect.stringContaining("ps -ef"),
    }));
  });
});


it.each([
  "mcporter config remove duplicate", "openclaw cron remove duplicate", "openclaw gateway call cron.remove --params '{}'",
  "curl -X DELETE http://127.0.0.1:20003/api/cron/duplicate", "printf changed > /workspace/skills/review/SKILL.md",
  "printf '{}' > /home/admin/.mcporter/mcporter.json", "rm /home/admin/.openclaw/cron/jobs.json",
])("rejects an approved Shell bypass before dispatch: %s", async command => {
  const executeShell = vi.fn();
  const tool = new AgentRuntimeTool({ executeShell });
  await expect(tool.applyApprovedAction(runtimeContext(), {
    actionId: "change", type: "container_command", command,
    summary: "调整环境", risk: "局部", verification: "核对", rollback: null,
  })).rejects.toThrow();
  expect(executeShell).not.toHaveBeenCalled();
});
it("does not permit the deep-diagnostic Shell to perform interface operations", async () => {
  const executeShell = vi.fn();
  await expect(new AgentRuntimeTool({ executeShell }).inspect(runtimeContext(), {
    operation: "shell_exec", command: "openclaw cron list",
  })).rejects.toMatchObject({ code: "adapter_tool_required" });
  expect(executeShell).not.toHaveBeenCalled();
});

it("excludes transcripts from recursive environment searches and guards resolved symlink paths", async () => {
  const { mkdtemp, mkdir, writeFile, symlink, rm } = await import('node:fs/promises');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const directory = await mkdtemp(join(tmpdir(), 'recovery-environment-'));
  try {
    const sessions = join(directory, '.openclaw/agents/main/sessions');
    await mkdir(sessions, { recursive: true });
    await writeFile(join(sessions, 'original.jsonl'), 'match future-answer\n');
    await writeFile(join(directory, 'config.txt'), 'match environment-setting\n');
    await symlink(join(sessions, 'original.jsonl'), join(directory, 'alias.txt'));
    // Adapt the Linux runtime's realpath flags for the macOS test host.
    const run = (command: string) => promisify(execFile)('/bin/bash', ['-c', 'realpath(){ command realpath "$3"; }; ' + command]);
    const search = await run(buildAgentRuntimeCommand({ operation: 'fs_search', path: directory, pattern: 'match' }, 'FRAME', { allowSessionHistory: false }));
    expect(search.stdout).toContain('environment-setting');
    expect(search.stdout).not.toContain('future-answer');
    await expect(run(buildAgentRuntimeCommand({ operation: 'fs_read', path: join(directory, 'alias.txt') }, 'FRAME', { allowSessionHistory: false }))).rejects.toMatchObject({ code: 45 });
  } finally { await rm(directory, { recursive: true, force: true }); }
});
