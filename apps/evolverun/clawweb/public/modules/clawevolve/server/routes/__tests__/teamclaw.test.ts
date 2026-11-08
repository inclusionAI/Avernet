import {
  SqliteDatabase,
  runMigrations,
} from "@avernet/clawweb-shared/server/db";
import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BenchDomainRepository } from "../../repositories/bench-domain-repository.js";
import { BenchRunRepository } from "../../repositories/bench-run-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { BenchTemplateVersionRepository } from "../../repositories/bench-template-version-repository.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import type { SpaceBot } from "../../services/evolve/teamclaw-scope.js";
import { createBenchRouter } from "../bench.js";
import { createEvolveRouter } from "../evolve.js";
import { createTeamclawRouter } from "../teamclaw.js";
let db: SqliteDatabase,
  tasks: EvolveRepository,
  domains: BenchDomainRepository,
  runs: BenchRunRepository;
let server: ReturnType<express.Application["listen"]>,
  url: string,
  bots: SpaceBot[];
const spaces = vi.fn();
let templates: BenchTemplateRepository;
beforeEach(async () => {
  vi.stubEnv("SERVER_ENV", "dev");
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  tasks = new EvolveRepository(db);
  domains = new BenchDomainRepository(db);
  runs = new BenchRunRepository(db);
  bots = [
    {
      ownerId: "author",
      botId: "bot",
      env: "pre",
      activeEngine: "openclaw",
      botName: "Bot",
      unambiguousEnvironment: false,
    },
  ];
  spaces.mockReset().mockResolvedValue([
    { id: "42", name: "Team", type: "TEAM", role: "MEMBER" },
    { id: "7", name: "Personal", type: "PERSONAL", role: "ADMIN" },
  ]);
  for (const [id, owner, env, type, config] of [
    ["task", "author", "pre", "full", {}],
    ["prod", "author", "prod", "full", {}],
    ["other", "other", "pre", "full", {}],
    ["analysis", "author", "pre", "session_analysis", {}],
    ["skill", "author", "pre", "full", { targetSkillAssetId: "a" }],
  ] as const) {
    await tasks.createTask({
      taskId: id,
      taskName: id,
      taskType: type,
      userId: owner,
      botId: "bot",
      configJson: JSON.stringify({ botEnv: env, ...config }),
      createdBy: owner,
    });
  }
  await tasks.createStep({
    taskId: "task",
    stepId: "step",
    stepType: "optimize",
    stepNo: 1,
    roundNo: 1,
    command: "optimize",
  });
  await db.exec(
    "UPDATE ce_steps SET status='succeeded',output_json=? WHERE step_id='step'",
    [JSON.stringify({ accepted: false, benchDecision: "not_improved" })]
  );
  for (const [owner, id, space] of [
    ["author", "same", "42"],
    ["other", "same", "42"],
    ["author", "private", undefined],
    ["viewer", "historic", undefined],
    ["viewer", "elsewhere", "99"],
  ] as const)
    await domains.create({
      domainId: id,
      name: id,
      ownerUserId: owner,
      spaceId: space,
    });
  await runs.create({
    benchRunId: "run",
    ownerUserId: "author",
    domainId: "same",
    templateName: "case",
    templateVersion: 1,
  });
  await runs.create({
    benchRunId: "foreign",
    ownerUserId: "viewer",
    domainId: "elsewhere",
    templateName: "case",
    templateVersion: 1,
  });
  templates = new BenchTemplateRepository(db);
  const versions = new BenchTemplateVersionRepository(db);
  const evolve = createEvolveRouter(tasks, {
    benchDomainRepo: domains,
    benchTemplateRepo: templates,
    benchRunRepo: runs,
    dispatch: vi.fn(),
    modelConfig: { defaultModel: "test/model", models: ["test/model"] },
  });
  const bench = createBenchRouter(domains, templates, versions, runs, null, db);
  const app = express();
  app.use(express.json());
  const common = {
    spaces: { listAccessibleSpaces: spaces },
    bots: async () => bots,
    tasks,
    domains,
    runs,
  };
  app.use(
    "/evolve/teamclaw",
    createTeamclawRouter({ ...common, kind: "evolve", legacy: evolve })
  );
  app.use(
    "/bench/teamclaw",
    createTeamclawRouter({ ...common, kind: "bench", legacy: bench })
  );
  app.use("/evolve", evolve);
  app.use("/bench", bench);
  await new Promise<void>((resolve) => {
    server = app.listen(0, "127.0.0.1", resolve);
  });
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(async () => {
  if (server)
    await new Promise<void>((resolve) => server.close(() => resolve()));
  await db.close();
  vi.unstubAllEnvs();
});
const request = (
  path: string,
  method = "GET",
  body?: unknown,
  user = "viewer"
) =>
  fetch(url + path, {
    method,
    headers: { "X-User-Id": user, "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
describe("explicit TeamClaw space adapters", () => {
  it.each(["/evolve/teamclaw/tasks", "/bench/teamclaw/domains"])(
    "requires space and live membership %s",
    async (path) => {
      expect((await request(path)).status).toBe(400);
      expect((await request(path + "?space_id=99")).status).toBe(403);
      expect(
        (await request(path + "?space_id=42", "GET", undefined, "")).status
      ).toBe(401);
      spaces.mockRejectedValueOnce(new Error("private upstream failure"));
      const failed = await request(path + "?space_id=42");
      expect(failed.status).toBe(503);
      expect(await failed.text()).not.toContain("private upstream");
    }
  );
  it("filters before task count/paging and follows Bot moves without touching tasks", async () => {
    let page = await (
      await request("/evolve/teamclaw/tasks?space_id=42&pageSize=1")
    ).json();
    expect(page.total).toBe(1);
    expect(page.tasks[0].task_id).toBe("task");
    bots = [];
    page = await (await request("/evolve/teamclaw/tasks?space_id=42")).json();
    expect(page.total).toBe(0);
    expect(
      (await request("/evolve/teamclaw/tasks/task?space_id=42")).status
    ).toBe(404);
    expect((await tasks.findTask("task"))?.config_json).toBe(
      '{"botEnv":"pre"}'
    );
  });
  it("uses current-space authorization, not author/creator/admin", async () => {
    expect(
      (await request("/evolve/teamclaw/tasks/task?space_id=42")).status
    ).toBe(200);
    const versions = await (
      await request("/evolve/teamclaw/versions?space_id=42&taskId=task")
    ).json();
    expect(versions.items).toHaveLength(1);
    expect(
      (await request("/evolve/teamclaw/versions?space_id=42&taskId=prod"))
        .status
    ).toBe(404);
  });
  it("keeps two authors with identical Domain IDs separate and personal legacy Domains private", async () => {
    const team = await (
      await request("/bench/teamclaw/domains?space_id=42")
    ).json();
    expect(
      team.map((d: { ownerUserId: string }) => d.ownerUserId).sort()
    ).toEqual(["author", "other"]);
    const personal = await (
      await request("/bench/teamclaw/domains?space_id=7")
    ).json();
    expect(personal.map((d: { domainId: string }) => d.domainId)).toEqual([
      "historic",
    ]);
    expect(
      (await request("/bench/teamclaw/domains/author/private?space_id=7"))
        .status
    ).toBe(404);
  });
  it("creates a Domain for actual actor, ignoring claimed owner/space", async () => {
    const response = await request(
      "/bench/teamclaw/domains?space_id=42",
      "POST",
      { domainId: "new", name: "new", ownerUserId: "author", space_id: "99" }
    );
    expect(response.status).toBe(201);
    const rows = await db.query<{ space_id: string }>(
      "SELECT space_id FROM cm_bench_domains WHERE owner_user_id='viewer' AND domain_id='new'"
    );
    expect(rows[0].space_id).toBe("42");
  });
  it("allows member template edits without actor-owned copies", async () => {
    const response = await request(
      "/bench/teamclaw/domains/author/same/templates/case?space_id=42",
      "PUT",
      {
        contentMd: "---\nid: case\nname: Case\n---\n# Case\nTest",
        displayName: "Case",
      }
    );
    expect(response.status).toBe(200);
    const rows = await db.query<{ owner_user_id: string }>(
      "SELECT owner_user_id FROM cm_bench_templates"
    );
    expect(rows.map((r) => r.owner_user_id)).toEqual(["author"]);
  });
  it.each([
    "/bench/teamclaw/runs/foreign",
    "/bench/teamclaw/runs/foreign/sessions",
    "/bench/teamclaw/runs/foreign/results",
    "/bench/teamclaw/runs/foreign/artifacts",
    "/bench/teamclaw/domains/viewer/elsewhere/templates",
    "/evolve/teamclaw/tasks/prod",
    "/evolve/teamclaw/tasks/skill",
  ])("rejects foreign direct URLs %s", async (path) =>
    expect((await request(path + "?space_id=42")).status).toBe(404)
  );
  it("validates Run parents while allowing same-space cross-author reads", async () =>
    expect((await request("/bench/teamclaw/runs/run?space_id=42")).status).toBe(
      200
    ));
  it.each([
    "/evolve/teamclaw/admin/owners",
    "/evolve/teamclaw/internal/tasks/task/steps/step/input",
    "/bench/teamclaw/admin/domains",
  ])("does not expose internal/admin endpoints %s", async (path) =>
    expect((await request(path + "?space_id=42")).status).toBe(404)
  );
  it("freezes two cross-author Domains without conflating runtime Owner or actor", async () => {
    for (const owner of ["author", "other"]) {
      await templates.create({
        ownerUserId: owner,
        domainId: "same",
        templateName: "case-" + owner,
        displayName: owner,
        status: "published",
      });
      await templates.update(owner, "same", "case-" + owner, {
        publishedVersion: 1,
      });
    }
    const response = await request(
      "/evolve/teamclaw/bench-optimizations?space_id=42",
      "POST",
      {
        taskName: "Cross author",
        userId: "author",
        botId: "bot",
        botEnv: "pre",
        trainBenchDomainId: "same",
        testBenchDomainId: "same",
        trainBenchOwnerUserId: "author",
        testBenchOwnerUserId: "other",
        objective: "goal",
        model: "test/model",
        maxRounds: 2,
      }
    );
    expect(response.status).toBe(201);
    const result = await response.json();
    const task = await tasks.findTask(result.task_id);
    expect(task?.user_id).toBe("author");
    expect(task?.created_by).toBe("viewer");
    const config = JSON.parse(task!.config_json);
    expect(config.entryPoint).toBe("teamclaw");
    expect(Object.keys(config.pinnedBenchDomains).sort()).toEqual([
      '["author","same"]',
      '["other","same"]',
    ]);
    expect(config.testBenchOwnerUserId).toBe("other");
  });
  it("Bench diagnose executes against the chosen resource author, without changing Bot Owner", async () => {
    await templates.create({
      ownerUserId: "other",
      domainId: "same",
      templateName: "case",
      displayName: "case",
      status: "published",
    });
    await templates.update("other", "same", "case", { publishedVersion: 1 });
    const response = await request(
      "/evolve/teamclaw/benches?space_id=42",
      "POST",
      {
        taskName: "Bench",
        userId: "author",
        botId: "bot",
        botEnv: "pre",
        benchDomainId: "same",
        benchDomainOwnerUserId: "other",
        model: "test/model",
      }
    );
    expect(response.status).toBe(201);
    const result = await response.json();
    expect(result.user_id).toBe("author");
    const saved = await tasks.findTask(result.task_id);
    expect(JSON.parse(saved!.config_json).bench.ownerUserId).toBe("other");
    expect(result.steps[0].command).toContain("--owner-id other");
  });
  it("does not authorize cross-space resources merely because a task references them", async () => {
    expect(
      (
        await request(
          "/evolve/teamclaw/bench-optimizations?space_id=42",
          "POST",
          {
            userId: "author",
            botId: "bot",
            botEnv: "pre",
            trainBenchDomainId: "private",
            testBenchDomainId: "same",
          }
        )
      ).status
    ).toBe(404);
  });
  it("preserves old Owner checks without requiring SpaceClient", async () => {
    spaces.mockRejectedValue(new Error("down"));
    expect((await request("/evolve/tasks/task")).status).toBe(403);
    expect(
      (await request("/evolve/tasks/task", "GET", undefined, "author")).status
    ).toBe(200);
    expect(
      (await request("/bench/domains/author/same", "PUT", { name: "attempt" }))
        .status
    ).toBe(403);
    expect(spaces).not.toHaveBeenCalled();
  });
  it("rejects browser-provided Skill callback context", async () => {
    expect(
      (
        await request("/bench/teamclaw/domains?space_id=42", "POST", {
          name: "bad",
          domainId: "bad",
          sourceTaskId: "task",
          sourceStepId: "step",
        })
      ).status
    ).toBe(400);
  });
  it.each([
    ["prod", ["production"]],
    ["gray", ["production"]],
    ["pre", ["preview", "production", "local", "unknown"]],
    ["dev", ["preview", "production", "local", "unknown"]],
  ])("filters only production Bot choices for server environment %s", async (env, expected) => {
    vi.stubEnv("SERVER_ENV", env);
    bots = [
      { ownerId: "author", botId: "preview", env: "pre", activeEngine: "openclaw", botName: "Preview" },
      { ownerId: "author", botId: "production", env: "prod", activeEngine: "openclaw", botName: "Production" },
      { ownerId: "author", botId: "local", env: "dev", activeEngine: "openclaw", botName: "Local" },
      { ownerId: "author", botId: "unknown", env: "", activeEngine: "openclaw", botName: "Unknown" },
      { ownerId: "author", botId: "unsupported", env: "prod", activeEngine: "teclaw", botName: "Unsupported" },
    ];
    const response = await request("/evolve/teamclaw/bots?space_id=42&env=pre");
    expect(response.status).toBe(200);
    const page = await response.json();
    expect(page.items.map((bot: SpaceBot) => bot.botId)).toEqual(expected);
    expect(page.total).toBe(expected.length);
  });
  it("filters production choices before paging and keeps search behavior", async () => {
    vi.stubEnv("SERVER_ENV", "prod");
    bots = Array.from({ length: 60 }, (_, i) => ({
      ownerId: "author", botId: `pre-${i}`, env: "pre", activeEngine: "openclaw", botName: "Preview",
    }));
    bots.push(...Array.from({ length: 3 }, (_, i) => ({
      ownerId: "author", botId: `prod-${i}`, env: "prod", activeEngine: "openclaw", botName: `Production ${i}`,
    })));
    const first = await (await request("/evolve/teamclaw/bots?space_id=42&pageSize=2")).json();
    const second = await (await request("/evolve/teamclaw/bots?space_id=42&pageSize=2&page=2")).json();
    expect(first.total).toBe(3);
    expect(first.items.map((bot: SpaceBot) => bot.botId)).toEqual(["prod-0", "prod-1"]);
    expect(second.total).toBe(3);
    expect(second.items.map((bot: SpaceBot) => bot.botId)).toEqual(["prod-2"]);
    const search = await (await request("/evolve/teamclaw/bots?space_id=42&query=prod-1")).json();
    expect(search.total).toBe(1);
    expect(search.items[0].botId).toBe("prod-1");
  });
  it("does not change historical task visibility or legacy task access in production", async () => {
    vi.stubEnv("SERVER_ENV", "prod");
    expect((await (await request("/evolve/teamclaw/bots?space_id=42")).json()).total).toBe(0);
    expect((await request("/evolve/teamclaw/tasks/task?space_id=42")).status).toBe(200);
    const page = await (await request("/evolve/teamclaw/tasks?space_id=42")).json();
    expect(page.total).toBe(1);
    expect(page.tasks[0].task_id).toBe("task");
    expect((await request("/evolve/tasks/task", "GET", undefined, "author")).status).toBe(200);
  });
  it("paginates the entire supported-engine Bot inventory", async () => {
    bots = Array.from({ length: 121 }, (_, i) => ({
      ownerId: "author",
      botId: `bot-${i}`,
      env: "pre",
      activeEngine: i === 120 ? "teclaw" : "openclaw",
      botName: `Bot ${i}`,
    }));
    const page = await (
      await request("/evolve/teamclaw/bots?space_id=42&page=3&pageSize=50")
    ).json();
    expect(page.total).toBe(120);
    expect(page.items).toHaveLength(20);
    expect(page.items[19].botId).toBe("bot-119");
  });
  it.each([false, true])(
    "validates role-specific callback Bench owners and passes them to Optimize (%s)",
    async (mismatch) => {
      await tasks.createTask({
        taskId: "callback",
        taskName: "Callback",
        taskType: "bench_optimize",
        userId: "author",
        botId: "bot",
        createdBy: "viewer",
        configJson: JSON.stringify({
          ownerUserId: "author",
          botEnv: "pre",
          entryPoint: "teamclaw",
          trainBenchDomainId: "same",
          testBenchDomainId: "same",
          trainBenchOwnerUserId: "author",
          testBenchOwnerUserId: "other",
          model: "test/model",
          maxRounds: 2,
          dispatchMode: "message",
        }),
      });
      await tasks.createStep({
        taskId: "callback",
        stepId: "callback-plan",
        stepType: "bench_plan",
        stepNo: 1,
        command: "plan",
      });
      for (const role of ["train", "test"])
        await runs.create({
          benchRunId: "baseline-" + role,
          domainId: "same",
          templateName: "__domain__",
          templateVersion: 0,
          ownerUserId: role === "test" && !mismatch ? "other" : "author",
          status: "succeeded",
          runConfigJson: JSON.stringify({
            evolveTaskId: "callback",
            evolveStepId: "callback-plan",
            role: "baseline_" + role,
          }),
        });
      const baseline = (role: string) => ({
        role,
        producerStepId: "callback-plan",
        source: "generated",
        ownerUserId: role === "train" ? "author" : "other",
        benchRunId: "baseline-" + role,
        domainId: "same",
        metrics: { score: 0.5 },
      });
      const result = await request(
        "/evolve/internal/tasks/callback/steps/callback-plan/report",
        "POST",
        {
          status: "succeeded",
          summary: "Baseline",
          output: {
            objective: { text: "goal", path: "/workspace/objective.md" },
            spec: {
              version: "v0",
              content_type: "text",
              content: "# Spec",
              path: "/workspace/spec.md",
            },
            baseline: { train: baseline("train"), test: baseline("test") },
          },
        },
        "author"
      );
      expect(result.status).toBe(mismatch ? 422 : 200);
      if (!mismatch) {
        const steps = await tasks.listSteps("callback");
        const command = steps.at(-1)!.command;
        expect(command).toContain("--owner-id author");
        expect(command).toContain("--train-bench-owner-id author");
        expect(command).toContain("--test-bench-owner-id other");
      }
    }
  );
  it("does not infer a historical environment until the Host proves uniqueness", async () => {
    await tasks.createTask({
      taskId: "legacy-env",
      taskName: "Legacy",
      taskType: "full",
      userId: "author",
      botId: "bot",
      createdBy: "author",
      configJson: "{}",
    });
    expect(
      (await request("/evolve/teamclaw/tasks/legacy-env?space_id=42")).status
    ).toBe(404);
    bots[0].unambiguousEnvironment = true;
    expect(
      (await request("/evolve/teamclaw/tasks/legacy-env?space_id=42")).status
    ).toBe(200);
    const page = await (
      await request("/evolve/teamclaw/tasks?space_id=42")
    ).json();
    expect(page.total).toBe(2);
  });
  it("revalidates live membership on every request after a member leaves", async () => {
    expect(
      (await request("/evolve/teamclaw/tasks/task?space_id=42")).status
    ).toBe(200);
    spaces.mockResolvedValue([]);
    expect(
      (await request("/evolve/teamclaw/tasks/task?space_id=42")).status
    ).toBe(403);
    expect(
      (await request("/bench/teamclaw/domains/author/same?space_id=42")).status
    ).toBe(403);
  });
  it.each(["ADMIN", "MEMBER"])(
    "grants equal space Bench writes for %s, without changing resource author",
    async (role) => {
      spaces.mockResolvedValue([
        { id: "42", name: "Team", type: "TEAM", role },
      ]);
      expect(
        (
          await request(
            "/bench/teamclaw/domains/author/same?space_id=42",
            "PUT",
            { name: "Updated" }
          )
        ).status
      ).toBe(200);
      expect(
        (await domains.findByOwnerAndDomainId("author", "same"))?.name
      ).toBe("Updated");
      expect(await domains.findByOwnerAndDomainId("viewer", "same")).toBeNull();
    }
  );
  it("does not use a Step belonging to another authorized parent", async () => {
    await tasks.createStep({
      taskId: "prod",
      stepId: "prod-step",
      stepType: "optimize",
      stepNo: 1,
      command: "optimize",
    });
    expect(
      (
        await request(
          "/evolve/teamclaw/tasks/task/steps/prod-step/diff?space_id=42"
        )
      ).status
    ).toBe(404);
  });
  it("does not hide a historical Bot version merely because its source type is not in the five-type task list", async () => {
    await tasks.createTask({
      taskId: "historical",
      taskName: "Historical",
      taskType: "hardening",
      userId: "author",
      botId: "bot",
      createdBy: "author",
      configJson: '{"botEnv":"pre"}',
    });
    await tasks.createStep({
      taskId: "historical",
      stepId: "historical-step",
      stepType: "optimize",
      stepNo: 1,
      roundNo: 1,
      command: "optimize",
    });
    await db.exec(
      "UPDATE ce_steps SET status='succeeded',output_json='{}' WHERE step_id='historical-step'"
    );
    const versions = await (
      await request("/evolve/teamclaw/versions?space_id=42")
    ).json();
    expect(
      versions.items.some((v: { taskId: string }) => v.taskId === "historical")
    ).toBe(true);
    const list = await (
      await request("/evolve/teamclaw/tasks?space_id=42")
    ).json();
    expect(
      list.tasks.some((t: { task_id: string }) => t.task_id === "historical")
    ).toBe(false);
    expect(
      (await request("/evolve/teamclaw/tasks/historical?space_id=42")).status
    ).toBe(200);
    expect(
      (
        await request(
          "/evolve/teamclaw/tasks/historical/steps/historical-step/retry?space_id=42",
          "POST",
          {}
        )
      ).status
    ).toBe(422);
  });
  it.each(["bench", "bench_optimize"])(
    "keeps cancel/logs available after a Bot move but revalidates Bench for retry (%s)",
    async (type) => {
      await db.exec(
        "UPDATE ce_tasks SET task_type=?,config_json=? WHERE task_id='task'",
        [
          type,
          JSON.stringify({
            botEnv: "pre",
            bench: { ownerUserId: "author", domainId: "same" },
            trainBenchOwnerUserId: "author",
            trainBenchDomainId: "same",
            testBenchOwnerUserId: "other",
            testBenchDomainId: "same",
          }),
        ]
      );
      // Bot directory represents its current new space; independent Bench stays behind.
      await db.exec(
        "UPDATE cm_bench_domains SET space_id='99' WHERE domain_id='same'"
      );
      await db.exec(
        "UPDATE ce_steps SET status='created' WHERE step_id='step'"
      );
      await tasks.createTaskLogArchive({
        archiveId: "archive",
        taskId: "task",
        requestedBy: "author",
      });
      const lookup = vi.spyOn(domains, "findInSpace");
      expect(
        (
          await request(
            "/evolve/teamclaw/tasks/task/log-archives?space_id=42",
            "POST",
            {}
          )
        ).status
      ).toBe(202);
      expect(
        (
          await request(
            "/evolve/teamclaw/tasks/task/steps/step/cancel?space_id=42",
            "POST",
            {}
          )
        ).status
      ).toBe(200);
      expect((await tasks.findStep("step"))?.status).toBe("canceled");
      expect(lookup).not.toHaveBeenCalled();
      expect(
        (
          await request(
            "/evolve/teamclaw/tasks/task/steps/step/retry?space_id=42",
            "POST",
            {}
          )
        ).status
      ).toBe(404);
      expect(lookup).toHaveBeenCalled();
      bots = [];
      expect(
        (
          await request(
            "/evolve/teamclaw/tasks/task/log-archives?space_id=42",
            "POST",
            {}
          )
        ).status
      ).toBe(404);
      expect(
        (
          await request(
            "/evolve/teamclaw/tasks/task/steps/step/cancel?space_id=42",
            "POST",
            {}
          )
        ).status
      ).toBe(404);
    }
  );
});
