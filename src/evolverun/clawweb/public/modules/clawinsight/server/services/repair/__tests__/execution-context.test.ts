import { spawnSync } from "node:child_process";
import { afterEach, expect, it, vi } from "vitest";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import type { RepairTaskContext } from "../contracts.js";
import type { ArcaCommandTransport } from "../arca-command-transport.js";
import { buildRepairRuntimeCommand, RepairRuntimeTool } from "../runtime-tool.js";

afterEach(() => vi.unstubAllGlobals());

function baasConfig(): ResolvedBaasConfig {
  return {
    apiKey: "<prod-api-key>",
    iamtoken: "test-iam-token",
    baseUrl: "https://secbaas-prod.example.test",
    environments: {
      pre: {
        apiKey: "<pre-api-key>",
        baseUrl: "https://secbaas-pre.example.test",
      },
      prod: {
        apiKey: "<prod-api-key>",
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


it("executes the fixed probe and observes the actual local identity without other env", () => {
  const command = buildRepairRuntimeCommand({ operation: "execution_context" });
  const result = spawnSync("/bin/sh", ["-c", command], {
    encoding: "utf8", cwd: process.cwd(),
    env: { PATH: "/usr/bin:/bin", REPAIR_SECRET_SENTINEL: "must-not-appear" },
  });
  expect(result.status, result.stderr).toBe(0);
  const actual = JSON.parse(result.stdout);
  expect(actual).toMatchObject({ schemaVersion: "repair-execution-context/v1",
    scope: "this_probe_process_only", uid: process.getuid!(), effectiveUid: process.geteuid!(),
    gid: process.getgid!(), effectiveGid: process.getegid!(), path: "/usr/bin:/bin",
    pathStatus: "observed", cwd: process.cwd(), groupsStatus: "observed" });
  expect(actual.supplementaryGroups.every((gid: number) => Number.isInteger(gid) && gid >= 0)).toBe(true);
  expect(actual.pid).toBeGreaterThan(0);
  expect(result.stdout).not.toContain("must-not-appear");
  expect(actual).not.toHaveProperty("executionContextBinding");
  if (process.platform !== "linux") expect(actual.processIdentity).toMatchObject({ status: "unsupported_platform", namespaces: null });
});

it("preserves oversized PATH as unknown rather than silently truncating", () => {
  const result = spawnSync("/bin/sh", ["-c", buildRepairRuntimeCommand({ operation: "execution_context" })], {
    encoding: "utf8", env: { PATH: "/usr/bin:/bin:" + "x".repeat(16384) },
  });
  expect(result.status, result.stderr).toBe(0);
  expect(JSON.parse(result.stdout)).toMatchObject({ path: null, pathStatus: "too_large" });
});

it.each(["pid", "path", "env", "user", "command"])("rejects caller-controlled %s before transport", async field => {
  const execute = vi.fn();
  await expect(new RepairRuntimeTool(baasConfig(), { execute } as unknown as ArcaCommandTransport)
    .inspect(runtimeContext("arca"), { operation: "execution_context", [field]: "forbidden" } as never)).rejects.toThrow();
  expect(execute).not.toHaveBeenCalled();
});

it.each(["baas", "arca"])("binds source from controller and retains admin wrapper on %s", async provider => {
  const stdout = JSON.stringify({ executionContextBinding: { actor: "original_openclaw_exec", targetFingerprint: "forged" }, path: "/untrusted/path" });
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: 0, data: { exit_code: 0, stdout, stderr: "" } }), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  const execute = vi.fn(async () => ({ status: "success", stdout, stderr: "", exitCode: 0, durationMs: 1 }));
  const result = await new RepairRuntimeTool(baasConfig(), { execute } as unknown as ArcaCommandTransport)
    .inspect(runtimeContext(provider), { operation: "execution_context" });
  expect(result).toMatchObject({ status: "success", evidenceLocators: [], shellObservedLocators: [], observation: { executionContextBinding: {
    entry: "runtime_inspect.execution_context", actor: "current_repair_probe", taskId: "REPAIR-001", stepId: "STEP-001", attempt: 1,
    targetFingerprint: "target-fingerprint", runtimeTargetVersion: 1, initialRuntimeUserPolicy: "admin",
    establishesOriginalExecContext: false, establishesFutureActionContext: false, establishesWritePermission: false,
  } } });
  const dispatched = provider === "arca" ? JSON.stringify(execute.mock.calls) : JSON.stringify(fetchMock.mock.calls);
  expect(dispatched).toContain("su admin -c");
  expect(dispatched).toContain("python3 -I -S -B");
});

it.each(["baas", "arca"])("does not turn unavailable Python into a successful observation on %s", async provider => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: 0, data: { exit_code: 127, stdout: "", stderr: "python3: not found" } }), { headers: { "Content-Type": "application/json" } })));
  const execute = vi.fn(async () => ({ status: "failed", stdout: "", stderr: "python3: not found", exitCode: 127, durationMs: 1 }));
  const result = await new RepairRuntimeTool(baasConfig(), { execute } as unknown as ArcaCommandTransport)
    .inspect(runtimeContext(provider), { operation: "execution_context" });
  expect(result).toMatchObject({ status: "failed", exitCode: 127, stdout: "", evidenceLocators: [], observation: { executionContextBinding: { scope: "this_probe_process_only" } } });
  expect(JSON.stringify(result)).not.toContain('"effectiveUid"');
});
