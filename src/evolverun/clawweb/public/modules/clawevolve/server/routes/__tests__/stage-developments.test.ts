import { afterEach, describe, expect, it } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { createStageSkillsRouter } from "../stage-skills.js";

let server: ReturnType<express.Application["listen"]> | undefined;
let db: SqliteDatabase | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  await db?.close(); db = undefined;
});

async function setup() {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  const repo = new StageSkillRepository(db);
  const objects = new Map<string, Buffer>();
  const app = express(); app.use(express.json());
  app.use("/api/evolve", createStageSkillsRouter({ repo, artifactStore: {
    putObject: async (key: string, bytes: Buffer) => { objects.set(key, bytes); return { etag: null }; },
    getObject: async (key: string) => ({ content: objects.get(key)!, etag: null, contentType: "application/zip" }),
  } as never }));
  server = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
  const request = (path: string, init: RequestInit = {}, owner = "owner") => fetch(url + path, {
    ...init, headers: { "X-User-Id": owner, ...(init.headers ?? {}) },
  });
  const create = async () => {
    const response = await request("/stage-developments", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: "diagnose", mode: "preprocess", flow: "skill_evolution" }),
    });
    expect(response.status).toBe(201); return response.json();
  };
  const upload = async (id: string, stage = "diagnose", owner = "owner") => {
    const bytes = await new JSZip().file("SKILL.md", "# 自定义处理\n检查输入并返回处理结果。\n").generateAsync({ type: "uint8array" });
    const body = new FormData();
    body.set("stageSkillId", id); body.set("stage", stage); body.set("mode", "preprocess");
    body.set("package", new Blob([bytes], { type: "application/zip" }), "custom.zip");
    return request("/stage-skills/uploads", { method: "POST", body }, owner);
  };
  return { repo, objects, request, create, upload };
}

describe("independent Stage development record", () => {
  it("freezes the new Plan business contract from the owned development without upgrading old versions", async () => {
    const test = await setup();
    for (const flow of ["skill_evolution", "bot_evolution"]) {
      const response = await test.request("/stage-developments", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage: "plan", mode: "replace", flow }),
      });
      expect(response.status).toBe(201);
      const draft = await response.json();
      const old = await test.repo.createImplementation({
        stageSkillId: draft.stageSkillId, implementationId: `OLD-${flow}`, ownerUserId: "owner",
        displayName: draft.displayName, stage: "plan", mode: "replace", versionNo: 1,
        packageRef: "oss://clawevolve-artifacts/old.zip", packageSha256: "a".repeat(64),
        staticValidation: { status: "passed", manifest: { entrypoint: "SKILL.md" } },
      });
      const bytes = await new JSZip().file("SKILL.md", "# 规划能力\n分析输入并制定方案。\n").generateAsync({ type: "uint8array" });
      const body = new FormData();
      body.set("stageSkillId", draft.stageSkillId); body.set("stage", "plan"); body.set("mode", "replace");
      body.set("executionContract", "clawevolve.plan-business/v1");
      body.set("package", new Blob([bytes], { type: "application/zip" }), "plan.zip");
      const uploaded = await test.request("/stage-skills/uploads", { method: "POST", body });
      expect(uploaded.status).toBe(201);
      const current = await uploaded.json();
      expect(current.version).toBe("v2");
      expect(current.staticValidation.executionContract).toBe(flow === "skill_evolution" ? "clawevolve.plan-business/v1" : undefined);
      const historical = await (await test.request(`/stage-skills/${old.implementation_id}`)).json();
      expect(historical.staticValidation.executionContract).toBeUndefined();
    }
  });

  it("persists selection before upload, resumes it, and binds all versions/tests to it", async () => {
    const test = await setup(); const draft = await test.create();
    expect(draft).toMatchObject({ ownerId: "owner", stage: "diagnose", mode: "preprocess", flow: "skill_evolution" });
    expect((await test.repo.listImplementations("owner"))).toEqual([]);
    expect(await (await test.request("/stage-developments")).json()).toMatchObject({ items: [{ stageSkillId: draft.stageSkillId }] });
    expect(await (await test.request(`/stage-developments/${draft.stageSkillId}`)).json()).toEqual(draft);
    const uploaded = await test.upload(draft.stageSkillId);
    expect(uploaded.status).toBe(201);
    const v1 = await uploaded.json();
    expect(v1).toMatchObject({ stageSkillId: draft.stageSkillId, displayName: draft.displayName, version: "v1" });
    await test.repo.updateIntegrationTest(v1.implementationId, "TEST-1", "test_passed");
    const second = await test.upload(draft.stageSkillId); expect(second.status).toBe(201);
    const v2 = await second.json();
    expect(v2).toMatchObject({ stageSkillId: draft.stageSkillId, version: "v2", integrationTestStatus: "untested", integrationTestTaskId: null });
    expect(await test.repo.findImplementation(v1.implementationId)).toMatchObject({ integration_test_task_id: "TEST-1", integration_test_status: "test_passed" });
  });

  it("downloads the selected development package without a user manifest or fixed implementation directory", async () => {
    const test = await setup(); const draft = await test.create();
    const response = await test.request(`/stage-skills/developer-package?developmentId=${draft.stageSkillId}`);
    expect(response.status).toBe(200);
    const zip = await JSZip.loadAsync(await response.arrayBuffer());
    expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
    const guide = await zip.file("SKILL.md")!.async("string");
    expect(guide).toContain("Skill 自进化"); expect(guide).toContain("诊断");
  });

  it("serves the selected background for every flow, Stage and mode without changing JSON delivery", async () => {
    const test = await setup();
    for (const flow of ["skill_evolution", "bot_evolution"]) {
      for (const [stage, name] of [["diagnose", "诊断"], ["plan", "规划"], ["optimize", "优化"]]) {
        for (const [mode, suffix] of [["preprocess", "前置"], ["postprocess", "后置"], ["replace", ""]]) {
          const response = await test.request(`/stage-skills/developer-package?flow=${flow}&stage=${stage}&mode=${mode}`);
          expect(response.status).toBe(200);
          const zip = await JSZip.loadAsync(await response.arrayBuffer());
          expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
          const guide = await zip.file("SKILL.md")!.async("string");
          expect(guide).toContain(`本次需要你开发一个“${name}${suffix} Skill”`);
          expect(guide).toContain(`${flow === "skill_evolution" ? "Skill" : "Bot"} 自进化任务用于`);
          expect(guide.match(/^## /gm)).toHaveLength(4);
          const input = JSON.parse([...guide.matchAll(/```json\n([\s\S]*?)\n```/g)][0]![1]!);
          expect(Boolean(input.target_skill)).toBe(flow === "skill_evolution");
          expect(Boolean(input.builtin_result)).toBe(mode === "postprocess");
          expect(guide).toContain("<form>");
        }
      }
    }
  });

  it("uses the frozen development selection even if query parameters request another background", async () => {
    const test = await setup(); const draft = await test.create();
    const response = await test.request(`/stage-skills/developer-package?developmentId=${draft.stageSkillId}&flow=bot_evolution&stage=plan&mode=replace`);
    expect(response.status).toBe(200);
    const zip = await JSZip.loadAsync(await response.arrayBuffer());
    const guide = await zip.file("SKILL.md")!.async("string");
    expect(guide).toContain("Skill 自进化任务用于");
    expect(guide).toContain("本次需要你开发一个“诊断前置 Skill”");
    expect(guide).not.toContain("本次需要你开发一个“规划 Skill”");
  });

  it("rejects another owner's record and mismatched Stage instead of trusting uploaded fields", async () => {
    const test = await setup(); const draft = await test.create();
    expect((await test.request(`/stage-developments/${draft.stageSkillId}`, {}, "other")).status).toBe(404);
    expect((await test.request(`/stage-skills/developer-package?developmentId=${draft.stageSkillId}`, {}, "other")).status).toBe(404);
    expect((await test.upload(draft.stageSkillId, "diagnose", "other")).status).toBe(404);
    expect((await test.upload(draft.stageSkillId, "plan")).status).toBe(404);
    expect((await test.upload("")).status).toBe(404);
    expect(test.objects.size).toBe(0);
  });

  it("deletes an empty draft but protects a development with uploaded versions", async () => {
    const test = await setup(); const draft = await test.create();
    expect((await test.request(`/stage-developments/${draft.stageSkillId}`, { method: "DELETE" })).status).toBe(200);
    expect(await test.repo.findDevelopment(draft.stageSkillId)).toBeNull();
    const uploadedDraft = await test.create(); await test.upload(uploadedDraft.stageSkillId);
    expect((await test.request(`/stage-developments/${uploadedDraft.stageSkillId}`, { method: "DELETE" })).status).toBe(409);
  });
});
