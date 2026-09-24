import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { createEvolveRouter } from "../evolve.js";

function planOutput() {
  return {
    goal: { summary: "改善日报", metrics: [] }, spec: { version: "v0", content_type: "text", content: "构造的测试方案" },
    benchDomains: { trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" },
    benchCases: { trainCount: 1, testCount: 1, items: ["train", "test"].map((split) => ({
      sourceCaseId: `case-${split}`, taskId: `bench-${split}`, split,
      template: { ownerUserId: "owner", domainId: split.toUpperCase(), templateName: "daily", version: 1 },
    })) }, extraProducerOutput: { keep: "完整输出不裁剪" },
  };
}

describe("Optimize StageTest constructed input", () => {
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
  async function start(body: Record<string, unknown> = {}) {
    const response = await fetch(`${base}/stage-skills/IMPL-OPT/integration-tests`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner" },
      body: JSON.stringify({ botId: "bot", caseInput: { round: 1, plan_result: planOutput() }, ...body }),
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

  it("prepares a task-owned Skill copy before Optimize and delivers that same copy to postprocess", async () => {
    await stages.createDevelopment({ stageSkillId: "STAGE-OPT", ownerUserId: "owner", displayName: "Skill优化后置",
      flow: "skill_evolution", stage: "optimize", mode: "postprocess" });
    const created = await start();
    expect(created.status).toBe(201);
    const { taskId, stepId } = created.body;
    const prepare = await input(taskId, stepId);
    expect(prepare).toMatchObject({ protocolVersion: "clawevolve.skill-candidate/v1", action: "prepare",
      candidateWorkspace: `/home/admin/.openclaw/clawevolve_workspaces/${taskId}/workspace`,
      targetSkill: { skillId: "fixture:skill-description-v2" } });
    expect((await repo.listSteps(taskId)).map(s => s.step_type)).toEqual(["plan", "skill_prepare"]);
    expect(dispatch.mock.calls[0][0].command).not.toContain("--stage optimize");
    const unsafePrepare = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", output: { prepared: true,
        workspace: "/home/admin/.openclaw/workspace", targetSkillPath: "/home/admin/.openclaw/workspace/skills/skills-local/daily-report-zh" } }),
    });
    expect(unsafePrepare.status).toBe(422);
    expect((await repo.listSteps(taskId)).map(s => s.step_type)).toEqual(["plan", "skill_prepare"]);
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
        output: { diff: { summary: "候选副本变化", files: [{ path: "skills/skills-local/stage-test-text-summary/SKILL.md", change: "modified" }] }, metrics: [],
          baseline: Object.fromEntries(["train", "test"].map(role => [role, { role, producerStepId: optimize.step_id,
            source: "generated", ownerUserId: "owner", domainId: role.toUpperCase(), benchRunId: `run-${role}`, metrics: {} }])),
          roundDecision: { stop: true, reason: "一轮测试" } } }),
    });
    expect(reported.status).toBe(200);
    const post = (await repo.listSteps(taskId)).find(s => s.step_type === "stage_extension")!;
    const postInput = (await input(taskId, post.step_id)).input;
    expect(postInput.builtin_result.diff.files).toEqual([
      { path: "skills/skills-local/stage-test-text-summary/SKILL.md", change: "modified" },
    ]);
    expect(postInput.target_skill).toMatchObject({
      workspace: prepare.candidateWorkspace, path: prepare.targetSkill.path, name: "stage-test-text-summary" });
  });

  it("uses the supplied Plan without requiring any historical source task", async () => {
    const created = await start();
    expect(created.status).toBe(201);
    const { taskId, stepId } = created.body;
    expect(await repo.listTasks()).toHaveLength(1);
    const steps = await repo.listSteps(taskId);
    expect(steps.map(step => step.step_type)).toEqual(["plan", "optimize"]);
    expect(steps[0].command).toBe("stage-test supplied Plan result");
    expect((await input(taskId, stepId)).inputs.diagnoses[0].plan.output).toEqual(planOutput());
  });

  it.each([undefined, {}, { goal: { summary: "incomplete" } }])(
    "rejects malformed constructed Plan input before writes", async plan => {
      const result = await start({ caseInput: { round: 1, plan_result: plan } });
      expect(result.status).toBe(400);
      expect(await repo.listTasks()).toHaveLength(0);
      expect(dispatch).not.toHaveBeenCalled();
      expect(putObject).not.toHaveBeenCalled();
    });
});
