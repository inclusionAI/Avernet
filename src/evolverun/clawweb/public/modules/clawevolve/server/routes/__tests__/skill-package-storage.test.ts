import { afterEach, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { createClawevolveModule } from "../../create-module.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";

let server: ReturnType<express.Application["listen"]> | undefined;
let db: SqliteDatabase;
afterEach(async () => {
  if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
  server = undefined;
  if (db) await db.close();
});

it("uploads a Stage package, persists its location and signs both implementation and hardening fixture from the dedicated store", async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  const objects = new Map<string, Buffer>();
  const store = {
    putObject: vi.fn(async (key: string, content: Buffer | Uint8Array | string) => {
      objects.set(key, Buffer.from(content)); return { etag: null };
    }),
    getObject: vi.fn(async (key: string) => ({ content: objects.get(key)!, contentType: "application/zip", etag: null })),
    createSignedUrl: vi.fn(async (key: string) => `https://download.example.test/${key}`),
  };
  const legacy = { putObject: vi.fn(), getObject: vi.fn(), createSignedUrl: vi.fn() };
  const module = createClawevolveModule({ db,
    hostLocalSkills: { listLocalSkills: vi.fn(), exportLocalSkill: vi.fn(), replaceLocalSkill: vi.fn() },
    publicBaseUrl: "https://workbench.example.test", trustedPublicOrigins: ["https://workbench.example.test"],
    dispatch: vi.fn(async () => ({ runId: "run-test", sessionId: "session-test" })),
    artifactStore: legacy, artifactUrlStore: legacy,
    skillPackageStorage: { bucket: "skill-packages", prefix: "packages/", store },
    modelConfig: { defaultModel: "test-model", models: ["test-model"] },
  });
  const app = express(); app.use(express.json()); app.use("/api/evolve", module.publicRouter);
  server = app.listen(0, "127.0.0.1"); await once(server, "listening");
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
  async function request(path: string, body?: Record<string, unknown> | FormData) {
    const response = await fetch(`${base}${path}`, { method: body ? "POST" : "GET",
      headers: { "X-User-Id": "owner", ...(body && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { body: body instanceof FormData ? body : JSON.stringify(body) } : {}),
    });
    const raw = await response.text();
    expect(response.ok, `${response.status}: ${raw}`).toBe(true);
    return JSON.parse(raw);
  }
  const development = await request("/stage-developments", {
    flow: "skill_hardening", stage: "hardening", mode: "replace", displayName: "Hardening test",
  });
  const bytes = await new JSZip().file("SKILL.md", "# Hardening\nRead supplied input and return a result.\n")
    .generateAsync({ type: "nodebuffer" });
  const form = new FormData(); form.set("stageSkillId", development.stageSkillId);
  form.set("stage", "hardening"); form.set("mode", "replace");
  form.set("package", new Blob([new Uint8Array(bytes)], { type: "application/zip" }), "skill.zip");
  const implementation = await request("/stage-skills/uploads", form);
  const stored = await new StageSkillRepository(db).findImplementation(implementation.implementationId);
  expect(stored!.package_ref).toBe(`oss://skill-packages/packages/stage-implementations/${implementation.implementationId}/v1/package.zip`);
  expect(stored!.package_sha256.replace(/^sha256:/, "")).toBe(createHash("sha256").update(bytes).digest("hex"));
  const content = await request(`/stage-skills/${implementation.implementationId}/content`);
  expect(content.selected.content).toContain("Read supplied input");
  const task = await request(`/stage-skills/${implementation.implementationId}/integration-tests`, {
    botId: "bot-fixture", caseInput: { goal: "Clarify the supplied Skill" },
  });
  const input = await request(`/internal/tasks/${task.taskId}/steps/${task.stepId}/input`);
  expect(input.protocolVersion).toBe("clawevolve.stage-runtime/v1");
  expect(input.implementation.package.url).toBe(`https://download.example.test/packages/stage-implementations/${implementation.implementationId}/v1/package.zip`);
  expect(input.resources.testSkillFixture.package.url)
    .toBe(`https://download.example.test/packages/stage-tests/${task.taskId}/fixtures/skill-description-v2/package.zip`);
  expect(input.input.target_skill.name).toBe("stage-test-text-summary");
  expect(legacy.putObject).not.toHaveBeenCalled();
  expect(legacy.getObject).not.toHaveBeenCalled();
  expect(legacy.createSignedUrl).not.toHaveBeenCalled();
});
