import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResolvedBaasConfig } from "../../../db.js";
import type { RepairTaskContext } from "../contracts.js";
import { buildRepairRuntimeCommand, RepairRuntimeTool } from "../runtime-tool.js";
import type { ArcaCommandTransport } from "../arca-command-transport.js";

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

function runtimeContext(provider = "baas"): RepairTaskContext {
  return {
    schemaVersion: "ce-repair/v1",
    taskId: "REPAIR-001",
    stepId: "STEP-001",
    attempt: 1,
    phase: "repair_plan",
    issue: {
      symptom: "Bot 无法响应",
      traceId: null,
      relatedTaskId: null,
      errorText: null,
      timeRange: { from: 1_786_000_000, to: 1_786_000_060 },
    },
    authorizationScope: {
      actorUserId: "user-001",
      ownerId: "user-001",
      botId: "bot-001",
      environment: "pre",
    },
    authorizationScopeDigest: "scope-digest",
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
    targetFingerprint: "target-fingerprint",
    runtimeTargetVersion: 1,
  };
}

describe("buildRepairRuntimeCommand", () => {
  it("builds broad read-only filesystem commands with a remote realpath guard", () => {
    const command = buildRepairRuntimeCommand({
      operation: "fs_search",
      path: "/home/admin/.openclaw/openclaw.json",
      pattern: "gateway",
      maxMatches: 50,
    });
    expect(command).toContain("realpath -e");
    expect(command).not.toContain("/home/admin/*");
    expect(command).toContain("find -P");
    expect(command).toContain("grep -nHF");
    expect(command).toContain("head -n 50");
  });

  it("allows absolute readable paths while still rejecting raw shell fields on structured operations", () => {
    expect(buildRepairRuntimeCommand({ operation: "fs_read", path: "/var/log/messages" }))
      .toContain("/var/log/messages");
    expect(() => buildRepairRuntimeCommand({
      operation: "process_list",
      command: "rm -rf /",
    } as never)).toThrow("不接受原始 command");
  });

  it("wraps an explicitly authorized diagnostic shell command without interpolating it", () => {
    const command = buildRepairRuntimeCommand({
      operation: "shell_exec",
      command: "git -C /tmp/openclaw status --short\nprintf 'done\\n'",
    });
    expect(command).toContain("base64 -d | bash --noprofile --norc");
    expect(command).not.toContain("git -C /tmp/openclaw");
  });

  it("only permits HTTP GET to a validated loopback port and path", () => {
    expect(buildRepairRuntimeCommand({ operation: "http_get", port: 18789, path: "/readyz" }))
      .toContain("http://127.0.0.1:18789/readyz");
    expect(() => buildRepairRuntimeCommand({
      operation: "http_get", port: 18789, path: "https://attacker.example",
    })).toThrow("loopback");
  });
});

describe("RepairRuntimeTool", () => {
  it("executes an approved container-local Engine API call through the generic command transport", async () => {
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
    const command = "curl -fsS -X POST http://127.0.0.1:18789/api/engine/action";

    await expect(new RepairRuntimeTool(baasConfig()).applyApprovedAction(
      runtimeContext(),
      {
        actionId: "call-container-engine-api",
        type: "container_command",
        summary: "调用容器内 Engine 接口",
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

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
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
    expect(JSON.parse(String(init?.body))).toEqual({ cmd: "ps -ef", timeout_seconds: 30 });
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

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
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
    const tool = new RepairRuntimeTool(baasConfig());

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

    await expect(new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_list", path: "/opt/logs" },
    )).resolves.toMatchObject({
      evidenceLocators: ["/opt/logs", "/opt/logs/openclaw.log", "/opt/logs/archive"],
    });
  });

  it("fails closed for a non-BaaS provider before making a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext("teclaw"),
      { operation: "port_list" },
    )).rejects.toMatchObject({ status: 422, code: "unsupported_runtime_provider" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("routes a legacy ARCA target through the injected owner-relay transport", async () => {
    const execute = vi.fn(async () => ({
      status: "success" as const,
      exitCode: 0,
      stdout: "arca-ok\n",
      stderr: "",
      durationMs: 8,
    }));
    const tool = new RepairRuntimeTool(baasConfig(), { execute } as unknown as ArcaCommandTransport);

    await expect(tool.inspect(
      runtimeContext("arca"),
      { operation: "process_list", pattern: "openclaw" },
      { Cookie: "SESSION=owner", "x-user-id": "user-001" },
    )).resolves.toMatchObject({
      status: "success",
      operation: "process_list",
      target: { sandboxId: "ARCA-SANDBOX-123" },
      stdout: "arca-ok\n",
    });
    expect(execute).toHaveBeenCalledWith(expect.objectContaining({
      environment: "pre",
      bindingId: "binding-001",
      sandboxId: "ARCA-SANDBOX-123",
      authHeaders: { Cookie: "SESSION=owner", "x-user-id": "user-001" },
    }));
  });
});
