import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import type { RepairTaskContext } from "../contracts.js";
import {
  assertRepairPlanShellIsObservational,
  buildRepairRuntimeCommand,
  buildRepairRuntimeUserCommand,
  RepairRuntimeTool,
} from "../runtime-tool.js";
import type { ArcaCommandTransport } from "../arca-command-transport.js";

afterEach(() => vi.unstubAllGlobals());

function baasConfig(): ResolvedBaasConfig {
  return {
    apiKey: "prod-api-key",
    iamtoken: "test-iam-token",
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
  it.each([
    { mode: "literal" as const, pattern: "error|ERROR|Error", expected: "", status: 0 },
    { mode: "regex" as const, pattern: "error|ERROR|Error", expected: "1:ERROR tool unavailable\n", status: 0 },
    { mode: "regex" as const, pattern: "missing", expected: "", status: 0 },
    { mode: "regex" as const, pattern: "[", expected: "", status: 2 },
    { mode: "literal" as const, pattern: "literal'$(printf injected)", expected: "2:literal'$(printf injected)\n", status: 0 },
  ])("executes the chosen search semantics ($mode, $pattern)", ({ mode, pattern, expected, status }) => {
    const directory = mkdtempSync(join(tmpdir(), "repair-search-"));
    try {
      const path = join(directory, "sample.txt");
      writeFileSync(path, "ERROR tool unavailable\nliteral'$(printf injected)\n");
      const command = buildRepairRuntimeCommand({ operation: "fs_search", path, pattern, matchMode: mode });
      // macOS realpath lacks GNU -e. Supply only the already-created fixture
      // path; run the generated guards, grep, quoting and pipeline unchanged.
      const portable = command.replace(/^p=\$\(realpath -e -- .*?\) \|\| exit 44;/, 'p="$1";');
      const result = spawnSync("sh", ["-c", portable, "search-test", path], { encoding: "utf8" });
      expect(result.status).toBe(status);
      expect(result.stdout).toBe(expected);
      if (status === 2) expect(result.stderr.length).toBeGreaterThan(0);
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });

  it("rejects an invalid mode before any transport call", () => {
    expect(() => buildRepairRuntimeCommand({ operation: "fs_search", path: "/verified/file", pattern: "error", matchMode: "auto" } as never)).toThrow("matchMode");
  });

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

  it("builds a bounded process identity probe without reading process environment", () => {
    const command = buildRepairRuntimeCommand({ operation: "process_detail", pid: 1610 });
    expect(command).toContain("OBSERVED_AT_UTC");
    expect(command).toContain("/proc/$pid/exe");
    expect(command).toContain("/proc/$pid/cmdline");
    expect(command).toContain("/proc/$pid/cwd");
    expect(command).not.toContain("/proc/$pid/environ");
    expect(command).toContain("command -v --");
    expect(command).toContain("realpath -e --");
    expect(() => buildRepairRuntimeCommand({ operation: "process_detail", pid: 0 }))
      .toThrow("pid 必须在 1 到 4194304 之间");
  });

  it("wraps an explicitly authorized diagnostic shell command without interpolating it", () => {
    const command = buildRepairRuntimeCommand({
      operation: "shell_exec",
      command: "git -C /tmp/openclaw status --short\nprintf 'done\\n'",
    });
    expect(command).toContain("base64 -d | bash --noprofile --norc");
    expect(command).not.toContain("git -C /tmp/openclaw");
  });

  it("rejects direct managed-runtime CLI invocation during Plan without blocking source inspection", () => {
    const context = runtimeContext();
    expect(() => assertRepairPlanShellIsObservational(context, {
      operation: "shell_exec",
      command: "openclaw cron list 2>&1",
    })).toThrow("未执行该命令");
    expect(() => assertRepairPlanShellIsObservational(context, {
      operation: "shell_exec",
      command: "printf ready && /usr/local/bin/cfuse status",
    })).toThrow("未执行该命令");
    expect(() => assertRepairPlanShellIsObservational(context, {
      operation: "shell_exec",
      command: "command -v openclaw && rg -n 'openclaw' /opt/source",
    })).not.toThrow();
    expect(() => assertRepairPlanShellIsObservational(
      { ...context, phase: "repair_apply" },
      { operation: "shell_exec", command: "openclaw cron list" },
    )).not.toThrow();
  });

  it("only permits HTTP GET to a validated loopback port and path", () => {
    expect(buildRepairRuntimeCommand({ operation: "http_get", port: 18789, path: "/readyz" }))
      .toContain("http://127.0.0.1:18789/readyz");
    expect(() => buildRepairRuntimeCommand({
      operation: "http_get", port: 18789, path: "https://attacker.example",
    })).toThrow("loopback");
  });
});

describe("buildRepairRuntimeUserCommand", () => {
  it("runs root transports as admin and rejects every other inherited user", () => {
    const command = buildRepairRuntimeUserCommand("id -un && touch /tmp/repair-owned");

    expect(command).toContain('if [ "$repair_uid" = "0" ]');
    expect(command).toContain("su admin -c");
    expect(command).toContain('test "$(id -un)" = admin');
    expect(command).toContain("HOME=/home/admin");
    expect(command).toContain("umask 077");
    expect(command).toContain("id -un && touch /tmp/repair-owned");
  });
});

describe("RepairRuntimeTool", () => {
  it.each(["baas", "arca"])("reports actual regex mode on both transports (%s)", async provider => {
    const stdout = "1:ERROR tool unavailable\n";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { exit_code: 0, stdout, stderr: "", execution_time_ms: 1 },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const arca = { execute: vi.fn(async () => ({ status: "success", stdout, stderr: "", exitCode: 0, durationMs: 1 })) } as unknown as ArcaCommandTransport;
    const result = await new RepairRuntimeTool(baasConfig(), arca).inspect(runtimeContext(provider), {
      operation: "fs_search", path: "/verified/sample.txt", pattern: "error|ERROR", matchMode: "regex",
    });
    expect(result).toMatchObject({ stdout, searchCoverage: { matchMode: "regex", patternSyntax: "POSIX ERE" } });
  });

  it.each([
    { name: "below the row cap", stdout: "1:match\n", limit: 2, reached: false, truncated: false, count: 1 },
    { name: "exactly at the row cap", stdout: "1:match\n2:match\n", limit: 2, reached: true, truncated: false, count: 2 },
    { name: "output truncation before the row cap", stdout: "1:match\n[TRUNCATED]\n", limit: 5, reached: false, truncated: true, count: 1 },
  ])("preserves search completeness uncertainty: $name", async ({ stdout, limit, reached, truncated, count }) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { exit_code: 0, stdout, stderr: "", execution_time_ms: 1 },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const result = await new RepairRuntimeTool(baasConfig()).inspect(runtimeContext(), {
      operation: "fs_search", path: "/verified/runtime.txt", pattern: "match", maxMatches: limit,
    });
    expect(result).toMatchObject({
      searchCoverage: { maxMatches: limit, matchingLinesBeforeTimeFilter: count,
        limitReached: reached, outputTruncated: truncated, remainingMatches: "unknown" },
    });
    // Merely reaching the cap does not prove more matches exist.
    expect(result.stdout).toBe(stdout);
  });

  it.each(["baas", "arca"])("retains a reached search cap after incident filtering removes every row (%s)", async provider => {
    const stdout = '1:{"time":"2026-09-04T11:41:27.000Z","message":"later"}\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: 0, data: { exit_code: 0, stdout, stderr: "", execution_time_ms: 1 },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const arca = { execute: vi.fn(async () => ({ status: "success", stdout, stderr: "", exitCode: 0, durationMs: 1 })) } as unknown as ArcaCommandTransport;
    const result = await new RepairRuntimeTool(baasConfig(), arca).inspect(runtimeContext(provider), {
      operation: "fs_search", path: "/opt/logs/worker.log", pattern: "message", maxMatches: 1,
    });
    expect(result.stdout).toBe("");
    expect(result).toMatchObject({
      searchCoverage: { maxMatches: 1, matchingLinesBeforeTimeFilter: 1, limitReached: true, remainingMatches: "unknown" },
      observation: { temporalScope: { retainedLines: 0, excludedLines: 1 } },
    });
  });

  it("excludes timestamped log records outside the incident window", async () => {
    const inWindow = JSON.stringify({
      time: "2026-08-06T07:07:00.000Z",
      message: "incident failure",
    });
    const afterIncident = JSON.stringify({
      time: "2026-09-04T11:41:27.000Z",
      message: "probe-created EADDRINUSE",
    });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: `${inWindow}\n${afterIncident}\n`,
        stderr: "",
        execution_time_ms: 3,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_read", path: "/opt/logs/openclaw.log" },
    );

    expect(result.stdout).toContain("incident failure");
    expect(result.stdout).not.toContain("EADDRINUSE");
    expect(result).toMatchObject({
      observation: {
        temporalScope: {
          mode: "incident_window",
          timestampedLines: 2,
          retainedLines: 1,
          excludedLines: 1,
        },
      },
    });
  });

  it("time-scopes a structured search across a log directory", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: [
          "/opt/logs/a.log:4:{\"time\":\"2026-08-06T07:07:10.000Z\",\"message\":\"incident\"}",
          "/opt/logs/b.log:9:{\"time\":\"2026-09-04T11:41:27.000Z\",\"message\":\"later probe\"}",
          "",
        ].join("\n"),
        stderr: "",
        execution_time_ms: 3,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_search", path: "/opt/logs", pattern: "message" },
    );

    expect(result.stdout).toContain("incident");
    expect(result.stdout).not.toContain("later probe");
    expect(result).toMatchObject({
      observation: { temporalScope: { retainedLines: 1, excludedLines: 1 } },
    });
  });

  it("retries one transient local-login response for a structured Plan read", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        "<!doctype html><html><title>Login</title></html>",
        { status: 200, headers: { "Content-Type": "text/html" } },
      ))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 0,
        data: { exit_code: 0, stdout: "ok\n", stderr: "", execution_time_ms: 3 },
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_read", path: "/home/admin/example.txt" },
    )).resolves.toMatchObject({ status: "success", stdout: "ok\n" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not auto-retry an arbitrary diagnostic shell after local-login rejection", async () => {
    const fetchMock = vi.fn(async () => new Response(
      "<!doctype html><html><title>Login</title></html>",
      { status: 200, headers: { "Content-Type": "text/html" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "shell_exec", command: "uname -a" },
    )).rejects.toThrow("服务返回登录页");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

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
    expect(JSON.parse(String(init?.body))).toEqual({
      cmd: buildRepairRuntimeUserCommand("printf 'OBSERVED_AT_UTC\\t'; date -u '+%Y-%m-%dT%H:%M:%SZ'; TZ=UTC LC_ALL=C ps -eo pid=,ppid=,lstart=,etime=,user=,args="),
      timeout_seconds: 30,
    });
    expect(result).toMatchObject({
      status: "success",
      operation: "process_list",
      observation: {
        kind: "repair_probe",
        incidentWindow: {
          from: "2026-08-06T07:06:40.000Z",
          to: "2026-08-06T07:07:40.000Z",
        },
        causality: expect.stringContaining("不能在缺少独立关联证据时解释更早发生的故障"),
      },
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

  it("publishes typed evidence locators from redacted command output", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "admin 101 node /opt/runtime/server.js\n",
        stderr: "at load (/opt/runtime/loader.js:42:7)\n",
        execution_time_ms: 4,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "process_list" },
    );

    expect(result.evidenceLocators).toEqual([
      "/opt/runtime/server.js",
      "/opt/runtime/loader.js",
    ]);
  });

  it("does not let deep shell output mint filesystem evidence locators", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "/home/admin\n/mnt/sys\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "shell_exec", command: "pwd; printf '%s\\n' /mnt/sys" },
    );

    expect(result.evidenceLocators).toEqual([]);
    expect(result.shellObservedLocators).toEqual(["/home/admin", "/mnt/sys"]);
  });

  it("publishes executable, cwd and argv paths from process detail", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: [
          "PID\t1610",
          "EXE\t/usr/bin/node",
          "CWD\t/home/admin",
          "ARG\t/usr/bin/node",
          "ARG\t/opt/openclaw/dist/index.js",
          "COMMAND\t/usr/lib/node_modules/openclaw/openclaw.mjs",
          "",
        ].join("\n"),
        stderr: "",
        execution_time_ms: 3,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "process_detail", pid: 1610 },
    )).resolves.toMatchObject({
      status: "success",
      operation: "process_detail",
      evidenceLocators: [
        "/usr/bin/node",
        "/home/admin",
        "/opt/openclaw/dist/index.js",
        "/usr/lib/node_modules/openclaw/openclaw.mjs",
      ],
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).cmd).not.toContain("environ");
  });

  it("publishes a resolved symlink target as an unverified shell-observed locator", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: [
          "/usr/bin/openclaw: symbolic link to ../lib/node_modules/openclaw/openclaw.mjs",
          "lrwxrwxrwx 1 root root 41 /usr/bin/openclaw -> ../lib/node_modules/openclaw/openclaw.mjs",
          "",
        ].join("\n"),
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "shell_exec", command: "file /usr/bin/openclaw && ls -la /usr/bin/openclaw" },
    );

    expect(result.evidenceLocators).toEqual([]);
    expect(result.shellObservedLocators).toEqual([
      "/usr/bin/openclaw",
      "/usr/lib/node_modules/openclaw/openclaw.mjs",
    ]);
  });

  it("returns a successfully confirmed structured path as an evidence locator", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "directory\t755\tadmin:admin\t4096\t2026-09-01 00:00:00\t/tmp/openclaw\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_stat", path: "/tmp/openclaw" },
    );

    expect(result.evidenceLocators).toContain("/tmp/openclaw");
    expect(result.shellObservedLocators).toEqual([]);
  });

  it("publishes the canonical fs_stat target instead of a requested symlink alias", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "regular file\t600\tadmin:admin\t12\t2026-09-04 00:00:00.000000000 +0000\t/opt/runtime/config.json\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_stat", path: "/home/admin/config-link" },
    );

    expect(result.evidenceLocators).toEqual(["/opt/runtime/config.json"]);
    expect(result.evidenceLocators).not.toContain("/home/admin/config-link");
  });

  it("does not publish a canonical fs_stat target that resolves to a broad root", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "directory\t755\troot:root\t4096\t2026-09-04 00:00:00.000000000 +0000\t/\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_stat", path: "/home/admin/root-link" },
    );

    expect(result.evidenceLocators).toEqual([]);
  });

  it("publishes only direct regular children from a server-owned fs_list", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: [
          "f\t/opt/logs/openclaw_err.log",
          "d\t/opt/logs/tracelog",
          "l\t/opt/logs/current",
          "f\t/opt/logs/nested/escape.log",
          "f\t/opt/logs/../logs/normalized-away.log",
          "f\t/opt/logs/control-\u0001.log",
          "f\t/etc/passwd",
          "not-a-listing\t/opt/logs/invented",
          "",
        ].join("\n"),
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_list", path: "/opt/logs" },
    );

    expect(result.evidenceLocators).toEqual([
      "/opt/logs",
      "/opt/logs/openclaw_err.log",
      "/opt/logs/tracelog",
    ]);
  });

  it("does not mint locators from a structured search pattern or output text", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "/verified/root/file.txt:1:/invented/from-output\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_search", path: "/verified/root", pattern: "/invented/from-pattern" },
    );

    expect(result.evidenceLocators).toEqual(["/verified/root"]);
    expect(result.evidenceLocators).not.toContain("/invented/from-pattern");
    expect(result.evidenceLocators).not.toContain("/invented/from-output");
  });

  it("does not mint locators from structured file contents", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      code: 0,
      data: {
        exit_code: 0,
        stdout: "configured=/invented/from-file\n",
        stderr: "",
        execution_time_ms: 2,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new RepairRuntimeTool(baasConfig()).inspect(
      runtimeContext(),
      { operation: "fs_read", path: "/verified/config.txt", startLine: 1, lines: 20 },
    );

    expect(result.evidenceLocators).toEqual(["/verified/config.txt"]);
    expect(result.evidenceLocators).not.toContain("/invented/from-file");
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

  it("routes a legacy ARCA target through the injected server transport", async () => {
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
    }));
  });
});
