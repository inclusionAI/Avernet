import Database from "better-sqlite3";
import express from "express";
import { existsSync, mkdirSync, realpathSync } from "node:fs";
import { join, resolve, relative, isAbsolute } from "node:path";
import { open } from "node:fs/promises";
import { pipeline } from "node:stream/promises";
import { pathToFileURL } from "node:url";
import { loadSingleboxConfig, type SingleboxConfig } from "./singlebox/config.js";
import { createLocalBotResolver } from "./singlebox/bot-runtime.js";
import { createLocalExecution } from "./singlebox/local-execution.js";
import { EvolveRepository } from "./repositories/evolve-repository.js";
import { createClawevolveModule } from "./create-module.js";
import { configureClawWebRuntimeConfig, runMigrations, SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { FilesystemObjectStore } from "./services/object-storage/filesystem-object-store.js";

function createLocalDatabase(dataDirectory: string): SqliteDatabase {
  const dataRoot = resolve(dataDirectory);
  mkdirSync(dataRoot, { recursive: true, mode: 0o700 });
  const raw = new Database(join(dataRoot, "clawevolve.db"));
  raw.pragma("journal_mode = WAL");
  raw.pragma("foreign_keys = ON");
  raw.pragma("busy_timeout = 5000");
  return new SqliteDatabase(raw);
}

function prepareLocalOpenClawCatalog(config: SingleboxConfig): void {
  if (config.botSource !== "openclaw") return;
  mkdirSync(resolve(config.dataDirectory), { recursive: true, mode: 0o700 });
  const raw = new Database(config.backendDb);
  try {
    raw.exec(`
      CREATE TABLE IF NOT EXISTS ac_bots (
        id INTEGER PRIMARY KEY, bot_id TEXT, bot_name TEXT, env TEXT,
        active_engine TEXT, bot_type TEXT, owner_id TEXT, entity_id TEXT,
        entity_type TEXT, is_delete INTEGER DEFAULT 0, status TEXT,
        binding_id INTEGER
      );
      CREATE TABLE IF NOT EXISTS ac_bot_collaborator (
        id INTEGER PRIMARY KEY, user_id TEXT, bot_id TEXT, owner_id TEXT
      );
      CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
        id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT,
        device_props TEXT, status TEXT, env TEXT
      );
      CREATE TABLE IF NOT EXISTS ac_bot_publish (
        id INTEGER PRIMARY KEY, source_bot_pk INTEGER, env TEXT, status TEXT
      );
    `);
    raw.prepare("DELETE FROM ac_bots").run();
    raw.prepare(`INSERT INTO ac_bots
      (id, bot_id, bot_name, env, active_engine, bot_type, owner_id, entity_id, entity_type, is_delete, status, binding_id)
      VALUES (1, ?, ?, 'dev', 'openclaw', 'personal', ?, ?, 'staff', 0, 'active', NULL)`)
      .run(config.localBotId ?? "local-openclaw", config.localBotName ?? "Local OpenClaw", config.userId, config.userId);
  } finally {
    raw.close();
  }
}

export async function startSinglebox(config: SingleboxConfig) {
  const { port, dataDirectory } = config;
  const publicBaseUrl = `http://127.0.0.1:${port}`;
  const taskDbPath = resolve(dataDirectory, "clawevolve.db");
  prepareLocalOpenClawCatalog(config);
  if (existsSync(taskDbPath) && realpathSync(taskDbPath) === realpathSync(config.backendDb)) {
    throw new Error("ClawEvolve task DB must not be the Backend database");
  }
  configureClawWebRuntimeConfig({});
  const db = createLocalDatabase(dataDirectory);
  await runMigrations(db, "sqlite");
  // COSEC: separate readonly connection; never migrate/write Backend's database.
  const botDb = new SqliteDatabase(new Database(config.backendDb, { readonly: true, fileMustExist: true }));
  const repo = new EvolveRepository(db, botDb);
  const resolveBot = createLocalBotResolver(config, botDb);
  const maxArtifactBytes = config.maxArtifactBytes ?? 512 * 1024 * 1024;
  const artifactStore = new FilesystemObjectStore(join(dataDirectory, "artifacts"), publicBaseUrl, maxArtifactBytes);
  const execution = createLocalExecution(config, async (input, message) => {
    const step = await repo.findStep(input.stepId);
    if (step && !["succeeded", "failed", "canceled"].includes(step.status)) {
      await repo.updateStepStatus(input.stepId, { status: "failed", errorCode: "LOCAL_EXECUTION_FAILED", errorMessage: message, retryable: true });
    }
  }, resolveBot);
  const module = createClawevolveModule({
    version: "openversion", artifactBucket: "clawevolve-artifacts", db, botDb, artifactStore, publicBaseUrl, dispatch: execution.dispatch, cancelExecution: execution.cancel,
  });
  await module.start();

  const app = express();
  app.use((request, response, next) => {
    const hosts = [`127.0.0.1:${port}`, `localhost:${port}`];
    const origin = request.get("origin");
    // COSEC: loopback-only execution must also reject cross-site requests and DNS rebinding.
    if (!hosts.includes(request.get("host") ?? "")
      || (origin && !hosts.some((host) => origin === `http://${host}`))
      || request.get("sec-fetch-site") === "cross-site") {
      response.status(403).json({ error: "Singlebox accepts same-origin local requests only" }); return;
    }
    request.headers["x-user-id"] = config.userId;
    request.isAdmin = false;
    next();
  });
  app.get("/api/singlebox/tasks/:taskId/logs", async (request, response) => {
    const taskId = String(request.params.taskId);
    // COSEC: bind reads to an owned persisted task and server-generated Step paths.
    if (!/^[A-Za-z0-9_-]+$/.test(taskId)) { response.status(400).json({ error: "Invalid task ID" }); return; }
    const task = await repo.findTask(taskId);
    if (!task) { response.status(404).json({ error: "Task not found" }); return; }
    if (task.user_id !== config.userId) { response.status(403).json({ error: "Task is not owned" }); return; }
    const steps = await repo.listSteps(taskId);
    const chunks: string[] = [];
    // Bounded diagnostic download: latest 40 Steps, last 256 KiB per Step.
    for (const step of steps.slice(-40)) {
      if (!/^[A-Za-z0-9_-]+$/.test(step.step_id)) continue;
      const file = join(dataDirectory, "logs", taskId, `${step.step_id}.log`);
      try {
        // COSEC: reject symlink redirection outside the configured data directory.
        const resolved = realpathSync(file);
        const rel = relative(realpathSync(dataDirectory), resolved);
        if (rel.startsWith("..") || isAbsolute(rel)) throw new Error("Log path escapes data directory");
        const handle = await open(resolved, "r");
        try {
          const stat = await handle.stat();
          if (!stat.isFile()) throw new Error("Log is not a regular file");
          const length = Math.min(stat.size, 256 * 1024);
          const buffer = Buffer.alloc(length);
          const { bytesRead } = await handle.read(buffer, 0, length, stat.size - length);
          chunks.push(`=== ${step.step_id} (${step.step_type})${stat.size > length ? " [last 256 KiB]" : ""} ===\n${buffer.subarray(0, bytesRead).toString("utf8")}`);
        } finally { await handle.close(); }
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
        response.status(500).json({ error: "Unable to read local task logs" }); return;
      }
    }
    if (!chunks.length) { response.status(404).json({ error: "暂无本地执行日志；仅支持本机运行的任务" }); return; }
    response.attachment(`${taskId}-local.log`).type("text/plain").send(
      "Local Step process output (latest 40 Steps; up to 256 KiB per Step). Not a complete Bot session archive.\n\n" + chunks.join("\n\n"));
  });
  app.put(
    "/api/singlebox/artifacts/:token",
    async (request, response) => {
      try {
        const key = artifactStore.resolveSignedRequest(String(request.params.token), "PUT");
        if (Number(request.header("content-length") ?? 0) > maxArtifactBytes) {
          response.status(413).json({ error: `Artifact exceeds maxArtifactBytes=${maxArtifactBytes}` }); return;
        }
        const result = await artifactStore.putStream(key, request);
        response.set("ETag", result.etag).status(204).end();
      } catch (error) {
        if (!response.headersSent && !response.destroyed) response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
      }
    },
  );
  app.get("/api/singlebox/artifacts/:token", async (request, response) => {
    try {
      const key = artifactStore.resolveSignedRequest(String(request.params.token), "GET");
      const object = await artifactStore.openStream(key);
      response.set("Content-Length", String(object.size));
      response.type("application/octet-stream");
      await pipeline(object.stream, response);
    } catch (error) {
      if (!response.headersSent && !response.destroyed) response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });
  app.use(express.json({ limit: "10mb" }));
  app.use("/api", async (request, response, next) => {
    if (request.path.startsWith("/bench/admin")) {
      response.status(403).json({ error: "Openversion has no Bench administrator scope" }); return;
    }
    const benchRun = /^\/bench\/runs\/([^/]+)(?:\/|$)/.exec(request.path);
    if (benchRun) {
      const run = await module.repositories.benchRun.findByBenchRunId(decodeURIComponent(benchRun[1]));
      if (!run) { response.status(404).json({ error: "Bench Run not found" }); return; }
      // COSEC: even result writes and reads must remain in this local user's namespace.
      if (run.owner_user_id !== config.userId) { response.status(403).json({ error: "Bench Run is not owned" }); return; }
    }
    if (["GET", "HEAD"].includes(request.method)) { next(); return; }
    if (request.method === "POST" && request.path === "/evolve/runtime-cleanups") {
      const body = request.body ?? {};
      // COSEC: openversion manual cleanup never accepts arbitrary paths, commands, or owners.
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string"
        || body.forceCleanup !== true || body.runtimeMaintenance === true
        || body.nodeCommandYamls || body.input) {
        response.status(422).json({ error: "Openversion cleanup requires an owned personal Bot and explicit confirmation" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch (error) {
        response.status(422).json({ error: error instanceof Error ? error.message : "Local Bot runtime unavailable" }); return;
      }
      request.body = { taskName: body.taskName, remark: body.remark, userId: config.userId,
        botId: body.botId, botEnv: body.botEnv, forceCleanup: true };
      next(); return;
    }
    if (request.method === "POST" && request.path === "/evolve/pack-restores") {
      const body = request.body ?? {};
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string" || body.confirmRestore !== true) {
        response.status(422).json({ error: "恢复会覆盖所选 Bot 的工作区物料，请明确确认" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch { response.status(422).json({ error: "Local Bot unavailable" }); return; }
      // COSEC: source binding and active-restore exclusion remain in the original Router.
      request.body = { taskName: body.taskName, remark: body.remark, userId: config.userId, botId: body.botId,
        botEnv: body.botEnv, packId: body.packId, sourceTaskId: body.sourceTaskId, sourceKind: body.sourceKind, sourceRound: body.sourceRound };
      next(); return;
    }
    const restoreDownload = request.method === "POST" && /^\/evolve\/internal\/tasks\/([^/]+)\/steps\/([^/]+)\/artifacts\/restore-download-url$/.exec(request.path);
    if (restoreDownload) {
      const task = await repo.findTask(restoreDownload[1]);
      const step = await repo.findStep(restoreDownload[2]);
      if (!task || task.user_id !== config.userId || !step || step.task_id !== task.task_id || step.step_type !== "restore") {
        response.status(403).json({ error: "Restore Step is not owned" }); return;
      }
      next(); return;
    }
    if (request.method === "POST" && request.path === "/evolve/packs") {
      const body = request.body ?? {};
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string") {
        response.status(422).json({ error: "Pack requires an owned personal Bot" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch { response.status(422).json({ error: "Local Bot unavailable" }); return; }
      // COSEC: accept only task metadata; paths and execution are selected by the local adapter.
      request.body = { taskName: body.taskName, remark: body.remark, userId: config.userId, botId: body.botId, botEnv: body.botEnv };
      next(); return;
    }
    const upload = request.method === "POST" && /^\/evolve\/internal\/tasks\/([^/]+)\/steps\/([^/]+)\/artifacts\/upload-url$/.exec(request.path);
    if (upload) {
      const task = await repo.findTask(upload[1]);
      const step = await repo.findStep(upload[2]);
      // COSEC: only this user's Pack Step may request snapshot upload tickets.
      if (!task || task.user_id !== config.userId || !step || step.task_id !== task.task_id || !["pack", "optimize"].includes(step.step_type)) {
        response.status(403).json({ error: "Artifact Step is not owned" }); return;
      }
      next(); return;
    }
    if (request.method === "POST" && ["/evolve/optimizations", "/evolve/bench-optimizations"].includes(request.path)) {
      const body = request.body ?? {};
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string"
        || body.input || body.nodeCommandYamls || body.apiKey || body.improvementId || body.improvementRequestId
        || body.source === "improvement") {
        response.status(422).json({ error: "Openversion optimization requires an owned personal Bot and original local workflow" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch { response.status(422).json({ error: "Local Bot unavailable" }); return; }
      // Retain original source/Domain validation and round limits in the existing Router.
      request.body = { taskName: body.taskName, remark: body.remark, userId: config.userId, botId: body.botId,
        botEnv: body.botEnv, sourceDiagnosisTaskIds: body.sourceDiagnosisTaskIds, objective: body.objective,
        trainBenchDomainId: body.trainBenchDomainId, testBenchDomainId: body.testBenchDomainId,
        model: body.model ?? config.model, maxRounds: body.maxRounds, runtimeMaintenance: false, openclawExecutionMode: "local" };
      next(); return;
    }
    const acceptedDownload = request.method === "POST" && /^\/evolve\/internal\/tasks\/([^/]+)\/steps\/([^/]+)\/artifacts\/accepted-download-url$/.exec(request.path);
    if (acceptedDownload) {
      const task = await repo.findTask(acceptedDownload[1]);
      const step = await repo.findStep(acceptedDownload[2]);
      // COSEC: original Router additionally checks round ordering and accepted Pack metadata.
      if (!task || task.user_id !== config.userId || !step || step.task_id !== task.task_id || step.step_type !== "optimize") {
        response.status(403).json({ error: "Optimize Step is not owned" }); return;
      }
      next(); return;
    }
    if (request.method === "POST" && request.path === "/evolve/benches") {
      const body = request.body ?? {};
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string"
        || body.input || body.nodeCommandYamls || body.apiKey || body.judgeBackend === "api") {
        response.status(422).json({ error: "Openversion Bench requires an owned personal Bot and fixed local execution" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch (error) { response.status(422).json({ error: error instanceof Error ? error.message : "Local Bot unavailable" }); return; }
      request.body = { taskName: body.taskName, remark: body.remark, userId: config.userId,
        botId: body.botId, botEnv: body.botEnv, benchDomainId: body.benchDomainId,
        templateName: body.templateName, templateVersion: body.templateVersion,
        model: body.model ?? config.model, judge: body.model ?? config.model, suite: "all", scene: "claw-evolve-bench",
        runtimeMaintenance: false, openclawExecutionMode: "local" };
      next(); return;
    }
    if (request.method === "POST" && request.path === "/bench/runs") {
      const body = request.body ?? {};
      const runConfig = body.runConfig;
      if (body.ownerId !== config.userId || !runConfig || typeof runConfig !== "object") {
        response.status(422).json({ error: "Bench Run requires the local owner and frozen Evolve identity" }); return;
      }
      const task = await repo.findTask(String(runConfig.evolveTaskId ?? ""));
      const step = await repo.findStep(String(runConfig.evolveStepId ?? ""));
      if (!task || task.user_id !== config.userId || !step || step.task_id !== task.task_id) {
        response.status(403).json({ error: "Bench Run Evolve identity is not owned" }); return;
      }
      next(); return;
    }
    if (benchRun && ((request.method === "PUT" && /^\/bench\/runs\/[^/]+$/.test(request.path))
      || (request.method === "POST" && /^\/bench\/runs\/[^/]+\/(results|artifacts)$/.test(request.path)))) {
      next(); return;
    }
    const domainWrite = /^\/bench\/domains\/([^/]+)\/([^/]+)(?:\/(.*))?$/.exec(request.path);
    if (domainWrite && ["POST", "PUT", "DELETE"].includes(request.method)) {
      if (decodeURIComponent(domainWrite[1]) !== config.userId) {
        response.status(403).json({ error: "Bench Domain is not owned" }); return;
      }
      const action = domainWrite[3] ?? "";
      if (!action || /^(?:uploads\/scan|templates(?:\/[^/]+(?:\/publish)?)?)$/.test(action)) {
        next(); return;
      }
    }
    const creation = request.method === "POST" && ["/evolve/tasks", "/evolve/diagnoses"].includes(request.path);
    if (creation) {
      const body = request.body ?? {};
      if (body.userId !== config.userId || typeof body.botId !== "string" || typeof body.botEnv !== "string"
        || (body.taskType && !["diagnose", "full"].includes(body.taskType)) || body.judgeBackend === "api" || body.apiKey
        || body.input || body.nodeCommandYamls || body.sessionSource === "service_export" || body.improvementId || body.improvementRequestId || body.source === "improvement") {
        response.status(422).json({ error: "Openversion requires an owned personal Bot and configured local model; custom commands and governance sources are unavailable" }); return;
      }
      try { await resolveBot(config.userId, body.botId, body.botEnv); }
      catch (error) {
        response.status(422).json({ error: error instanceof Error ? error.message : "Local Bot runtime unavailable" }); return;
      }
      request.body = { ...body, model: body.model ?? config.model, judgeBackend: "subagent", runtimeMaintenance: false, openclawExecutionMode: "local" };
      next(); return;
    }
    const retry = request.method === "POST"
      ? /^\/evolve\/tasks\/([^/]+)\/steps\/([^/]+)\/retry$/.exec(request.path) : null;
    if (retry) {
      const task = await repo.findTask(retry[1]);
      const step = await repo.findStep(retry[2]);
      if (!task || !step || step.task_id !== task.task_id) {
        response.status(404).json({ error: "Task or Step not found" }); return;
      }
      if (task.user_id !== config.userId) {
        response.status(403).json({ error: "Task is not owned by the current user" }); return;
      }
      const restoreRetry = task.task_type === "pack_restore" && step.step_type === "restore";
      const cleanupRetry = task.task_type === "runtime_cleanup" && step.step_type === "runtime_cleanup";
      // COSEC: each openversion cleanup retry requires confirmation, never parameter overrides.
      if (restoreRetry ? request.body?.confirmRestore !== true || Object.keys(request.body).some((key) => key !== "confirmRestore")
        : cleanupRetry
        ? request.body?.forceCleanup !== true || Object.keys(request.body).some((key) => key !== "forceCleanup")
        : !((({ diagnose: ["diagnose", "plan"], full: ["diagnose", "plan", "optimize"], optimize: ["optimize"], bench_optimize: ["bench_plan", "optimize"] } as Record<string, string[]>)[task.task_type]?.includes(step.step_type)) || (task.task_type === "bench" && step.step_type === "bench") || (task.task_type === "pack" && step.step_type === "pack")) || Object.keys(request.body ?? {}).length > 0) {
        response.status(422).json({ error: "Local retry requires an allowed stage and its confirmation; parameter overrides are unavailable" }); return;
      }
      try {
        const taskConfig = JSON.parse(task.config_json || "{}");
        await resolveBot(config.userId, task.bot_id, String(taskConfig.botEnv ?? ""));
      } catch (error) {
        response.status(422).json({ error: error instanceof Error ? error.message : "Local Bot runtime unavailable" }); return;
      }
      // Keep original retry eligibility, Step creation and command generation in the existing Router.
      next(); return;
    }
    const report = request.method === "POST" && /^\/evolve\/internal\/tasks\/[^/]+\/steps\/[^/]+\/report$/.test(request.path);
    const cancel = request.method === "POST" && /^\/evolve\/tasks\/[^/]+\/steps\/[^/]+\/cancel$/.test(request.path);
    const templates = request.method === "POST" && (request.path === "/bench/domains"
      || /^\/bench\/domains\/[^/]+\/[^/]+\/(uploads\/scan|templates\/batch-publish)$/.test(request.path));
    if (!report && !cancel && !templates) {
      response.status(422).json({ error: "This operation is not yet supported by openversion" }); return;
    }
    next();
  });
  app.get("/api/auth/me", (_request, response) => {
    response.json({
      userId: config.userId,
      nickName: "Singlebox User",
      userName: config.userId,
      displayName: "Singlebox User",
      avatarUrl: "",
      isAdmin: false,
      isClawEvolveAdmin: false,
    });
  });
  app.get("/health", (_request, response) => {
    response.json({ status: "ok", db: db.dbType, mode: "openversion", botSource: config.botSource ?? "singlebox" });
  });
  app.get("/api/tclog/bots", async (_request, response) => {
    const bots = (await repo.listEvolveBots(config.userId)).filter((bot) => bot.activeEngine?.toLowerCase() === "openclaw" && bot.botType?.toLowerCase() === "personal");
    response.json({ ownerId: config.userId, bots: bots.map((bot) => ({ ...bot,
      displayBotId: bot.botId, ownerId: config.userId, accessType: "owner", status: "active",
      source: config.botSource === "openclaw" ? "openclaw" : "backend",
    })) });
  });
  app.get("/api/singlebox/info", (_request, response) => {
    response.json({ model: config.model });
  });
  app.use("/api/evolve", module.publicRouter);
  app.use("/api/bench", module.benchRouter);

  const staticDirectory = join(import.meta.dirname, "..", "singlebox");
  if (existsSync(staticDirectory)) {
    app.use(express.static(staticDirectory));
    app.get("{*path}", (request, response) => {
      if (request.path.startsWith("/api/")) {
        response.status(404).json({ error: "Not Found" });
        return;
      }
      response.sendFile(join(staticDirectory, "index.html"));
    });
  }

  const server = app.listen(port, "127.0.0.1", () => {
    console.log(`[clawevolve] Singlebox listening on http://127.0.0.1:${port}/evolve`);
  });
  const shutdown = async () => {
    await execution.stop();
    server.close();
    await module.stop();
    await botDb.close();
    await db.close();
  };
  return { server, shutdown };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const main = async () => {
    if (!process.env.CLAWEVOLVE_SINGLEBOX_CONFIG) throw new Error("CLAWEVOLVE_SINGLEBOX_CONFIG is required; use start-clawevolve-singlebox.sh --config FILE");
    const { shutdown } = await startSinglebox(loadSingleboxConfig(process.env.CLAWEVOLVE_SINGLEBOX_CONFIG));
    process.once("SIGINT", () => void shutdown());
    process.once("SIGTERM", () => void shutdown());
  };
  main().catch((error) => {
    console.error("[clawevolve] Singlebox failed to start:", error);
    process.exitCode = 1;
  });
}
