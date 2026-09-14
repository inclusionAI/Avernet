import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { createEvolveRouter } from "../evolve.js";
import { freezeStageTestFixture } from "../../services/evolve/stage-test-fixture.js";
import { getArtifactBucket } from "../../services/object-storage/oss-object-store.js";

function planOutput() {
  return {
    goal: { summary: "改善日报", metrics: [] }, spec: { version: "v0", content_type: "text", content: "完整真实来源方案" },
    benchDomains: { trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" },
    benchCases: { trainCount: 1, testCount: 1, items: ["train", "test"].map((split) => ({
      sourceCaseId: `case-${split}`, taskId: `bench-${split}`, split,
      template: { ownerUserId: "owner", domainId: split.toUpperCase(), templateName: "daily", version: 1 },
    })) }, extraProducerOutput: { keep: "完整输出不裁剪" },
  };
}

describe("Optimize StageTest real Plan reference", () => {
  let db: SqliteDatabase;
  let repo: EvolveRepository;
  let stages: StageSkillRepository;
  let templates: BenchTemplateRepository;
  let server: ReturnType<express.Application["listen"]>;
  let base: string;
  const dispatch = vi.fn();
  const signedUrl = vi.fn();
  const putObject = vi.fn();
  beforeEach(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    repo = new EvolveRepository(db); stages = new StageSkillRepository(db); templates = new BenchTemplateRepository(db);
    dispatch.mockReset().mockResolvedValue({ runId: "test-run", sessionId: "test-session" });
    signedUrl.mockReset().mockResolvedValue("https://example.test/test-package"); putObject.mockReset();
    const app = express(); app.use(express.json());
    app.use("/api/evolve", createEvolveRouter(repo, {
      dispatch, stageSkillRepo: stages, benchTemplateRepo: templates,
      artifactUrlStore: { createSignedUrl: signedUrl },
      artifactStore: { createSignedUrl: signedUrl, putObject, getObject: vi.fn() },
    }));
    server = await new Promise((resolve) => { const instance = app.listen(0, () => resolve(instance)); });
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
    await stages.createImplementation({
      stageSkillId: "STAGE-OPT", implementationId: "IMPL-OPT", ownerUserId: "owner", displayName: "优化后置",
      stage: "optimize", mode: "postprocess", versionNo: 1,
      packageRef: "oss://clawevolve-artifacts/evolve/stage-implementations/IMPL-OPT/v1/package.zip",
      packageSha256: "a".repeat(64), staticValidation: { status: "passed" },
    });
    await stages.registerImplementation("IMPL-OPT");
    for (const domainId of ["TRAIN", "TEST"]) {
      await templates.create({ ownerUserId: "owner", domainId, templateName: "daily" });
      await templates.update("owner", domainId, "daily", { status: "published", publishedVersion: 1 });
    }
  });
  afterEach(async () => {
    if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
    await db.close();
  });
  async function seedSource(kind = "plan", owner = "owner", bot = "bot") {
    await repo.createTask({ taskId: "SOURCE", taskType: "stage_test", userId: owner, botId: bot,
      taskName: "来源Plan", configJson: "{}", createdBy: owner });
    await repo.createStep({ taskId: "SOURCE", stepId: "SOURCE-PLAN", stepType: kind, stepNo: 1, command: "/actual-plan" });
    await repo.updateStepStatus("SOURCE-PLAN", { status: "succeeded", output: planOutput() });
    if (kind === "stage_extension") await stages.createExtensionRun({
      taskId: "SOURCE", stepId: "SOURCE-PLAN", stage: "plan", mode: "replace", implementationId: "SOURCE-IMPL",
    });
    await repo.completeTask("SOURCE");
  }
  async function start(body: Record<string, unknown> = {}) {
    const response = await fetch(`${base}/stage-skills/IMPL-OPT/integration-tests`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner" },
      body: JSON.stringify({ botId: "bot", caseInput: { round: 1 }, planSourceRef: { taskId: "SOURCE", stepId: "SOURCE-PLAN" }, ...body }),
    });
    const raw = await response.text();
    const diagnostic = `StageTest HTTP ${response.status}; content-type=${response.headers.get("content-type")}; body=${JSON.stringify(raw)}`;
    expect(response.headers.get("content-type"), diagnostic).toMatch(/application\/json/);
    expect(raw.length, diagnostic).toBeGreaterThan(0);
    try { return { status: response.status, body: JSON.parse(raw) }; }
    catch { throw new Error(`Invalid JSON response: ${diagnostic}`); }
  }
  async function input(taskId: string, stepId: string) {
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/input`);
    const body = await response.json();
    expect(response.status, JSON.stringify(body)).toBe(200); return body;
  }

  it("rejects Skill Optimize tests without a frozen source candidate before dispatch", async () => {
    await stages.createDevelopment({ stageSkillId: "STAGE-OPT", ownerUserId: "owner", displayName: "Skill优化后置",
      flow: "skill_evolution", stage: "optimize", mode: "postprocess" });
    await seedSource();
    const created = await start();
    expect(created.status).toBe(400);
    expect(created.body.error).toMatch(/候选|冻结.*Skill/);
    expect(dispatch).not.toHaveBeenCalled();
    expect(putObject).not.toHaveBeenCalled();
  });

  it("prepares a task-owned Skill copy before Optimize and delivers that same copy to postprocess", async () => {
    await stages.createDevelopment({ stageSkillId: "STAGE-OPT", ownerUserId: "owner", displayName: "Skill优化后置",
      flow: "skill_evolution", stage: "optimize", mode: "postprocess" });
    await seedSource("stage_extension");
    const fixture = await freezeStageTestFixture("SOURCE", async () => ({ etag: "fixture" }), 3);
    await repo.updateTaskConfig("SOURCE", { stageTest: { implementationId: "SOURCE-IMPL", stage: "plan", mode: "replace", fixture } });
    const created = await start();
    expect(created.status).toBe(201);
    const { taskId, stepId } = created.body;
    const prepare = await input(taskId, stepId);
    expect(prepare).toMatchObject({ protocolVersion: "clawevolve.skill-candidate/v1", action: "prepare",
      candidateWorkspace: `/home/admin/.openclaw/clawevolve_workspaces/${taskId}/workspace`,
      targetSkill: { baselineSha256: fixture.sha256 } });
    expect((await repo.listSteps(taskId)).map(s => s.step_type)).toEqual(["skill_prepare"]);
    expect(dispatch.mock.calls[0][0].command).not.toContain("--stage optimize");
    const unsafePrepare = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", output: { prepared: true,
        workspace: "/home/admin/.openclaw/workspace", targetSkillPath: "/home/admin/.openclaw/workspace/skills/skills-local/daily-report-zh" } }),
    });
    expect(unsafePrepare.status).toBe(422);
    expect((await repo.listSteps(taskId)).map(s => s.step_type)).toEqual(["skill_prepare"]);
    const prepared = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", output: { prepared: true,
        workspace: prepare.candidateWorkspace, targetSkillPath: prepare.targetSkill.path } }),
    });
    expect(prepared.status).toBe(200);
    const optimize = (await repo.listSteps(taskId)).find(s => s.step_type === "optimize")!;
    expect(optimize.command).toContain(`--workspace '${prepare.candidateWorkspace}'`);
    expect((await input(taskId, optimize.step_id)).inputs.diagnoses[0].plan.output).toEqual(planOutput());
    const reported = await fetch(`${base}/internal/tasks/${taskId}/steps/${optimize.step_id}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "succeeded",
        output: { diff: { summary: "候选副本变化", files: [] }, metrics: [],
          baseline: Object.fromEntries(["train", "test"].map(role => [role, { role, producerStepId: optimize.step_id,
            source: "generated", ownerUserId: "owner", domainId: role.toUpperCase(), benchRunId: `run-${role}`, metrics: {} }])),
          roundDecision: { stop: true, reason: "一轮测试" } } }),
    });
    expect(reported.status).toBe(200);
    const post = (await repo.listSteps(taskId)).find(s => s.step_type === "stage_extension")!;
    expect((await input(taskId, post.step_id)).input.target_skill).toMatchObject({
      workspace: prepare.candidateWorkspace, path: prepare.targetSkill.path, name: "daily-report-zh" });
  });

  it.each(["missing-ref", "wrong-task", "wrong-ref", "wrong-version", "unfinalized-target", "later-final-candidate"])(
    "rejects unsafe Skill source %s without dispatching", async bad => {
      await stages.createDevelopment({ stageSkillId: "STAGE-OPT", ownerUserId: "owner", displayName: "Skill优化后置",
        flow: "skill_evolution", stage: "optimize", mode: "postprocess" });
      await seedSource("stage_extension");
      const fixture = await freezeStageTestFixture("SOURCE", async () => ({ etag: "fixture" }), 3);
      const config: Record<string, unknown> = { stageTest: { implementationId: "SOURCE-IMPL", stage: "plan", mode: "replace", fixture } };
      if (bad === "wrong-task") fixture.taskId = "OTHER";
      if (bad === "wrong-ref") fixture.ref = fixture.ref.replace("SOURCE", "OTHER");
      if (bad === "wrong-version") fixture.version = 2;
      if (bad === "unfinalized-target") {
        delete config.stageTest;
        config.targetSkill = { name: "daily-report-zh", baseline: { ref: fixture.ref, sha256: fixture.sha256 }, candidate: {} };
      }
      if (bad === "later-final-candidate") {
        delete config.stageTest;
        const ref = `oss://${getArtifactBucket()}/evolve/skills/tasks/SOURCE/candidate/package.zip`;
        config.targetSkill = { name: "daily-report-zh", candidate: { ref,
          artifact: { ref, sha256: "b".repeat(64), size: 1234, contentType: "application/zip" } } };
      }
      await repo.updateTaskConfig("SOURCE", config);
      const created = await start(bad === "missing-ref" ? { planSourceRef: undefined, caseInput: { round: 1, plan_result: planOutput() } } : {});
      expect(created.status).toBe(400);
      expect(dispatch).not.toHaveBeenCalled();
      expect(putObject).not.toHaveBeenCalled();
    });

  it.each(["plan", "stage_extension"])("freezes real %s output and runs builtin Optimize before postprocess without a fake Plan Step", async (kind) => {
    await seedSource(kind);
    const created = await start(); expect(created.status).toBe(201);
    const { taskId, stepId } = created.body;
    const steps = await repo.listSteps(taskId);
    expect(steps.map((step) => step.step_type)).toEqual(["optimize"]);
    const config = JSON.parse((await repo.findTask(taskId))!.config_json);
    expect(config.stageTest.planResultSource).toEqual({
      producer: { taskId: "SOURCE", stepId: "SOURCE-PLAN", userId: "owner", botId: "bot", stepType: kind }, output: planOutput(),
    });
    expect(config.caseInput.plan_result).toEqual(planOutput());
    const optimizeInput = await input(taskId, stepId);
    expect(optimizeInput.inputs.diagnoses).toEqual([{ taskId: "SOURCE", role: "primary", diagnose: null,
      plan: { stepId: "SOURCE-PLAN", output: planOutput() } }]);
    // Later source edits cannot change the already frozen test input.
    await repo.reviseSucceededStep("SOURCE-PLAN", { output: { ...planOutput(), spec: { version: "v1", content_type: "text", content: "later" } } });
    expect((await input(taskId, stepId)).inputs.diagnoses).toEqual(optimizeInput.inputs.diagnoses);
    const builtin = { diff: { summary: "默认优化真实回报", files: [] }, metrics: [],
      baseline: Object.fromEntries(["train", "test"].map((role) => [role, { role, producerStepId: stepId,
        source: "generated", ownerUserId: "owner", domainId: role.toUpperCase(), benchRunId: `run-${role}`, metrics: {} }])),
      roundDecision: { stop: true, reason: "测试一轮" } };
    const reported = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "succeeded", output: builtin }),
    });
    expect(reported.status).toBe(200);
    const post = (await repo.listSteps(taskId)).find((step) => step.step_type === "stage_extension")!;
    expect(post).toBeTruthy();
    const postInput = await input(taskId, post.step_id);
    expect(postInput.input.plan_result).toEqual(planOutput());
    expect(postInput.input.builtin_result).toMatchObject(builtin);
    expect((await repo.listSteps(taskId)).map((step) => step.step_type)).toEqual(["optimize", "stage_extension"]);
  });

  it.each(["owner", "bot", "task-running", "step-failed", "wrong-step", "wrong-type", "run-missing", "run-stage", "run-mode",
    "manual", "extra-ref", "unsafe-ref", "null-ref", "ref-on-plan", "synthetic", "step-task", "run-task", "schema", "empty-spec",
    "unpublished", "template-owner", "template-domain", "template-version"])("rejects %s before writes or dispatch", async (bad) => {
    await seedSource(bad.startsWith("run-") ? "stage_extension" : bad === "wrong-type" ? "diagnose" : "plan",
      bad === "owner" ? "other" : "owner", bad === "bot" ? "other" : "bot");
    const body: Record<string, unknown> = {};
    if (bad === "task-running") await repo.updateTaskState({ taskId: "SOURCE", status: "running" });
    if (bad === "step-failed") await repo.updateStepStatus("SOURCE-PLAN", { status: "failed" });
    if (bad === "wrong-step") body.planSourceRef = { taskId: "SOURCE", stepId: "missing" };
    if (bad === "step-task") await db.exec("UPDATE ce_steps SET task_id = 'OTHER' WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "synthetic") await db.exec("UPDATE ce_steps SET command = 'stage-test supplied Plan result' WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "run-missing") await db.exec("DELETE FROM ce_stage_extension_runs WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "run-stage") await db.exec("UPDATE ce_stage_extension_runs SET stage_key = 'diagnose' WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "run-mode") await db.exec("UPDATE ce_stage_extension_runs SET extension_mode = 'preprocess' WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "run-task") await db.exec("UPDATE ce_stage_extension_runs SET task_id = 'OTHER' WHERE step_id = ?", ["SOURCE-PLAN"]);
    if (bad === "manual") body.caseInput = { round: 1, plan_result: planOutput() };
    if (bad === "extra-ref") body.planSourceRef = { taskId: "SOURCE", stepId: "SOURCE-PLAN", url: "https://example.test/plan" };
    if (bad === "unsafe-ref") body.planSourceRef = { taskId: "../SOURCE", stepId: "SOURCE-PLAN" };
    if (bad === "null-ref") body.planSourceRef = null;
    if (bad === "ref-on-plan") await db.exec("UPDATE ce_stage_skill_implementations SET stage_key = 'plan' WHERE implementation_id = ?", ["IMPL-OPT"]);
    if (bad === "schema") await repo.reviseSucceededStep("SOURCE-PLAN", { output: { goal: {} } });
    if (bad === "empty-spec") await repo.reviseSucceededStep("SOURCE-PLAN", { output: { ...planOutput(), spec: { version: "v0", content_type: "text", content: " " } } });
    if (bad === "unpublished") await templates.update("owner", "TRAIN", "daily", { status: "draft" });
    if (bad.startsWith("template-")) {
      const value = planOutput();
      if (bad === "template-owner") value.benchCases.items[0].template.ownerUserId = "other";
      if (bad === "template-domain") value.benchCases.items[0].template.domainId = "TEST";
      if (bad === "template-version") value.benchCases.items[0].template.version = 2;
      await repo.reviseSucceededStep("SOURCE-PLAN", { output: value });
    }
    const tasksBefore = await repo.listTasks();
    const result = await start(body); expect(result.status).toBeGreaterThanOrEqual(400); expect(result.status).toBeLessThan(500);
    if (bad === "manual") {
      expect(result.status).toBe(400);
      expect(result.body).toEqual({ error: "不能混用 planSourceRef 和手工 plan_result" });
    }
    expect(await repo.listTasks()).toEqual(tasksBefore);
    expect((await stages.findImplementation("IMPL-OPT"))!.integration_test_status).toBe("untested");
    expect(dispatch).not.toHaveBeenCalled(); expect(putObject).not.toHaveBeenCalled();
  });

  it("preserves the historical manual Plan test path only when no ref is supplied", async () => {
    const created = await start({ planSourceRef: undefined, caseInput: { round: 1, plan_result: planOutput() } });
    expect(created.status).toBe(201);
    const steps = await repo.listSteps(created.body.taskId);
    expect(steps.map((step) => step.step_type)).toEqual(["plan", "optimize"]);
    expect(JSON.parse((await repo.findTask(created.body.taskId))!.config_json).stageTest).not.toHaveProperty("planResultSource");
  });
});
