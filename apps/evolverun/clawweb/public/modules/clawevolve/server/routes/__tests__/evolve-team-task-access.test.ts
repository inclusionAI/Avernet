import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { createHash } from "node:crypto";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { SkillPackageStorage } from "../../services/object-storage/skill-package-storage.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let stages: StageSkillRepository;
let assets: SkillAssetRepository;
let server: ReturnType<express.Application["listen"]>;
let url: string;
let candidate: Buffer;
let baseline: Buffer;
const spaces = vi.fn();
const dispatch = vi.fn();
const replace = vi.fn();
const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");
const team = { id: "235", type: "TEAM" as const, name: "team", role: "MEMBER" as const };
const ref = (name: string) => `oss://example-skills/test/skills/${name}`;

async function request(path: string, actor = "member", method = "GET", body?: unknown) {
  return fetch(`${url}/api/evolve${path}`, { method,
    headers: { "X-User-Id": actor, "Content-Type": "application/json", Cookie: "test-session=fixture" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
async function seed(id = "EV-TEAM", changes: Record<string, unknown> = {}, type = "hardening") {
  await repo.createTask({ taskId: id, taskType: type, userId: "owner", botId: "bot",
    createdBy: "owner", taskName: id, configJson: JSON.stringify({ dispatchMode: "run", botEnv: "pre",
      targetSkill: { assetId: "SKILL-TEAM", skillId: "source", name: "fixture", ownerUserId: "owner",
        spaceId: "235", spaceType: "TEAM", baseline: { ref: ref("baseline.zip"), sha256: `sha256:${digest(baseline)}` },
        candidate: { ref: ref("candidate.zip"), artifact: { ref: ref("candidate.zip"),
          size: candidate.length, sha256: digest(candidate), contentType: "application/zip" } }, ...changes },
    }) });
}

async function grantBotCollaboration(actor = "member", env = "pre", botPk = 1) {
  await db.exec("INSERT INTO ac_bot_collaborator (bot_pk, user_id, role, env) VALUES (?, ?, 'member', ?)",
    [botPk, actor, env]);
}

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db); stages = new StageSkillRepository(db); assets = new SkillAssetRepository(db);
  await db.exec(`CREATE TABLE ac_bots (
    id INTEGER PRIMARY KEY, bot_id TEXT, owner_id TEXT, entity_id TEXT, env TEXT, is_delete INTEGER DEFAULT 0
  )`);
  await db.exec(`CREATE TABLE ac_bot_collaborator (
    bot_pk INTEGER, user_id TEXT, role TEXT, env TEXT
  )`);
  await db.exec("INSERT INTO ac_bots (id, bot_id, owner_id, entity_id, env) VALUES (1, 'bot', 'owner', 'owner', 'pre')");
  const zip = new JSZip(); zip.file("SKILL.md", "---\nname: fixture\n---\nbaseline");
  baseline = await zip.generateAsync({ type: "nodebuffer" });
  zip.file("SKILL.md", "---\nname: fixture\n---\ncandidate");
  candidate = await zip.generateAsync({ type: "nodebuffer" });
  spaces.mockReset();
  spaces.mockImplementation(async ({ identity }) => identity.userId === "outsider" ? [] : [team]);
  dispatch.mockReset(); dispatch.mockResolvedValue({ runId: "run", sessionId: null, platformResponse: {} });
  replace.mockReset(); replace.mockResolvedValue({ sha256: `sha256:${digest(candidate)}` });
  vi.spyOn(repo, "resolveEvolveBotRuntime").mockResolvedValue({ provider: "baas", deviceId: "device", env: "pre",
    activeEngine: "openclaw", botType: "personal", hasServiceBot: false, botStatus: "active",
    bindingId: "binding", bindingStatus: "active" });
  await assets.createAsset({ assetId: "SKILL-TEAM", versionId: "SKVER-BASE", ownerUserId: "owner",
    botId: "bot", externalSkillId: "source", displayName: "fixture", spaceId: "235", spaceType: "TEAM",
    spaceName: "team", packageRef: ref("baseline.zip"), packageSha256: `sha256:${digest(baseline)}` });
  const app = express(); app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, {
    hostSpaces: { listAccessibleSpaces: spaces }, skillAssetRepo: assets, stageSkillRepo: stages, dispatch,
    dispatchTaskLogArchive: async () => ({ runId: "archive", sessionId: null, platformResponse: {} }),
    artifactUrlStore: { createSignedUrl: async () => "https://example.test/feedback" },
    artifactStore: { getObject: async () => ({ content: candidate, etag: null, contentType: "application/zip" }), createSignedUrl: async () => "https://example.test/signed" },
    hostLocalSkills: { listLocalSkills: async () => [], exportLocalSkill: async () => ({ packageBytes: baseline,
      sha256: `sha256:${digest(baseline)}`, displayName: "fixture" }), replaceLocalSkill: replace },
    skillPackages: new SkillPackageStorage({ bucket: "example-skills", prefix: "test", store: {
      getObject: async key => ({ content: key.endsWith("candidate.zip") ? candidate : baseline, etag: null, contentType: "application/zip" }),
      createSignedUrl: async () => "https://example.test/signed",
    } }),
  }));
  app.use((error: Error & { status?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(error.status ?? 500).json({ error: error.message });
  });
  server = await new Promise((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); vi.restoreAllMocks(); });

describe("team-space Skill task permissions", () => {
  it.each(["hardening", "diagnose", "full"])("lets teammates read and share a %s task without public sharing", async type => {
    await seed("EV-TEAM", {}, type);
    const response = await request("/tasks/EV-TEAM");
    expect(response.status).toBe(200);
    expect((await response.json()).canManage).toBe(true);
    expect((await request("/tasks/EV-TEAM/share", "member", "PATCH", { shared: true })).status).toBe(200);
    expect(JSON.parse((await repo.findTask("EV-TEAM"))!.config_json).shared).toBe(true);
    expect((await repo.findTask("EV-TEAM"))!.created_by).toBe("owner");
    expect(spaces.mock.calls[0][0].identity).toMatchObject({ userId: "member", cookie: "test-session=fixture" });
  });

  it("lists own and accessible team tasks with correct pagination", async () => {
    await seed(); await seed("EV-OTHER", { spaceId: "other" });
    await seed("EV-PERSONAL", { spaceType: "PERSONAL" });
    await seed("EV-INVALID", { assetId: null });
    await seed("EV-NUMERIC");
    const malformed = JSON.parse((await repo.findTask("EV-NUMERIC"))!.config_json);
    malformed.targetSkill.assetId = 123;
    await repo.updateTaskConfig("EV-NUMERIC", malformed);
    await repo.createTask({ taskId: "EV-MINE", taskType: "diagnose", userId: "member", botId: "mine",
      createdBy: "member", taskName: "mine", configJson: "{}" });
    const body = await (await request("/tasks?pageSize=1")).json();
    expect(body.total).toBe(2); expect(body.tasks).toHaveLength(1);
    const all = await (await request("/tasks?pageSize=50")).json();
    expect(all.tasks.map((task: { task_id: string }) => task.task_id).sort()).toEqual(["EV-MINE", "EV-TEAM"]);
  });

  it.each([{ spaceId: null }, { spaceType: "PERSONAL" }, { spaceId: "other" }, { assetId: "" }])(
    "does not grant team access outside the frozen target team: %j", async change => {
      await seed("EV-PRIVATE", change);
      expect((await request("/tasks/EV-PRIVATE")).status).toBe(403);
      expect((await request("/tasks/EV-PRIVATE/share", "member", "PATCH", { shared: true })).status).toBe(403);
    });

  it("does not trust a requested space or a shared implementation's space", async () => {
    await repo.createTask({ taskId: "EV-NATIVE", taskType: "diagnose", userId: "owner", botId: "bot",
      createdBy: "owner", taskName: "native", configJson: JSON.stringify({ stageExtensions: {
        diagnose: { replace: { enabled: true, spaceId: "235" } } } }) });
    expect((await request("/tasks/EV-NATIVE?spaceId=235")).status).toBe(403);
    await seed();
    expect((await request("/tasks/EV-TEAM?spaceId=235", "outsider")).status).toBe(403);
  });

  it("checks live membership again on the next request and fails closed on lookup failure", async () => {
    await seed(); expect((await request("/tasks/EV-TEAM")).status).toBe(200);
    spaces.mockResolvedValue([]);
    expect((await request("/tasks/EV-TEAM/share", "member", "PATCH", { shared: true })).status).toBe(403);
    spaces.mockRejectedValue(Object.assign(new Error("space unavailable"), { status: 503 }));
    expect((await request("/tasks/EV-TEAM/share", "member", "PATCH", { shared: true })).status).toBe(503);
    expect(JSON.parse((await repo.findTask("EV-TEAM"))!.config_json).shared).not.toBe(true);
    expect((await request("/tasks/EV-TEAM", "owner")).status).toBe(200);
  });

  it("preserves the Bot owner's existing log access when another user created the task", async () => {
    await seed();
    await db.exec("UPDATE ce_tasks SET created_by = ? WHERE task_id = ?", ["creator", "EV-TEAM"]);
    spaces.mockRejectedValue(new Error("space unavailable"));
    expect((await request("/tasks/EV-TEAM/log-archives", "owner")).status).toBe(200);
    expect((await request("/tasks/EV-TEAM/log-archives", "owner", "POST", {})).status).toBe(202);
    expect(spaces).not.toHaveBeenCalled();
  });

  it("keeps public sharing read-only for nonmembers, including retry and cancel", async () => {
    await seed(); await repo.updateTaskConfig("EV-TEAM", { ...JSON.parse((await repo.findTask("EV-TEAM"))!.config_json), shared: true });
    const response = await request("/tasks/EV-TEAM", "outsider");
    expect(response.status).toBe(200); expect((await response.json()).canManage).toBe(false);
    for (const path of ["/share", "/skill-decision", "/steps/STEP/retry", "/steps/STEP/cancel",
      "/steps/STEP/interactions/HITL/answer", "/steps/STEP/interactions/LOOP/files/upload-url", "/log-archives"]) {
      expect((await request(`/tasks/EV-TEAM${path}`, "outsider", path === "/share" ? "PATCH" : "POST", {})).status).toBe(403);
    }
    expect(dispatch).not.toHaveBeenCalled(); expect(replace).not.toHaveBeenCalled();
  });

  it.each([
    { question: { tag: "scope", format: "text", content: "Approve the scoped changes?" }, answer: "Approved" },
    { question: { format: "form", title: "Scope", contents: [],
      questions: [{ id: "scope", type: "short_text", title: "Scope", required: true }] },
      answer: { answers: { scope: { value: "Preserve business behavior" } } } },
  ])("lets a teammate answer an existing interaction and resume its owner's task", async ({ question, answer }) => {
    await seed();
    await repo.createStep({ taskId: "EV-TEAM", stepId: "STEP", stepType: "hardening", stepNo: 1,
      command: "/clawevolve-hardening --step-id STEP" });
    await repo.updateStepStatus("STEP", { status: "waiting_context" });
    await stages.createInteraction({ interactionId: "HITL", taskId: "EV-TEAM", stepId: "STEP", request: question });
    const response = await request("/tasks/EV-TEAM/steps/STEP/interactions/HITL/answer", "member", "POST", { answer });
    expect(response.status, JSON.stringify(await response.json())).toBe(200);
    expect((await stages.findInteraction("HITL"))!.status).toBe("answered");
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ userId: "owner", botId: "bot", stepId: "STEP" }));
    expect((await request("/tasks/EV-TEAM/steps/STEP/interactions/HITL/answer", "member", "POST", { answer })).status).toBe(409);
  });

  it("lets a teammate upload loop feedback and continue the same Stage", async () => {
    await seed();
    await repo.createStep({ taskId: "EV-TEAM", stepId: "STEP", stepType: "hardening", stepNo: 1,
      command: "/clawevolve-hardening --step-id STEP" });
    await repo.updateStepStatus("STEP", { status: "waiting_context", output: { summary: "Round one" } });
    await stages.createInteraction({ interactionId: "LOOP", taskId: "EV-TEAM", stepId: "STEP", request: {
      kind: "loop_feedback", action: "request_feedback", prompt: "Feedback?", accepts: { text: true, files: [".txt"] },
    } });
    const upload = await request("/tasks/EV-TEAM/steps/STEP/interactions/LOOP/files/upload-url", "member", "POST", {
      name: "feedback.txt", size: 3, sha256: "a".repeat(64), contentType: "text/plain",
    });
    const file = await upload.json();
    expect(upload.status, JSON.stringify(file)).toBe(200);
    const response = await request("/tasks/EV-TEAM/steps/STEP/interactions/LOOP/answer", "member", "POST", {
      answer: { action: "continue", feedback: { text: "Keep the original scope", files: [file.artifact] } },
    });
    const result = await response.json();
    expect(response.status, JSON.stringify(result)).toBe(201);
    const next = await repo.findStep(result.nextStep.stepId);
    expect(next!.step_type).toBe("hardening");
    expect(JSON.parse(next!.ext_json!).loop.round).toBe(2);
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ userId: "owner", stepId: next!.step_id }));
    expect((await request("/tasks/EV-TEAM/steps/STEP/interactions/LOOP/files/0/download-url")).status).toBe(200);
  });

  it("lets a teammate cancel, retry and request logs without changing task ownership", async () => {
    await seed();
    await repo.createStep({ taskId: "EV-TEAM", stepId: "STEP", stepType: "hardening", stepNo: 1,
      command: "/clawevolve-hardening --step-id STEP" });
    expect((await request("/tasks/EV-TEAM/steps/STEP/cancel", "member", "POST", {})).status).toBe(200);
    expect((await repo.findStep("STEP"))!.status).toBe("canceled");
    const retried = await request("/tasks/EV-TEAM/steps/STEP/retry", "member", "POST", {});
    expect(retried.status, JSON.stringify(await retried.json())).toBe(201);
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ userId: "owner", botId: "bot" }));
    expect((await request("/tasks/EV-TEAM/log-archives", "member", "POST", {})).status).toBe(202);
    expect((await request("/tasks/EV-TEAM/log-archives")).status).toBe(200);
    expect((await repo.findTask("EV-TEAM"))!.created_by).toBe("owner");
  });

  it("lets a teammate who collaborates on the source Bot apply and records the real actor", async () => {
    await grantBotCollaboration();
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    expect(await (await request("/tasks/EV-TEAM")).json()).toMatchObject({ canManage: true, canApplySkill: true });
    const response = await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "accept" });
    expect(response.status).toBe(200);
    expect(replace).toHaveBeenCalledWith(expect.objectContaining({ ownerUserId: "owner", identity: expect.objectContaining({ userId: "member" }) }));
    const versions = await assets.listVersions("SKILL-TEAM");
    expect(versions[0].created_by).toBe("member");
    expect((await repo.findTask("EV-TEAM"))!.user_id).toBe("owner");
  });

  it("keeps team task controls but denies applying without source Bot rights", async () => {
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    expect(await (await request("/tasks/EV-TEAM")).json()).toMatchObject({ canManage: true, canApplySkill: false });
    const before = await repo.findTask("EV-TEAM");
    const response = await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "accept" });
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "SKILL_APPLY_FORBIDDEN" });
    expect(await repo.findTask("EV-TEAM")).toEqual(before);
    expect(await assets.listVersions("SKILL-TEAM")).toHaveLength(1);
    expect(replace).not.toHaveBeenCalled();
  });

  it("lets the source Bot owner apply", async () => {
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    expect(await (await request("/tasks/EV-TEAM", "owner")).json()).toMatchObject({ canApplySkill: true });
    expect((await request("/tasks/EV-TEAM/skill-decision", "owner", "POST", { decision: "accept" })).status).toBe(200);
    expect(replace).toHaveBeenCalledWith(expect.objectContaining({ identity: expect.objectContaining({ userId: "owner" }) }));
  });

  it.each(["owner", "member"])("uses the frozen Skill environment for a historical task (%s)", async actor => {
    await grantBotCollaboration();
    await db.exec("INSERT INTO ac_bots (id, bot_id, owner_id, entity_id, env) VALUES (2, 'bot', 'owner', 'owner', 'prod')");
    await seed("EV-TEAM", { botEnv: "pre" });
    const config = JSON.parse((await repo.findTask("EV-TEAM"))!.config_json);
    await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance", config: { ...config, botEnv: "" } });
    expect(await (await request("/tasks/EV-TEAM", actor)).json()).toMatchObject({ canApplySkill: true });
    expect((await request("/tasks/EV-TEAM/skill-decision", actor, "POST", { decision: "accept" })).status).toBe(200);
    expect(replace).toHaveBeenCalledWith(expect.objectContaining({ botEnv: "pre" }));
  });

  it("rechecks collaboration when applying after the detail was loaded", async () => {
    await grantBotCollaboration();
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    expect(await (await request("/tasks/EV-TEAM")).json()).toMatchObject({ canApplySkill: true });
    await db.exec("DELETE FROM ac_bot_collaborator WHERE user_id = 'member'");
    expect((await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "accept" })).status).toBe(403);
    expect(replace).not.toHaveBeenCalled();
  });

  it.each([{ env: "prod", botPk: 1 }, { env: "pre", botPk: 2 }])(
    "does not authorize another Bot or environment's collaboration: %j", async ({ env, botPk }) => {
      await grantBotCollaboration("member", env, botPk);
      await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
      expect(await (await request("/tasks/EV-TEAM")).json()).toMatchObject({ canApplySkill: false });
      expect((await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "accept" })).status).toBe(403);
      expect(replace).not.toHaveBeenCalled();
    });

  it("lets a teammate reject a candidate without writing to the Bot", async () => {
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    expect((await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "reject" })).status).toBe(200);
    expect(replace).not.toHaveBeenCalled();
    expect((await repo.findTask("EV-TEAM"))!.status).toBe("completed");
  });

  it("retains the Host's conflict check when a teammate accepts an outdated candidate", async () => {
    await grantBotCollaboration();
    await seed(); await repo.updateTaskState({ taskId: "EV-TEAM", status: "waiting_acceptance" });
    replace.mockRejectedValue(Object.assign(new Error("Skill changed"), { status: 409, writeNotStarted: true }));
    const response = await request("/tasks/EV-TEAM/skill-decision", "member", "POST", { decision: "accept" });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ code: "SKILL_VERSION_CONFLICT" });
    expect((await assets.listVersions("SKILL-TEAM"))).toHaveLength(1);
    expect((await repo.findTask("EV-TEAM"))!.status).toBe("waiting_acceptance");
  });
});
