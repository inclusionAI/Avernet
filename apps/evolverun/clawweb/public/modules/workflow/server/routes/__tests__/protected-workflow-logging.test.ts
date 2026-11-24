// @vitest-environment node
import express, { type Router } from "express";
import { once } from "node:events";
import type { Server } from "node:http";
import { appendFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatHttpLog } from "@avernet/clawweb-shared/server/services/file-log-format";
import { createAutoHealRouter, type AutoHealRouteDependencies } from "../auto-heal.js";
import { createPackVersionRouter } from "../pack-version.js";
import { createPackScriptsRouter } from "../pack-scripts.js";
import { createWorkflowUnifiedSaveRouter } from "../workflow-unified-save.js";
import { createEditorDraftCasRouter } from "../editor-draft-cas.js";
import { AutoHealSaveService } from "../../services/auto-heal-save-service.js";
import { AutoHealDeployService } from "../../services/auto-heal-deploy-service.js";
import * as gitSettings from "../../config/auto-heal-git-config.js";

const files = vi.hoisted(() => ({ appendFileSync: vi.fn() }));
vi.mock("node:fs", async importOriginal => ({ ...await importOriginal<typeof import("node:fs")>(), ...files }));
const channels = ["log", "info", "warn", "error", "debug", "trace", "dir", "dirxml", "table"] as const;
const servers: Server[] = [];
beforeEach(() => {
  files.appendFileSync.mockClear();
  for (const channel of channels) vi.spyOn(console, channel).mockImplementation(() => {});
  vi.spyOn(gitSettings, "resolveAutoHealGitSettings").mockResolvedValue({
    gitConfig: { remoteUrl: "", username: "", email: "", token: "" }, gitAuthor: { name: "", email: "" },
  });
});
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))));
  vi.restoreAllMocks();
});
async function listen(router: Router) {
  const app = express();
  app.use(express.json());
  // The actual route supplies the diagnostic; exercise its file-format consumer contract too.
  app.use((_req, res, next) => {
    res.once("finish", () => {
      if (res.locals.taskGuardApiDiagnostic) appendFileSync("/test/task-guard-http.log",
        formatHttpLog(new Date(), "ERROR", "http", res.locals.taskGuardApiDiagnostic) + "\n");
    });
    next();
  });
  app.use(router);
  const server = app.listen(0, "127.0.0.1"); servers.push(server); await once(server, "listening");
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}
function privateError(status = 500) {
  return Object.assign(new Error("<PRIVATE_TOKEN_MARKER> prompt=<PRIVATE_PROMPT_MARKER> currentYaml=<PRIVATE_YAML_MARKER>"), {
    status, config: { headers: { Authorization: "<PRIVATE_AUTH_MARKER>" } }, token: "<PRIVATE_TOKEN_MARKER>",
  });
}
function expectSafeObservation() {
  expect(console.error).toHaveBeenCalled();
  expect(files.appendFileSync).toHaveBeenCalledOnce();
  const line = files.appendFileSync.mock.calls[0][1] as string;
  expect(JSON.parse(line.slice(line.indexOf("{")))).toEqual({ message: "Protected workflow operation failed",
    stack: expect.stringContaining("Workflow protected operation failure observed"), stackSource: "observation" });
  expect(JSON.stringify([...channels.map(channel => vi.mocked(console[channel]).mock.calls), files.appendFileSync.mock.calls]))
    .not.toMatch(/PRIVATE_|prompt=|currentYaml|Authorization|config|token/);
}
const dependencies = () => ({
  db: {}, workflowSpecRepo: {}, deployHistoryRepo: {}, botPermRepo: null,
  resolvePrincipal: async () => ({ actorId: "owner" }),
  gitConfig: { remoteUrl: "", username: "", email: "", token: "" }, gitAuthor: { name: "", email: "" },
} as unknown as AutoHealRouteDependencies);

const saveBody = { workflowId: "flow", spec: { id: "flow" } };
const unifiedBody = { requestId: "request", workflowId: "flow", packId: "pack", sourceVersion: "draft", sourceCommit: "a".repeat(40),
  expectedSavedHead: null, expectedDraftToken: "a".repeat(64), specJson: "{}" };
describe("protected workflow route exception privacy", () => {
  it.each([
    ["/save", "save", saveBody, { error: "Save could not be completed", code: "SAVE_FAILED" }],
    ["/unified-save", "saveUnified", unifiedBody, { code: "UNIFIED_SAVE_FAILED" }],
    ["/editor-save-first-draft", "saveEditorFirstDraft", { ...saveBody, expectedDraftAbsent: true, expectedActiveDeployNumber: 1,
      expectedActiveToken: "a".repeat(64) }, { error: "Save could not be completed", code: "SAVE_FAILED" }],
  ] as const)("never logs the original sensitive service exception on %s", async (path, method, body, expectedBody) => {
    vi.spyOn(AutoHealSaveService.prototype, method).mockRejectedValue(privateError());
    const response = await fetch(await listen(createAutoHealRouter(dependencies())) + path, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    expect(response.status).toBe(500); expect(await response.json()).toEqual(expectedBody);
    expectSafeObservation();
  });

  it.each([
    ["/deploy", "deploy", {}], ["/editor-deploy", "editorDeploy", {}],
    ["/editor-deploy/snapshot-v2", "editorDeploy", { snapshotProtocol: "saved-tree-v2" }],
  ] as const)("uses a safe observation for protected deployment %s", async (path, method, extra) => {
    vi.spyOn(AutoHealDeployService.prototype, method).mockRejectedValue(privateError());
    const response = await fetch(await listen(createAutoHealRouter(dependencies())) + path, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ workflowId: "flow", ...extra }),
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Deployment could not be completed", code: "DEPLOYMENT_FAILED" });
    expectSafeObservation();
  });

  it("keeps protected deployment-state failures sanitized", async () => {
    vi.spyOn(AutoHealDeployService.prototype, "editorDeploymentState").mockRejectedValue(privateError());
    const response = await fetch(await listen(createAutoHealRouter(dependencies())) + "/editor-deploy/snapshot-v2/state?workflowId=flow");
    expect(response.status).toBe(500); expect(await response.json()).toEqual({ code: "DEPLOYMENT_STATE_UNAVAILABLE" });
    expectSafeObservation();
  });

  it("keeps protected business rejections at fixed-metadata WARN without raw message/stack", async () => {
    vi.spyOn(AutoHealSaveService.prototype, "save").mockRejectedValue(privateError(409));
    const response = await fetch(await listen(createAutoHealRouter(dependencies())) + "/save", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(saveBody),
    });
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: "Save could not be completed", code: "SAVE_FAILED" });
    expect(console.warn).toHaveBeenCalledWith("[auto-heal] request failed:", { errorCode: "PROTECTED_WORKFLOW_REJECTED" });
    expect(console.error).not.toHaveBeenCalled();
    expect(JSON.stringify(channels.map(channel => vi.mocked(console[channel]).mock.calls))).not.toMatch(/PRIVATE_|prompt=|currentYaml/);
    expect(files.appendFileSync).not.toHaveBeenCalled();
  });

  it.each(["pack-version?version=draft", "pack-operations/request-123", "pack-scripts", "unified-save"])(
    "retains the existing Pack suppression boundary on %s across console/file channels", async path => {
      const error = privateError();
      const deps = { resolvePrincipal: async () => ({ actorId: "owner" }),
        botPermRepo: { hasEditPermission: async () => true },
        workflowSpecRepo: { findByWorkflowId: async () => ({ workflow_id: "flow", pack_id: "pack" }) },
        authorizePackRead: async () => true, readSnapshot: vi.fn().mockRejectedValue(error), readOperation: vi.fn().mockRejectedValue(error) };
      const router = path.startsWith("pack-scripts") ? createPackScriptsRouter(deps)
        : path === "unified-save" ? createWorkflowUnifiedSaveRouter({ resolvePrincipal: deps.resolvePrincipal,
          resolveAuthorizedService: vi.fn().mockRejectedValue(error) }) : createPackVersionRouter(deps);
      const response = await fetch(await listen(router) + "/flow/" + path);
      expect(response.status).toBe(503); expect(await response.json()).toMatchObject({ code: "SERVICE_UNAVAILABLE" });
      expect(JSON.stringify([...channels.map(channel => vi.mocked(console[channel]).mock.calls), files.appendFileSync.mock.calls]))
        .not.toMatch(/PRIVATE_|prompt=|currentYaml|Authorization|config|token/);
      expect(files.appendFileSync).not.toHaveBeenCalled();
    });

  it("never bypasses the protected editor-draft boundary with a raw exception handoff", async () => {
    const base = await listen(createEditorDraftCasRouter({ resolvePrincipal: async () => ({ actorId: "owner" }),
      read: vi.fn().mockRejectedValue(privateError()), save: vi.fn() }));
    const response = await fetch(base + "/flow/editor-draft");
    expect(response.status).toBe(503); expect(await response.json()).toEqual({ code: "SERVICE_UNAVAILABLE" });
    expectSafeObservation();
  });
});
