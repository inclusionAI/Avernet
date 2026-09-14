import { afterEach, expect, it } from "vitest";
import Database from "better-sqlite3";
import { once } from "node:events";
import { createServer } from "node:net";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startSinglebox } from "../../singlebox.js";
import type { SingleboxConfig } from "../config.js";

let root = "";
let app: Awaited<ReturnType<typeof startSinglebox>> | undefined;
afterEach(async () => { await app?.shutdown(); app = undefined; if (root) rmSync(root, { recursive: true, force: true }); });

it("uses readonly Backend Bots, starts local Bash, and advances Diagnose to Plan through the existing report route", async () => {
  root = mkdtempSync(join(tmpdir(), "ce-singlebox-"));
  const backendDb = join(root, "backend.db");
  const seed = new Database(backendDb);
  seed.exec(`CREATE TABLE ac_bots(id INTEGER, bot_id TEXT, bot_name TEXT, env TEXT, active_engine TEXT, bot_type TEXT,
    owner_id TEXT, entity_id TEXT, entity_type TEXT, is_delete INTEGER, status TEXT, binding_id INTEGER);
    CREATE TABLE ac_entity_device_binding(id INTEGER, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT);
    INSERT INTO ac_bots VALUES(1,'bot','Personal Bot','dev','openclaw','personal','owner','owner','staff',0,'active',NULL);
    INSERT INTO ac_bots VALUES(2,'bot-2','Second Bot','dev','openclaw','personal','owner','owner','staff',0,'active',NULL);
    INSERT INTO ac_bots VALUES(3,'foreign','Foreign Bot','dev','openclaw','personal','other','other','staff',0,'active',NULL);`);
  seed.close();
  for (const bot of ["bot", "bot-2"]) {
    const home = join(root, "bots", "staff_owner", bot, "openclaw");
    const workspace = join(home, "workspace");
    mkdirSync(workspace, { recursive: true });
    writeFileSync(join(home, "openclaw.json"), JSON.stringify({ agents: { defaults: { workspace } } }));
  }
  for (const stage of ["diagnose", "plan"]) {
    mkdirSync(join(root, `clawevolve-${stage}/scripts`), { recursive: true });
    // Fixture only: actual Skill/model acceptance is explicitly separate.
    writeFileSync(join(root, `clawevolve-${stage}/scripts/run.sh`), "sleep 30\n");
  }
  mkdirSync(join(root, "scripts"), { recursive: true });
  writeFileSync(join(root, "scripts/clawevolve_runtime_cleanup.py"), "import time\ntime.sleep(30)\n");
  mkdirSync(join(root, "clawevolve-workflow/scripts/handlers"), { recursive: true });
  writeFileSync(join(root, "clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py"), "import time\ntime.sleep(30)\n");
  const probe = createServer().listen(0, "127.0.0.1"); await once(probe, "listening");
  const port = (probe.address() as { port: number }).port; await new Promise<void>((resolve) => probe.close(() => resolve()));
  const config: SingleboxConfig = { userId: "owner", model: "provider/model",
    backendDb, skillsRoot: root, botsRoot: join(root, "bots"), dataDirectory: join(root, "data"), port };
  app = await startSinglebox(config); if (!app.server.listening) await once(app.server, "listening");
  const base = `http://127.0.0.1:${port}`;
  const post = async (path: string, data: unknown) => fetch(base + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data) });
  expect((await fetch(base + "/health")).status).toBe(200);
  expect((await fetch(base + "/health", { headers: { origin: "https://other.example" } })).status).toBe(403);
  expect((await (await fetch(base + "/api/tclog/bots")).json()).bots.map((bot: { botId: string }) => bot.botId)).toEqual(["bot-2", "bot"]);
  for (const [botId, botEnv] of [["foreign", "dev"], ["bot", "prod"], ["missing", "dev"]]) {
    expect((await post("/api/evolve/tasks", { userId: "owner", botId, botEnv, taskType: "diagnose" })).status).toBe(422);
  }
  // New Backend Bots appear without restarting ClawWeb or editing its config.
  const added = new Database(backendDb);
  added.exec("INSERT INTO ac_bots VALUES(4,'new-bot','New Bot','dev','openclaw','personal','owner','owner','staff',0,'active',NULL)");
  added.close();
  expect((await (await fetch(base + "/api/tclog/bots")).json()).bots[0].botId).toBe("new-bot");
  expect((await post("/api/evolve/tasks", { userId: "owner", botId: "new-bot", botEnv: "dev", taskType: "diagnose" })).status).toBe(422);
  expect((await post("/api/evolve/pack-restores", {})).status).toBe(422);
  const domain = await post("/api/bench/domains", { domainId: "LOCAL-PLAN", name: "Plan upload fixture" });
  expect(domain.status, JSON.stringify(await domain.json())).toBe(201);
  const template = await post("/api/bench/domains/owner/LOCAL-PLAN/templates", {
    templateName: "local_case", contentMd: "---\nid: local_case\nname: Local Case\n---\n## Prompt\nReply hello.\n",
  });
  expect(template.status, JSON.stringify(await template.json())).toBe(201);
  expect((await post("/api/bench/domains/other/LOCAL-PLAN/templates", { templateName: "forbidden", contentMd: "x" })).status).toBe(403);
  const published = await post("/api/bench/domains/owner/LOCAL-PLAN/templates/local_case/publish", {});
  expect(published.status, JSON.stringify(await published.json())).toBe(200);
  expect((await post("/api/evolve/pack-restores", { userId: "owner", botId: "bot", botEnv: "dev" })).status).toBe(422);
  expect((await post("/api/evolve/pack-restores", { userId: "owner", botId: "foreign", botEnv: "dev", confirmRestore: true })).status).toBe(422);
  expect((await post("/api/evolve/internal/tasks/missing/steps/missing/artifacts/restore-download-url", { kind: "artifact" })).status).toBe(403);
  const benchInput = { taskName: "Local Bench", userId: "owner", botId: "bot", botEnv: "dev", benchDomainId: "LOCAL-PLAN" };
  expect((await post("/api/evolve/benches", { ...benchInput, botId: "foreign" })).status).toBe(422);
  const benchResponse = await post("/api/evolve/benches", { ...benchInput, model: "wrong/model", judge: "wrong/judge" });
  const benchTask = await benchResponse.json();
  expect(benchResponse.status, JSON.stringify(benchTask)).toBe(201);
  expect(benchTask.steps[0].botRunId).toMatch(/^local:/);
  expect(benchTask.config.bench.model).toBe("wrong/model");
  expect(benchTask.config.bench.judge.model).toBe("wrong/model");
  expect(benchTask.config.runtimeMaintenance).toBe(false);
  const directResponse = await post("/api/evolve/tasks", { taskType: "full", inputMode: "direct_goal",
    taskName: "Local direct goal", userId: "owner", botId: "bot", botEnv: "dev",
    model: "provider/custom-model", goal: "Create one local Skill", maxRounds: 1 });
  const directTask = await directResponse.json();
  expect(directResponse.status, JSON.stringify(directTask)).toBe(201);
  expect(directTask.config.model).toBe("provider/custom-model");
  expect(directTask.steps[0].command).toContain("--model provider/custom-model");
  const runInput = { ownerId: "owner", domainId: "LOCAL-PLAN", templateName: "__domain__", templateVersion: 0,
    model: config.model, runConfig: { evolveTaskId: benchTask.task_id, evolveStepId: benchTask.steps[0].stepId } };
  expect((await post("/api/bench/runs", { ...runInput, ownerId: "other" })).status).toBe(422);
  expect((await post("/api/bench/runs", { ...runInput, runConfig: { evolveTaskId: "missing", evolveStepId: "missing" } })).status).toBe(403);
  const runResponse = await post("/api/bench/runs", runInput);
  const run = await runResponse.json();
  expect(runResponse.status, JSON.stringify(run)).toBe(201);
  expect((await fetch(`${base}/api/bench/runs/${run.benchRunId}`)).status).toBe(200);
  expect((await fetch(`${base}/api/bench/admin/domains`)).status).toBe(403);
  const created = await post("/api/evolve/tasks", { taskType: "diagnose", taskName: "local test", userId: "owner", botId: "bot-2", botEnv: "dev", judgeBackend: "subagent", diagnoseIntent: "test", model: "unused" });
  const task = await created.json();
  expect(created.status, JSON.stringify(task)).toBeLessThan(300);
  const logUrl = `${base}/api/singlebox/tasks/${task.task_id}/logs`;
  const logPath = join(config.dataDirectory, "logs", task.task_id, `${task.steps[0].stepId}.log`);
  writeFileSync(logPath, "local diagnostic fixture\n");
  const logResponse = await fetch(logUrl);
  expect(logResponse.status).toBe(200);
  expect(logResponse.headers.get("content-disposition")).toContain("attachment");
  expect(await logResponse.text()).toContain("local diagnostic fixture");
  expect((await fetch(`${base}/api/singlebox/tasks/EV-missing/logs`)).status).toBe(404);
  const retryUrl = (stepId: string) => `/api/evolve/tasks/${task.task_id}/steps/${stepId}/retry`;
  // Running steps still use the original Router's 409 eligibility check.
  expect((await post(retryUrl(task.steps[0].stepId), {})).status).toBe(409);
  const failed = await post(`/api/evolve/internal/tasks/${task.task_id}/steps/${task.steps[0].stepId}/report`, {
    status: "failed", summary: "fixture judge failure", error: { code: "DIAGNOSE_JUDGE_EXECUTION_FAILED", message: "fixture", retryable: true },
  });
  expect(failed.status).toBe(200);
  expect((await post(retryUrl(task.steps[0].stepId), { apiKey: "not-allowed" })).status).toBe(422);
  const retried = await post(retryUrl(task.steps[0].stepId), {});
  const retryData = await retried.json();
  expect(retried.status, JSON.stringify(retryData)).toBe(201);
  expect(retryData.step.stepId).not.toBe(task.steps[0].stepId);
  expect(retryData.step.botRunId).toMatch(/^local:/);
  const report = await post(`/api/evolve/internal/tasks/${task.task_id}/steps/${retryData.step.stepId}/report`, {
    status: "succeeded", output: { diagnosis: { summary: "fixture" }, cases: { total: 1, goodCount: 0, badCount: 1,
      items: [{ caseId: "case-1", type: "bad", summary: "fixture case" }] } },
  });
  const result = await report.json();
  expect(report.status, JSON.stringify(result)).toBe(200);
  const details = await (await fetch(`${base}/api/evolve/tasks/${task.task_id}`)).json();
  expect(details.steps.map((step: { stepType: string }) => step.stepType)).toEqual(["diagnose", "diagnose", "plan"]);
  expect(details.steps[2].botRunId).toMatch(/^local:/);
  const planId = details.steps[2].stepId;
  expect((await post(`/api/evolve/internal/tasks/${task.task_id}/steps/${planId}/report`, {
    status: "failed", summary: "fixture plan failure", error: { code: "PLAN_FAILED", message: "fixture", retryable: true },
  })).status).toBe(200);
  const taskDb = new Database(join(config.dataDirectory, "clawevolve.db"));
  taskDb.prepare("UPDATE ce_tasks SET user_id=? WHERE task_id=?").run("other", task.task_id);
  expect((await fetch(logUrl)).status).toBe(403);
  expect((await post(retryUrl(planId), {})).status).toBe(403);
  taskDb.prepare("UPDATE ce_tasks SET user_id=? WHERE task_id=?").run("owner", task.task_id);
  taskDb.prepare("UPDATE ce_steps SET step_type=? WHERE step_id=?").run("optimize", planId);
  expect((await post(retryUrl(planId), {})).status).toBe(422);
  taskDb.prepare("UPDATE ce_steps SET step_type=? WHERE step_id=?").run("plan", planId);
  taskDb.close();
  const planRetry = await post(retryUrl(planId), {});
  const planRetryData = await planRetry.json();
  expect(planRetry.status, JSON.stringify(planRetryData)).toBe(201);
  expect(planRetryData.step.botRunId).toMatch(/^local:/);
  const cleanupInput = { taskName: "manual cleanup", userId: "owner", botId: "bot-2", botEnv: "dev", forceCleanup: true };
  expect((await post("/api/evolve/runtime-cleanups", { ...cleanupInput, forceCleanup: false })).status).toBe(422);
  expect((await post("/api/evolve/runtime-cleanups", { ...cleanupInput, botId: "foreign" })).status).toBe(422);
  expect((await post("/api/evolve/runtime-cleanups", { ...cleanupInput, runtimeMaintenance: true })).status).toBe(422);
  // The same Bot has a running Plan, but explicitly confirmed openversion cleanup skips its guard.
  const cleaned = await post("/api/evolve/runtime-cleanups", { ...cleanupInput, version: "internalversion" });
  const cleanupTask = await cleaned.json();
  expect(cleaned.status, JSON.stringify(cleanupTask)).toBe(201);
  expect(cleanupTask.config).toMatchObject({ version: "openversion", gatewayRestart: false, activeTaskCheck: false, runtimeMaintenance: false });
  expect(cleanupTask.steps[0].botRunId).toMatch(/^local:/);
  const cleanupStep = cleanupTask.steps[0].stepId;
  expect((await post(`/api/evolve/internal/tasks/${cleanupTask.task_id}/steps/${cleanupStep}/report`, {
    status: "failed", summary: "fixture", error: { code: "RUNTIME_CLEANUP_FAILED", message: "fixture", retryable: true },
  })).status).toBe(200);
  const cleanupRetryPath = `/api/evolve/tasks/${cleanupTask.task_id}/steps/${cleanupStep}/retry`;
  expect((await post(cleanupRetryPath, {})).status).toBe(422);
  expect((await post(cleanupRetryPath, { forceCleanup: true, input: {} })).status).toBe(422);
  const cleanupRetried = await post(cleanupRetryPath, { forceCleanup: true });
  expect(cleanupRetried.status, JSON.stringify(await cleanupRetried.json())).toBe(201);
  const backend = new Database(backendDb, { readonly: true });
  expect(backend.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'ce_%'").all()).toEqual([]);
  backend.close();
}, 15000);
