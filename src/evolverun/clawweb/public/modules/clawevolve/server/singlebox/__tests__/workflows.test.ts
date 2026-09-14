import { afterEach, expect, it } from "vitest";
import Database from "better-sqlite3";
import { once } from "node:events";
import { createServer } from "node:net";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { startSinglebox } from "../../singlebox.js";
import type { SingleboxConfig } from "../config.js";

let root = "";
let app: Awaited<ReturnType<typeof startSinglebox>> | undefined;
afterEach(async () => { await app?.shutdown(); app = undefined; if (root) rmSync(root, { recursive: true, force: true }); });

it.each(["diagnose_goal", "direct_goal", "optimize", "bench_optimize"])("uses the original Task/Step protocol for %s (fixture execution, not model acceptance)", async (mode) => {
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

  for (const stage of ["optimize", "bench_plan"]) writeFileSync(join(root, `clawevolve-workflow/scripts/handlers/clawevolve_${stage}_run.py`), "import time\ntime.sleep(30)\n");
  const identity = { taskName: "local workflow", userId: "owner", botId: "bot", botEnv: "dev", maxRounds: 2 };
  const send = async (path: string, body: unknown, status = 201) => {
    const response = await post(path, body); const result = await response.json();
    expect(response.status, JSON.stringify(result)).toBe(status); return result;
  };
  const reportStep = (taskId: string, stepId: string, output: unknown) => send(`/api/evolve/internal/tasks/${taskId}/steps/${stepId}/report`, { status: "succeeded", output }, 200);
  for (const blocked of [{ taskType: "repair" }, { taskType: "session_analysis" }, { taskType: "full", source: "improvement" }, { taskType: "full", input: { type: "insight_improvement" } }]) {
    await send("/api/evolve/tasks", { ...identity, ...blocked }, 422);
  }
  for (const domainId of ["TRAIN", "TEST"]) {
    await send("/api/bench/domains", { domainId, name: domainId });
    await send(`/api/bench/domains/owner/${domainId}/templates`, { templateName: "case", contentMd: "---\nid: case\nname: Case\n---\n## Prompt\nReply hello.\n" });
    await send(`/api/bench/domains/owner/${domainId}/templates/case/publish`, {}, 200);
  }
  let task;
  if (mode === "optimize") {
    const raw = new Database(join(config.dataDirectory, "clawevolve.db"));
    const repo = new EvolveRepository(new SqliteDatabase(raw));
    await repo.createTask({ taskId: "EV-source", taskType: "diagnose", userId: "owner", botId: "bot", taskName: "source", remark: null, configJson: JSON.stringify({ botEnv: "dev" }), createdBy: "owner" });
    for (const stage of ["diagnose", "plan"]) {
      await repo.createStep({ taskId: "EV-source", stepId: `STEP-source-${stage}`, stepType: stage, stepNo: stage === "plan" ? 2 : 1, command: "/fixture" });
      await repo.updateStepStatus(`STEP-source-${stage}`, { status: "succeeded", output: { benchDomains: { trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" } } });
    }
    raw.close();
    await send("/api/evolve/optimizations", { ...identity, sourceDiagnosisTaskIds: [] }, 400);
    task = await send("/api/evolve/optimizations", { ...identity, sourceDiagnosisTaskIds: ["EV-source"] });
  } else if (mode === "bench_optimize") {
    task = await send("/api/evolve/bench-optimizations", { ...identity, objective: "Improve", trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" });
  } else {
    task = await send("/api/evolve/tasks", { ...identity, taskType: "full", inputMode: mode, goal: "Improve", diagnoseIntent: "Inspect", judgeBackend: "subagent" });
  }
  expect(task.config.runtimeMaintenance).toBe(false);
  expect(task.steps[0].botRunId).toMatch(/^local:/);
  const taskId = task.task_id;
  let step = task.steps[0];
  if (step.stepType === "diagnose") {
    const result = await reportStep(taskId, step.stepId, { diagnosis: { summary: "fixture" }, cases: { total: 1, badCount: 1, goodCount: 0, items: [{ caseId: "case", type: "bad", summary: "fixture" }] } });
    step = result.nextStep;
  }
  const spec = { version: "v0", path: "spec-v0.md", content_type: "text", content: "Fixture spec" };
  const baseline = Object.fromEntries(["train", "test"].map(role => [role, { role, source: "generated", producerStepId: step.stepId, ownerUserId: "owner", domainId: role.toUpperCase(), benchRunId: `fixture-${role}`, metrics: {} }]));
  for (const role of ["train", "test"]) {
    const run = await send("/api/bench/runs", { ownerId: "owner", domainId: role.toUpperCase(), templateName: "__domain__", templateVersion: 0,
      model: config.model, runConfig: { evolveTaskId: taskId, evolveStepId: step.stepId, role: `baseline_${role}` } });
    const updated = await fetch(`${base}/api/bench/runs/${run.benchRunId}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ status: "succeeded", score: 1, maxScore: 1 }) });
    expect(updated.status).toBe(200);
    baseline[role].benchRunId = run.benchRunId;
  }
  if (step.stepType === "plan") {
    const result = await reportStep(taskId, step.stepId, { goal: { text: "Improve" }, spec, benchCases: { items: [] }, benchDomains: { trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" } });
    step = result.nextStep;
  } else if (step.stepType === "bench_plan") {
    const result = await reportStep(taskId, step.stepId, { baseline, objective: { text: "Improve", path: "objective.md" }, spec });
    step = result.nextStep;
  }
  expect(step.stepType).toBe("optimize");
  const details = await (await fetch(`${base}/api/evolve/tasks/${taskId}`)).json();
  expect(details.steps.at(-1).botRunId).toMatch(/^local:/);
  const ticketBody = { kind: "baseline-pack", size: 1, sha256: "a".repeat(64), contentType: "application/zip" };
  const ticket = await send(`/api/evolve/internal/tasks/${taskId}/steps/${step.stepId}/artifacts/upload-url`, ticketBody, 200);
  expect(ticket.url).toMatch(/^http:\/\/127.0.0.1:/);
  expect(ticket.artifact.ref).toContain("oss://clawevolve-artifacts/");
  await send(`/api/evolve/internal/tasks/${taskId}/steps/${step.stepId}/artifacts/accepted-download-url`, { sourceRound: 1 }, 422);
  const first = await reportStep(taskId, step.stepId, { diff: {}, metrics: [], baseline, roundDecision: { stop: false, reason: "fixture continue" } });
  expect(first.nextStep.roundNo).toBe(2);
  await send(`/api/evolve/internal/tasks/${taskId}/steps/${first.nextStep.stepId}/artifacts/upload-url`, ticketBody, 422);
  await reportStep(taskId, first.nextStep.stepId, { diff: {}, metrics: [], baseline, roundDecision: { stop: true, reason: "fixture stop" } });
  const finished = await (await fetch(`${base}/api/evolve/tasks/${taskId}`)).json();
  expect(finished.status).toBe("completed");
  expect(finished.steps.filter((item: { stepType: string }) => item.stepType === "optimize")).toHaveLength(2);

}, 20000);
