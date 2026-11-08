import { afterEach, beforeEach, expect, it, vi } from "vitest";

const temporaryDirectories = vi.hoisted(() => [] as string[]);
vi.mock("@avernet/clawweb-shared/server/env", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@avernet/clawweb-shared/server/env")>();
  return {
    ...actual,
    createConfiguredTemporaryDirectory: async (scope: string) => {
      const directory = await actual.createConfiguredTemporaryDirectory(scope);
      temporaryDirectories.push(directory);
      return directory;
    },
  };
});
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { once } from "node:events";
import { readdirSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase, repo: EvolveRepository, base: string;
let server: ReturnType<express.Application["listen"]>;
const objects = new Map<string, Buffer>();
const dispatch = vi.fn(async () => ({ runId: "run", sessionId: "session" }));
const store = {
  putObject: vi.fn(async (key: string, bytes: Buffer | Uint8Array | string) => { objects.set(key, Buffer.from(bytes)); return { etag: null }; }),
  getObject: vi.fn(async (key: string) => { if (!objects.has(key)) throw Object.assign(new Error("missing"), { code: "NoSuchKey" }); return { content: objects.get(key)!, contentType: null, etag: null }; }),
  createSignedUrl: vi.fn(async (key: string) => `https://artifact.example.test/${key}`),
};
beforeEach(async () => {
  objects.clear(); dispatch.mockClear(); temporaryDirectories.length = 0;
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  await db.exec(`CREATE TABLE ac_entity_device_binding (id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT)`);
  await db.exec(`CREATE TABLE ac_bots (id INTEGER PRIMARY KEY, bot_id TEXT, bot_name TEXT, owner_id TEXT, entity_id TEXT, entity_type TEXT, is_delete INTEGER, active_engine TEXT, bot_type TEXT, status TEXT, binding_id INTEGER, env TEXT)`);
  await db.exec(`INSERT INTO ac_entity_device_binding VALUES (1,'arca','sandbox',NULL,'active','pre')`);
  await db.exec(`INSERT INTO ac_bots VALUES (1,'bot','Bot','owner','owner','staff',0,'openclaw','personal','active',1,'pre')`);
  const app = express(); app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, { artifactStore: store, artifactUrlStore: store, dispatch,
    skillAssetRepo: new SkillAssetRepository(db),
    hostLocalSkills: { listLocalSkills: vi.fn(), replaceLocalSkill: vi.fn(), exportLocalSkill: vi.fn(async () => ({
      packageBytes: Buffer.from("skill-package"), sha256: `sha256:${"a".repeat(64)}`, displayName: "daily" })) },
    stageSkillRepo: new StageSkillRepository(db), modelConfig: { defaultModel: "test-model", models: ["test-model"] } }));
  server = app.listen(0, "127.0.0.1"); await once(server, "listening");
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
});
afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await db.close(); });
async function request(path: string, body?: Record<string, unknown> | FormData, owner = "owner", key?: string) {
  return fetch(base + path, { method: body ? "POST" : "GET", headers: { "X-User-Id": owner,
    ...(key ? { "Idempotency-Key": key } : {}), ...(body && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}) },
    ...(body ? { body: body instanceof FormData ? body : JSON.stringify(body) } : {}) });
}
async function upload() {
  const bytes = await new JSZip().file("规范/日报.md", "金额单位为元，不足两位补零", { createFolders: false }).generateAsync({ type: "uint8array", compression: "STORE" });
  const form = new FormData(); form.set("package", new Blob([bytes as Uint8Array<ArrayBuffer>]), "materials.zip");
  const response = await request("/reference-materials/uploads", form);
  expect(response.status, await response.clone().text()).toBe(201);
  return response.json() as Promise<{ materialId: string }>;
}
function task(uploadId?: string) { return { taskType: "full", taskName: "参考业务材料优化", userId: "owner", botId: "bot", botEnv: "pre",
  inputMode: "direct_goal", goal: "完善日报输出", model: "test-model", maxRounds: 2,
  ...(uploadId ? { referenceMaterialsUploadId: uploadId } : {}) }; }

it("does not disclose temporary-directory configuration in upload errors", async () => {
  const missingParent = join(tmpdir(), "clawevolve-materials-missing-path");
  vi.stubEnv("CLAWWEB_TEMP_DIR", missingParent);
  try {
    const bytes = await new JSZip().file("规范/test.md", "test").generateAsync({ type: "uint8array" });
    const form = new FormData();
    form.set("package", new Blob([bytes as Uint8Array<ArrayBuffer>]), "materials.zip");
    const response = await request("/reference-materials/uploads", form);
    const body = await response.text();
    const result = JSON.parse(body) as { error: string };
    expect(response.status, body).toBe(500);
    expect(result.error).toBe("临时目录不可用或配置无效");
    expect(result.error).not.toContain(missingParent);
    expect(result.error).not.toContain("CLAWWEB_TEMP_DIR");
    expect(result.error).not.toContain("parent=");
  } finally {
    vi.unstubAllEnvs();
  }
});

it("stages reference-material uploads under the configured temporary directory", async () => {
  const parent = mkdtempSync(join(tmpdir(), "clawevolve-materials-temp-"));
  vi.stubEnv("CLAWWEB_TEMP_DIR", parent);
  try {
    await upload();
    const directory = temporaryDirectories.at(-1)!;
    expect(directory.startsWith(resolve(parent))).toBe(true);
    expect(dirname(directory)).toBe(resolve(parent));
    await vi.waitFor(() => expect(readdirSync(parent)).toEqual([]));
  } finally {
    vi.unstubAllEnvs();
    rmSync(parent, { recursive: true, force: true });
  }
});

it("uploads and delivers the same signed material to native Plan and subsequent Optimize", async () => {
  const uploaded = await upload();
  const created = await request("/tasks", task(uploaded.materialId));
  expect(created.status, await created.clone().text()).toBe(201);
  const result = await created.json();
  const frozen = result.config.reference_materials;
  expect(frozen.taskId).toBe(result.task_id);
  const steps = await repo.listSteps(result.task_id);
  expect(steps[0].command).toContain("--reference-materials-version 1");
  const plan = await (await request(`/internal/tasks/${result.task_id}/steps/${steps[0].step_id}/input`)).json();
  expect(plan.reference_materials.sha256).toBe(frozen.sha256);
  expect(plan.reference_materials.download.url).toContain(frozen.objectKey);
  const optimize = await repo.createStep({ stepId: "STEP-OPT", taskId: result.task_id, stepType: "optimize", stepNo: 2, roundNo: 2, command: "/optimize" });
  const next = await (await request(`/internal/tasks/${result.task_id}/steps/${optimize.step_id}/input`)).json();
  expect(next.reference_materials).toEqual(plan.reference_materials);
  expect(dispatch).toHaveBeenCalledTimes(1);
  const view = await request(`/tasks/${result.task_id}/reference-materials`);
  expect((await view.json()).files[0].path).toBe("规范/日报.md");
  expect((await request(`/tasks/${result.task_id}/reference-materials`, undefined, "stranger")).status).toBe(403);
});
it("rejects a foreign upload, forged identity and unsupported task type without dispatching", async () => {
  const uploaded = await upload();
  const foreign = await request("/tasks", { ...task(uploaded.materialId), userId: "other" }, "other");
  expect(foreign.ok).toBe(false);
  const unsupported = await request("/tasks", { ...task(uploaded.materialId), taskType: "diagnose" });
  expect(unsupported.status).toBe(422);
  expect(dispatch).not.toHaveBeenCalled();
});
it.each([false, true])("preserves ordinary new-Task semantics when creation is resubmitted (materials=%s)", async withMaterials => {
  const uploaded = withMaterials ? await upload() : null;
  const payload = task(uploaded?.materialId);
  const responses = [await request("/tasks", payload, "owner", "same-request"), await request("/tasks", payload, "owner", "same-request")];
  expect(responses.map(response => response.status)).toEqual([201, 201]);
  const results = await Promise.all(responses.map(response => response.json()));
  expect(results[0].task_id).not.toBe(results[1].task_id);
  expect(results.every(result => !result.task_id.startsWith("EV-M-"))).toBe(true);
  expect(dispatch).toHaveBeenCalledTimes(2);
  if (withMaterials) {
    expect(results[0].config.reference_materials.sha256).toBe(results[1].config.reference_materials.sha256);
    expect(results[0].config.reference_materials.objectKey).not.toBe(results[1].config.reference_materials.objectKey);
    for (const result of results) expect(result.config.reference_materials.taskId).toBe(result.task_id);
  }
});
it("keeps tasks without materials on the existing command/input path", async () => {
  const response = await request("/tasks", task());
  expect(response.status, await response.clone().text()).toBe(201);
  const result = await response.json();
  expect(result.config).not.toHaveProperty("reference_materials");
  const steps = await repo.listSteps(result.task_id);
  expect(steps[0].command).not.toContain("reference-materials-version");
});

it("delivers the same optional resource through a custom Stage input without persisting signed URLs", async () => {
  const uploaded = await upload();
  const created = await request("/tasks", task(uploaded.materialId));
  const result = await created.json();
  const stages = new StageSkillRepository(db);
  await stages.createImplementation({ implementationId: "IMPL-MATERIAL", stageSkillId: "SKILL-MATERIAL", ownerUserId: "owner",
    displayName: "材料预处理", stage: "plan", mode: "preprocess", versionNo: 1,
    packageRef: "oss://clawevolve-artifacts/stage-implementations/material/package.zip",
    packageSha256: "a".repeat(64), staticValidation: { status: "passed" } });
  await stages.registerImplementation("IMPL-MATERIAL");
  const config = { ...result.config, stageExtensions: { plan: { preprocess: { enabled: true, implementationId: "IMPL-MATERIAL", ownerUserId: "owner" } } } };
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(config), result.task_id]);
  await repo.createStep({ taskId: result.task_id, stepId: "STEP-CUSTOM", stepType: "stage_extension", stepNo: 2, command: "/stage" });
  await stages.createExtensionRun({ taskId: result.task_id, stepId: "STEP-CUSTOM", stage: "plan", mode: "preprocess", implementationId: "IMPL-MATERIAL" });
  const response = await request(`/internal/tasks/${result.task_id}/steps/STEP-CUSTOM/input`);
  expect(response.status, await response.clone().text()).toBe(200);
  const input = await response.json();
  expect(input.protocolVersion).toBe("clawevolve.stage-runtime/v1");
  expect(input.input.reference_materials.sha256).toBe(result.config.reference_materials.sha256);
  expect(input.input.reference_materials.download.method).toBe("GET");
  expect((await repo.findTask(result.task_id))!.config_json).not.toContain("https://artifact");
});

it("provides native Hardening businessInput with the prepared target and task reference materials", async () => {
  const uploaded = await upload();
  const created = await request("/tasks", task(uploaded.materialId));
  const result = await created.json();
  const sha256 = "a".repeat(64), workspacePath = `/home/admin/.openclaw/clawevolve_workspaces/${result.task_id}/workspace`;
  const config = { ...result.config, targetSkill: { assetId: "asset", skillId: "daily", name: "daily",
    baseline: { ref: "oss://clawevolve-artifacts/baseline.zip", sha256 }, candidate: {
      ref: "oss://clawevolve-artifacts/candidate.zip", prepared: { workspacePath,
        skillPath: `${workspacePath}/skills/skills-local/daily`, preparedByStepId: "PREP", baselineSha256: sha256 } } } };
  await db.exec("UPDATE ce_tasks SET task_type = 'hardening', config_json = ? WHERE task_id = ?", [JSON.stringify(config), result.task_id]);
  await repo.createStep({ taskId: result.task_id, stepId: "STEP-HARDEN", stepType: "hardening", stepNo: 2, command: "/harden" });
  const response = await request(`/internal/tasks/${result.task_id}/steps/STEP-HARDEN/input`);
  expect(response.status, await response.clone().text()).toBe(200);
  const input = await response.json();
  expect(input.businessInput.reference_materials.sha256).toBe(result.config.reference_materials.sha256);
  expect(input.businessInput.reference_materials.download.method).toBe("GET");
  expect(input.businessInput.target_skill.path).toBe(`${workspacePath}/skills/skills-local/daily`);
});

it.each([['full', 'diagnose_goal', 'diagnose'], ['full', 'direct_goal', 'plan'], ['hardening', 'diagnose_goal', 'hardening']])(
  'carries materials through candidate preparation into Skill %s (%s)', async (taskType, inputMode, nextType) => {
  await new SkillAssetRepository(db).createAsset({ assetId: 'SKILL-MATERIAL', versionId: 'SKVER-BASE',
    ownerUserId: 'owner', botId: 'bot', botEnv: 'pre', externalSkillId: 'daily', displayName: '日报',
    packageRef: 'oss://clawevolve-artifacts/skills/daily.zip', packageSha256: `sha256:${'a'.repeat(64)}` })
  const uploaded = await upload()
  const response = await request('/tasks', { ...task(uploaded.materialId), targetSkillAssetId: 'SKILL-MATERIAL',
    taskType, inputMode, judgeBackend: 'subagent', diagnoseIntent: '检查日报真实执行', maxSessions: 3 })
  expect(response.status, await response.clone().text()).toBe(201)
  const result = await response.json()
  expect(result.config.targetSkill.assetId).toBe('SKILL-MATERIAL')
  expect(result.config.reference_materials.taskId).toBe(result.task_id)
  const prepare = (await repo.listSteps(result.task_id))[0]
  expect(prepare.step_type).toBe('skill_prepare')
  const workspace = `/home/admin/.openclaw/clawevolve_workspaces/${result.task_id}/workspace`
  const prepared = await request(`/internal/tasks/${result.task_id}/steps/${prepare.step_id}/report`, {
    status: 'succeeded', output: { prepared: true, workspace, targetSkillPath: `${workspace}/skills/skills-local/daily` },
  })
  expect(prepared.status, await prepared.clone().text()).toBe(200)
  const next = (await prepared.json()).nextStep
  expect(next.stepType).toBe(nextType)
  const step = await repo.findStep(next.stepId)
  expect(step!.command).toContain('--reference-materials-version 1')
  const input = await (await request(`/internal/tasks/${result.task_id}/steps/${next.stepId}/input`)).json()
  expect(input.reference_materials.sha256).toBe(result.config.reference_materials.sha256)
  expect(input.task.config.targetSkill.candidate.prepared.workspacePath).toBe(workspace)
})

it.each([false, true])('preserves ordinary failed-Step retry with optional materials (%s)', async withMaterials => {
  const uploaded = withMaterials ? await upload() : null
  const created = await request('/tasks', { ...task(uploaded?.materialId), inputMode: 'diagnose_goal',
    judgeBackend: 'subagent', diagnoseIntent: '检查日报', maxSessions: 3 })
  const result = await created.json()
  let step = (await repo.listSteps(result.task_id))[0]
  expect(step.step_type).toBe('diagnose')
  for (let attempt = 0; attempt < 2; attempt++) {
    await repo.updateStepStatus(step.step_id, { status: 'failed', errorMessage: 'test failure' })
    const retry = await request(`/tasks/${result.task_id}/steps/${step.step_id}/retry`, {})
    expect(retry.status, await retry.clone().text()).toBe(201)
    step = (await repo.findStep((await retry.json()).step.stepId))!
    expect(step.task_id).toBe(result.task_id)
    expect(step.command.match(/--reference-materials-version 1/g) ?? []).toHaveLength(withMaterials ? 1 : 0)
    const input = await (await request(`/internal/tasks/${result.task_id}/steps/${step.step_id}/input`)).json()
    if (withMaterials) expect(input.reference_materials.sha256).toBe(result.config.reference_materials.sha256)
    else expect(input).not.toHaveProperty('reference_materials')
    expect(await db.query('SELECT task_id FROM ce_tasks')).toHaveLength(1)
  }
})

it('retains the material guard and frozen implementation when retrying a custom Stage', async () => {
  const uploaded = await upload()
  const result = await (await request('/tasks', task(uploaded.materialId))).json()
  for (const step of await repo.listSteps(result.task_id)) await repo.updateStepStatus(step.step_id, { status: 'succeeded' })
  const stages = new StageSkillRepository(db)
  await stages.createImplementation({ implementationId: 'IMPL-RETRY', stageSkillId: 'SKILL-RETRY', ownerUserId: 'owner',
    displayName: '材料预处理', stage: 'plan', mode: 'preprocess', versionNo: 1,
    packageRef: 'oss://clawevolve-artifacts/stage-implementations/retry/package.zip', packageSha256: 'a'.repeat(64),
    staticValidation: { status: 'passed' } })
  await stages.registerImplementation('IMPL-RETRY')
  await repo.createStep({ taskId: result.task_id, stepId: 'STEP-FAILED-CUSTOM', stepType: 'stage_extension', stepNo: 2,
    command: '/clawevolve-stage --action execute --task-id x --step-id STEP-FAILED-CUSTOM --reference-materials-version 1' })
  await stages.createExtensionRun({ taskId: result.task_id, stepId: 'STEP-FAILED-CUSTOM', stage: 'plan', mode: 'preprocess', implementationId: 'IMPL-RETRY' })
  await repo.updateStepStatus('STEP-FAILED-CUSTOM', { status: 'failed', errorMessage: 'test failure' })
  const retry = await request(`/tasks/${result.task_id}/steps/STEP-FAILED-CUSTOM/retry`, {})
  expect(retry.status, await retry.clone().text()).toBe(201)
  const step = (await retry.json()).step
  expect(step.command).toContain('--reference-materials-version 1')
  expect((await stages.findExtensionRun(step.stepId))!.implementation_id).toBe('IMPL-RETRY')
})

it.each([false, true])('preserves ephemeral API Judge credentials across material task dispatch (forceMessage=%s)', async forceMessage => {
  await db.exec("UPDATE ac_entity_device_binding SET device_provider = 'baas' WHERE id = 1")
  const uploaded = await upload()
  const response = await request('/tasks', { ...task(uploaded.materialId), inputMode: 'diagnose_goal',
    judgeBackend: 'api', apiKey: 'ephemeral-test-key', diagnoseIntent: '检查日报', forceMessage })
  expect(response.status, await response.clone().text()).toBe(201)
  const result = await response.json()
  const step = (await repo.listSteps(result.task_id))[0]
  expect(step.command).not.toContain('ephemeral-test-key')
  expect((await repo.findTask(result.task_id))!.config_json).not.toContain('ephemeral-test-key')
  const sent = dispatch.mock.calls.at(-1)![0]
  expect(sent.command).toContain('--reference-materials-version 1')
  expect(sent.forceMessage).toBe(forceMessage)
  expect(sent.command).not.toContain('--api-key')
  expect(sent.secrets).toEqual({ diagnoseApiKey: 'ephemeral-test-key' })
})

it.each([
  { taskType: 'full', input: { type: 'insight_improvement' } },
  { taskType: 'hardening', input: { type: 'insight_improvement' } },
  { taskType: 'full', improvementId: 1 },
])('rejects Insight inputs at the material boundary: %j', async fields => {
  const uploaded = await upload()
  expect((await request('/tasks', { ...task(uploaded.materialId), ...fields })).status).toBe(422)
  expect(dispatch).not.toHaveBeenCalled()
})
