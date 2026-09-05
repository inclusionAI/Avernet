import { once } from "node:events";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RepairTaskService } from "../../services/repair/repair-runtime.js";
import { RepairError } from "../../services/repair/errors.js";
import {
  type VerifiedRequestActor,
} from "../../services/repair/request-actor.js";
import type { RepairWorkloadVerifier } from "../../services/repair/workload-verifier.js";
import { createRepairRouter } from "../repair.js";

let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

const createTask = vi.fn();
const getTask = vi.fn();
const getStepPlan = vi.fn();
const setTaskShared = vi.fn();
const terminateTask = vi.fn();
const resumeTask = vi.fn();
const decidePlan = vi.fn();
const decideResult = vi.fn();
const fulfillToolCall = vi.fn();
const submitCfuseAuthCode = vi.fn();
const bootstrap = vi.fn();
const heartbeat = vi.fn();
const claimDecision = vi.fn();
const refreshArtifactUpload = vi.fn();
const requestCfuseLogin = vi.fn();
const takeCfuseAuthCode = vi.fn();
const reportCfuseLogin = vi.fn();
const recordSemanticConclusion = vi.fn();

const service = {
  createTask,
  getTask,
  getStepPlan,
  setTaskShared,
  terminateTask,
  resumeTask,
  decidePlan,
  decideResult,
  fulfillToolCall,
  submitCfuseAuthCode,
  bootstrap,
  heartbeat,
  claimDecision,
  refreshArtifactUpload,
  requestCfuseLogin,
  takeCfuseAuthCode,
  reportCfuseLogin,
  recordSemanticConclusion,
} as unknown as RepairTaskService;

async function startRouter(
  resolveActor?: () => Promise<VerifiedRequestActor | null>,
  workloadVerifier: RepairWorkloadVerifier | null = { verify: vi.fn() },
): Promise<void> {
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.isClawEvolveAdmin = req.header("X-Test-Evolve-Admin") === "true";
    next();
  });
  app.use("/api/repair/v1", createRepairRouter({
    service,
    workloadVerifier,
    ...(resolveActor ? { resolveActor } : {}),
  }));
  const instance = app.listen(0, "127.0.0.1");
  await once(instance, "listening");
  server = instance;
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind a TCP port");
  baseUrl = `http://127.0.0.1:${address.port}`;
}

beforeEach(() => {
  server = null;
  baseUrl = "";
  vi.clearAllMocks();
  createTask.mockResolvedValue({ taskId: "REPAIR-test", status: "running" });
  getTask.mockResolvedValue({ taskId: "REPAIR-test", status: "running" });
  getStepPlan.mockResolvedValue({
    taskId: "REPAIR-test",
    step: {
      stepId: "REPAIR-test-PLAN-1",
      stepNo: 1,
      attempt: 1,
      status: "succeeded",
      artifactDigest: "a".repeat(64),
    },
    source: "history",
    readOnly: true,
    approvable: false,
    plan: { schemaVersion: "ce-repair-plan/v2", diagnosis: {}, actions: [] },
  });
  setTaskShared.mockResolvedValue({
    taskId: "REPAIR-test",
    status: "running",
    shared: true,
    canOperate: true,
    canManageShare: true,
    toolCalls: [],
  });
  terminateTask.mockResolvedValue({
    taskId: "REPAIR-test",
    status: "canceled",
    termination: { status: "remote_stopped", aisJobId: "job-1" },
  });
  resumeTask.mockResolvedValue({ taskId: "REPAIR-test", status: "running" });
  decidePlan.mockResolvedValue({ taskId: "REPAIR-test", status: "running" });
  decideResult.mockResolvedValue({ taskId: "REPAIR-test", status: "running" });
  fulfillToolCall.mockResolvedValue({ toolCallId: "rtc-1", status: "succeeded" });
  submitCfuseAuthCode.mockResolvedValue({
    toolCallId: "rtc-login",
    stepId: "STEP-1",
    phase: "repair_plan",
    toolName: "cfuse_login",
    operation: "authorize",
    status: "executing",
    cfuseLoginUrl: "https://codefuse.antgroup-inc.cn/cloud/oauth?port=31337",
    requiresBrowserRelay: false,
  });
  bootstrap.mockResolvedValue({ taskId: "REPAIR-test", stepId: "STEP-1" });
  heartbeat.mockResolvedValue({ ok: true });
  claimDecision.mockResolvedValue({ status: "claimed", stepId: "STEP-2" });
  refreshArtifactUpload.mockResolvedValue({
    artifact: {
      name: "plan",
      objectKey: "evolution/REPAIR-test/repair/STEP-1/plan.json",
      contentType: "application/json; charset=utf-8",
      putUrl: "https://oss.example/plan?signature=secret",
    },
    expiresInSeconds: 86_400,
  });
  requestCfuseLogin.mockResolvedValue({ toolCallId: "rtc-login", status: "pending" });
  takeCfuseAuthCode.mockResolvedValue({ toolCallId: "rtc-login", status: "available", authCode: "one-time" });
  reportCfuseLogin.mockResolvedValue({ toolCallId: "rtc-login", status: "succeeded" });
  recordSemanticConclusion.mockResolvedValue({ toolCallId: "rtc-conclusion", status: "succeeded" });
});

afterEach(async () => {
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
});

describe("Repair verified actor boundary", () => {
  it("forwards verified administrator status only to read endpoints", async () => {
    await startRouter(async () => ({ userId: "global-admin", source: "request" }));

    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test`, {
      headers: { "X-Test-Evolve-Admin": "true" },
    });
    expect(response.status).toBe(200);
    expect(getTask).toHaveBeenCalledWith("global-admin", "REPAIR-test", true);
  });

  it("uses the default ClawWeb request-user identity path", async () => {
    await startRouter();

    const response = await fetch(`${baseUrl}/api/repair/v1/tasks`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=current-cookie",
        "X-Staff-Id": "request-owner",
        "X-User-Id": "fallback-owner",
      },
      body: JSON.stringify({ botId: "default", targetEnvironment: "pre", symptom: "MCP missing" }),
    });

    expect(response.status).toBe(202);
    expect(createTask).toHaveBeenCalledWith(expect.objectContaining({
      actorUserId: "request-owner",
      authHeaders: {
        cookie: "SSO_SESSION=current-cookie",
        "x-user-id": "request-owner",
      },
    }));
  });

  it("derives owner from the request actor and forwards its identity plus the raw Cookie", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));

    const response = await fetch(`${baseUrl}/api/repair/v1/tasks`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=verified-cookie",
        Authorization: "Bearer must-not-reach-ocb",
        "X-User-Id": "forged-user",
        "X-Staff-Id": "another-forged-user",
        "Idempotency-Key": "ignored-by-repair",
      },
      body: JSON.stringify({
        targetEnvironment: "pre",
        ownerId: "attacker-supplied-owner",
        botId: "default",
        symptom: "MCP missing",
        agentMode: "openclaw",
        llmUseDefault: false,
        llmModel: "GLM-5.2",
        llmApiKey: "route-only-key",
      }),
    });

    expect(response.status).toBe(202);
    expect(createTask).toHaveBeenCalledWith({
      actorUserId: "297189",
      authHeaders: { cookie: "SSO_SESSION=verified-cookie", "x-user-id": "297189" },
      body: expect.objectContaining({
        botId: "default",
        symptom: "MCP missing",
        agentMode: "openclaw",
        llmUseDefault: false,
        llmModel: "GLM-5.2",
        llmApiKey: "route-only-key",
      }),
    });
    expect(createTask.mock.calls[0][0]).not.toHaveProperty("idempotencyKey");
  });

  it("does not call the service when the session is invalid", async () => {
    await startRouter(async () => null);
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "forged-user" },
      body: JSON.stringify({}),
    });
    expect(response.status).toBe(401);
    expect(createTask).not.toHaveBeenCalled();
  });

  it("uses the verified actor when reading a CE Repair Task", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test`, {
      headers: { Cookie: "SSO_SESSION=verified-cookie", "X-User-Id": "forged-user" },
    });
    expect(response.status).toBe(200);
    expect(getTask).toHaveBeenCalledWith("297189", "REPAIR-test", false);
  });

  it("returns a historical Plan only for the verified actor and disables caching", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));

    const response = await fetch(
      `${baseUrl}/api/repair/v1/tasks/REPAIR-test/steps/REPAIR-test-PLAN-1/plan`,
      { headers: { Cookie: "SSO_SESSION=verified-cookie", "X-User-Id": "forged-user" } },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(await response.json()).toMatchObject({
      taskId: "REPAIR-test",
      source: "history",
      readOnly: true,
      approvable: false,
      step: { stepId: "REPAIR-test-PLAN-1" },
    });
    expect(getStepPlan).toHaveBeenCalledWith("297189", "REPAIR-test", "REPAIR-test-PLAN-1", false);
  });

  it("updates sharing under the verified actor and returns the complete safe Repair view", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/share`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=verified-cookie",
        "X-User-Id": "forged-user",
      },
      body: JSON.stringify({ shared: true }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      taskId: "REPAIR-test",
      shared: true,
      canOperate: true,
      canManageShare: true,
      toolCalls: [],
    });
    expect(setTaskShared).toHaveBeenCalledWith({
      actorUserId: "297189",
      isAdmin: false,
      taskId: "REPAIR-test",
      shared: true,
    });
  });

  it("rejects a non-boolean Repair sharing value before calling the service", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/share`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shared: "true" }),
    });

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "invalid_repair_shared" });
    expect(setTaskShared).not.toHaveBeenCalled();
  });

  it("terminates Repair under the verified owner identity without forwarding browser credentials", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/terminate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=current-cookie",
        Authorization: "Bearer must-not-forward",
        "X-User-Id": "forged-user",
      },
      body: JSON.stringify({ reason: "用户结束本次实验", jobId: "forged-job" }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      taskId: "REPAIR-test",
      status: "canceled",
      termination: { status: "remote_stopped", aisJobId: "job-1" },
    });
    expect(terminateTask).toHaveBeenCalledWith({
      actorUserId: "297189",
      taskId: "REPAIR-test",
      reason: "用户结束本次实验",
    });
  });

  it("passes only the request actor identity and current Cookie to resume", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/resume`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=current-cookie",
        Authorization: "Bearer browser-token-must-not-forward",
        "X-User-Id": "forged-user",
      },
      body: JSON.stringify({
        jobId: "forged-job",
        agentMode: "openclaw",
        llmUseDefault: false,
        llmModel: "GLM-5.2",
        llmApiKey: "fresh-key",
      }),
    });

    expect(response.status).toBe(202);
    expect(resumeTask).toHaveBeenCalledWith({
      actorUserId: "297189",
      authHeaders: { cookie: "SSO_SESSION=current-cookie", "x-user-id": "297189" },
      taskId: "REPAIR-test",
      body: {
        agentMode: "openclaw",
        llmUseDefault: false,
        llmModel: "GLM-5.2",
        llmApiKey: "fresh-key",
        cfuseEngine: undefined,
        cfuseModel: undefined,
      },
    });
  });

  it("forwards the selected Agent fields and one-execution key on Plan/Result decisions", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const plan = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/plan-decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: "SSO_SESSION=decision-cookie" },
      body: JSON.stringify({
        decision: "approve",
        artifactDigest: "a".repeat(64),
        agentMode: "openclaw",
        llmUseDefault: false,
        llmModel: "Kimi-K2.5",
        llmApiKey: "fresh-plan-key",
      }),
    });
    expect(plan.status).toBe(202);
    expect(decidePlan).toHaveBeenCalledWith({
      actorUserId: "297189",
      authHeaders: { cookie: "SSO_SESSION=decision-cookie", "x-user-id": "297189" },
      taskId: "REPAIR-test",
      body: expect.objectContaining({
        decision: "approve",
        agentMode: "openclaw",
        llmApiKey: "fresh-plan-key",
      }),
    });

    const result = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/result-decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: "SSO_SESSION=result-cookie" },
      body: JSON.stringify({ decision: "retry", reason: "still broken", llmApiKey: "fresh-result-key" }),
    });
    expect(result.status).toBe(202);
    expect(decideResult).toHaveBeenCalledWith({
      actorUserId: "297189",
      authHeaders: { cookie: "SSO_SESSION=result-cookie", "x-user-id": "297189" },
      taskId: "REPAIR-test",
      body: expect.objectContaining({ decision: "retry", llmApiKey: "fresh-result-key" }),
    });
  });

  it("fulfills by path toolCallId and current Cookie without accepting body overrides", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/tool-calls/rtc-1/fulfill`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=fulfill-cookie",
        Authorization: "Bearer must-not-reach-ocb",
      },
      body: JSON.stringify({
        toolCallId: "rtc-attacker",
        method: "DELETE",
        path: "/api/bots/victim",
        ownerId: "victim",
      }),
    });

    expect(response.status).toBe(200);
    expect(fulfillToolCall).toHaveBeenCalledWith({
      actorUserId: "297189",
      authHeaders: { cookie: "SSO_SESSION=fulfill-cookie", "x-user-id": "297189" },
      taskId: "REPAIR-test",
      toolCallId: "rtc-1",
    });
  });

  it("submits a cfuse AuthCode only under the verified task owner and path toolCallId", async () => {
    await startRouter(async () => ({ userId: "297189", source: "request" }));
    const response = await fetch(`${baseUrl}/api/repair/v1/tasks/REPAIR-test/tool-calls/rtc-login/cfuse-auth-code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "SSO_SESSION=browser-cookie",
        "X-User-Id": "forged-user",
      },
      body: JSON.stringify({
        authCode: "one-time-code",
        taskId: "REPAIR-victim",
        toolCallId: "rtc-victim",
      }),
    });

    expect(response.status).toBe(202);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.json() as Record<string, unknown>;
    expect(body).toMatchObject({
      toolCallId: "rtc-login",
      toolName: "cfuse_login",
      operation: "authorize",
      status: "executing",
      requiresBrowserRelay: false,
    });
    expect(body).not.toHaveProperty("request");
    expect(body).not.toHaveProperty("result");
    expect(submitCfuseAuthCode).toHaveBeenCalledWith({
      actorUserId: "297189",
      isAdmin: false,
      taskId: "REPAIR-test",
      toolCallId: "rtc-login",
      body: { authCode: "one-time-code" },
    });
  });
});

describe("Repair internal execution-ticket boundary", () => {
  it("verifies the Bearer execution ticket and passes only the verified workload identity", async () => {
    const resolveActor = vi.fn(async () => ({ userId: "browser-user", source: "request" as const }));
    const verify = vi.fn(async (req) => {
      expect(req.header("authorization")).toBe("Bearer ce_repair_current-ticket");
      return { taskId: "REPAIR-test", stepId: "STEP-1", executionId: "exec-1" };
    });
    await startRouter(resolveActor, { verify });

    const response = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/bootstrap`,
      { headers: { Authorization: "Bearer ce_repair_current-ticket", "X-User-Id": "forged-browser" } },
    );

    expect(response.status).toBe(200);
    expect(verify).toHaveBeenCalledTimes(1);
    expect(bootstrap).toHaveBeenCalledWith({
      taskId: "REPAIR-test",
      stepId: "STEP-1",
      executionId: "exec-1",
    });
    expect(resolveActor).not.toHaveBeenCalled();
  });

  it("rejects a ticket identity whose Task/Step does not match the internal route", async () => {
    const verifier = {
      verify: vi.fn(async () => ({
        taskId: "REPAIR-test",
        stepId: "STEP-from-another-ticket",
        executionId: "exec-1",
      })),
    };
    await startRouter(async () => ({ userId: "browser-user", source: "request" }), verifier);

    const response = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/bootstrap`,
      { headers: { Authorization: "Bearer ce_repair_wrong-scope" } },
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: "repair_workload_route_mismatch" });
    expect(bootstrap).not.toHaveBeenCalled();
  });

  it("refreshes an artifact upload target only through the verified workload identity", async () => {
    const identity = { taskId: "REPAIR-test", stepId: "STEP-1", executionId: "exec-1" };
    const verifier = { verify: vi.fn(async () => identity) };
    await startRouter(async () => ({ userId: "browser-user", source: "request" }), verifier);

    const response = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/artifacts/refresh`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer ce_repair_current-ticket",
        },
        body: JSON.stringify({ artifactName: "plan" }),
      },
    );

    expect(response.status).toBe(200);
    expect(refreshArtifactUpload).toHaveBeenCalledWith(identity, { artifactName: "plan" });
    expect(await response.json()).toMatchObject({
      artifact: { name: "plan", objectKey: expect.stringContaining("plan.json") },
      expiresInSeconds: 86_400,
    });
  });

  it("allows only decision/claim to carry an immediately-previous requestedStepId alias", async () => {
    const identity = {
      taskId: "REPAIR-test",
      stepId: "STEP-2",
      executionId: "exec-1",
      requestedStepId: "STEP-1",
    };
    const verifier = { verify: vi.fn(async () => identity) };
    await startRouter(async () => ({ userId: "browser-user", source: "request" }), verifier);

    const claimResponse = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/decision/claim`,
      { method: "POST", headers: { Authorization: "Bearer ce_repair_current-ticket" } },
    );
    expect(claimResponse.status).toBe(200);
    expect(claimDecision).toHaveBeenCalledWith(identity);

    const bootstrapResponse = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/bootstrap`,
      { headers: { Authorization: "Bearer ce_repair_current-ticket" } },
    );
    expect(bootstrapResponse.status).toBe(403);
    expect(bootstrap).not.toHaveBeenCalled();
  });

  it("fails closed when no workload verifier is configured", async () => {
    await startRouter(async () => ({ userId: "browser-user", source: "request" }), null);
    const response = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/heartbeat`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    );
    expect(response.status).toBe(503);
    expect(heartbeat).not.toHaveBeenCalled();
  });

  it("keeps cfuse login request, take and report on the strict current execution identity", async () => {
    const identity = { taskId: "REPAIR-test", stepId: "STEP-1", executionId: "exec-1" };
    const resolveActor = vi.fn(async () => ({ userId: "browser-user", source: "request" as const }));
    const verifier = { verify: vi.fn(async () => identity) };
    await startRouter(resolveActor, verifier);

    const request = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/tools/cfuse-login`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer ce_repair_ticket" },
        body: JSON.stringify({
          clientRequestId: "login-1",
          loginUrl: "https://codefuse.antgroup-inc.cn/cloud/oauth?port=31337",
          ignored: "value",
        }),
      },
    );
    expect(request.status).toBe(202);
    expect(requestCfuseLogin).toHaveBeenCalledWith(identity, {
      clientRequestId: "login-1",
      loginUrl: "https://codefuse.antgroup-inc.cn/cloud/oauth?port=31337",
    });

    const take = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/tools/cfuse-login/rtc-login/take`,
      { method: "POST", headers: { Authorization: "Bearer ce_repair_ticket" } },
    );
    expect(take.status).toBe(200);
    expect(take.headers.get("cache-control")).toBe("no-store");
    expect(takeCfuseAuthCode).toHaveBeenCalledWith(identity, "rtc-login");

    const report = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/tools/cfuse-login/rtc-login/report`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer ce_repair_ticket" },
        body: JSON.stringify({ status: "succeeded", errorCode: "ignored", ignored: "value" }),
      },
    );
    expect(report.status).toBe(200);
    expect(reportCfuseLogin).toHaveBeenCalledWith(identity, "rtc-login", {
      status: "succeeded",
      errorCode: "ignored",
      errorMessage: undefined,
    });
    expect(resolveActor).not.toHaveBeenCalled();
  });

  it("records a conclusion under the verified workload and drops non-contract fields", async () => {
    const identity = { taskId: "REPAIR-test", stepId: "STEP-1", executionId: "exec-1" };
    const verifier = { verify: vi.fn(async () => identity) };
    await startRouter(async () => ({ userId: "browser-user", source: "request" }), verifier);
    const response = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/tools/semantic-records`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer ce_repair_ticket" },
        body: JSON.stringify({
          sourceToolCallId: "rtc-source",
          evidenceToolCallIds: ["rtc-source"],
          conclusionZh: "本次检查确认目标文件存在。",
          nextAction: "继续检查对应进程的启动参数。",
          clientRequestId: "attacker-controlled-id",
          sourceResultDigest: "attacker-controlled-digest",
        }),
      },
    );
    expect(response.status).toBe(200);
    expect(recordSemanticConclusion).toHaveBeenCalledWith(identity, {
      sourceToolCallId: "rtc-source",
      evidenceToolCallIds: ["rtc-source"],
      conclusionZh: "本次检查确认目标文件存在。",
      nextAction: "继续检查对应进程的启动参数。",
    });

    recordSemanticConclusion.mockRejectedValueOnce(new RepairError(
      502,
      "repair_tool_failed",
      "Repair tool call 执行失败",
      "rtc-failed",
    ));
    const failed = await fetch(
      `${baseUrl}/api/repair/v1/internal/tasks/REPAIR-test/steps/STEP-1/tools/semantic-records`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer ce_repair_ticket" },
        body: JSON.stringify({
          sourceToolCallId: "rtc-source",
          evidenceToolCallIds: ["rtc-source"],
          conclusionZh: "本次检查未能形成有效结论。",
          nextAction: "修正调用参数后继续检查。",
        }),
      },
    );
    expect(failed.status).toBe(502);
    expect(await failed.json()).toEqual({
      error: "repair_tool_failed",
      message: "Repair tool call 执行失败",
      toolCallId: "rtc-failed",
    });
  });
});
