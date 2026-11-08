import { Router } from "express";
import { getCurrentEnv } from "../env.js";
import type { SpaceDirectory } from "../contracts/space-directory.js";
import type { BenchDomainRepository } from "../repositories/bench-domain-repository.js";
import type { BenchRunRepository } from "../repositories/bench-run-repository.js";
import type { EvolveRepository } from "../repositories/evolve-repository.js";
import {
  domainKey,
  matchesSpaceBot,
  setTeamclawScope,
  type TeamclawBotDirectory,
  type TeamclawScope,
} from "../services/evolve/teamclaw-scope.js";

const primary = new Set([
  "diagnose",
  "bench",
  "bench_optimize",
  "optimize",
  "full",
]);
const visible = new Set([...primary, "pack", "pack_restore"]);
const fail = (status: number, message: string) =>
  Object.assign(new Error(message), { status });

/** Explicit new-frontend delivery adapter. Business handlers remain single-owner.
 * No client-provided identity/space can bypass live Host membership checks. */
export function createTeamclawRouter(input: {
  kind: "evolve" | "bench";
  legacy: Router;
  spaces?: SpaceDirectory;
  bots?: TeamclawBotDirectory;
  tasks: EvolveRepository;
  domains: BenchDomainRepository;
  runs: BenchRunRepository;
}): Router {
  const router = Router();
  router.use(async (req, res, next) => {
    try {
      const userId = String(req.header("X-User-Id") ?? "").trim();
      if (!userId) throw fail(401, "Missing authenticated identity");
      if (typeof req.query.space_id !== "string" || !req.query.space_id.trim())
        throw fail(400, "space_id is required");
      if (!input.spaces || (input.kind === "evolve" && !input.bots))
        throw fail(503, "Space services are unavailable");
      const identity = {
        userId,
        authorization: req.header("authorization"),
        cookie: req.header("cookie"),
        origin: req.header("origin"),
        referer: req.header("referer"),
      };
      const spaces = await input.spaces.listAccessibleSpaces({ identity });
      const space = spaces.find(
        (s) =>
          s.id === req.query.space_id && ["ADMIN", "MEMBER"].includes(s.role)
      );
      if (!space) throw fail(403, "Space access denied");
      const scope: TeamclawScope = {
        identity,
        space,
        bots:
          input.kind === "evolve" ? await input.bots!({ identity, space }) : [],
        taskIds: new Set(),
        domainKeys: new Set(),
      };
      setTeamclawScope(req, scope);
      const domain = async (owner: unknown, id: unknown) => {
        if (
          typeof owner !== "string" ||
          !owner ||
          typeof id !== "string" ||
          !id
        )
          throw fail(400, "Complete Bench Owner/Domain reference is required");
        if (
          !(await input.domains.findInSpace(
            owner,
            id,
            space.id,
            space.type === "PERSONAL" ? userId : undefined
          ))
        )
          throw fail(404, "Bench Domain not found in current space");
        scope.domainKeys.add(domainKey(owner, id));
      };
      const task = async (id: unknown) => {
        if (typeof id !== "string")
          throw fail(400, "Task reference is required");
        const row = await input.tasks.findTask(id);
        if (!row || !matchesSpaceBot(scope, row))
          throw fail(404, "Task not found in current space");
        scope.taskIds.add(id);
        return row;
      };
      const pack = async (id: unknown) => {
        const row =
          typeof id === "string" ? await input.tasks.findPack(id) : null;
        if (!row) throw fail(404, "Pack not found");
        await task(row.source_task_id);
      };
      const parts = req.path.split("/").filter(Boolean).map(decodeURIComponent);
      const body = req.body ?? {};
      if (input.kind === "evolve") {
        if (req.method === "GET" && req.path === "/bots") {
          const keyword = String(req.query.query ?? "")
            .trim()
            .toLowerCase();
          const engine = String(req.query.engine ?? "openclaw").toLowerCase();
          const production = getCurrentEnv() === "prod";
          const bots = scope.bots.filter(
            (b) =>
              b.activeEngine.toLowerCase() === engine &&
              engine === "openclaw" &&
              (!production || b.env === "prod") &&
              (!keyword ||
                [b.botName, b.botId, b.ownerId].some((s) =>
                  s.toLowerCase().includes(keyword)
                ))
          );
          const page = Number(req.query.page ?? 1),
            pageSize = Number(req.query.pageSize ?? 50);
          if (
            !Number.isSafeInteger(page) ||
            page < 1 ||
            !Number.isSafeInteger(pageSize) ||
            pageSize < 1 ||
            pageSize > 100
          )
            throw fail(400, "Invalid Bot pagination");
          res.json({
            items: bots.slice((page - 1) * pageSize, page * pageSize),
            total: bots.length,
            page,
            pageSize,
          });
          return;
        }
        if (parts[0] === "tasks" && parts[1]) {
          const target = await task(parts[1]);
          if (req.method !== "GET" && !visible.has(String(target.task_type)))
            throw fail(422, "This historical task is read-only in TeamClaw");
          // Only execution retries consume the frozen Bench inputs again.
          // Cancellation and log collection remain task operations when its Bot
          // has moved but the independent Domains remain in the old space.
          if (
            req.method === "POST" &&
            parts[2] === "steps" &&
            parts[4] === "retry"
          ) {
            const config = JSON.parse(target.config_json);
            if (target.task_type === "bench")
              await domain(
                config.bench?.ownerUserId ?? target.user_id,
                config.bench?.domainId
              );
            if (
              ["bench_optimize", "optimize", "full"].includes(target.task_type)
            ) {
              if (config.trainBenchDomainId)
                await domain(
                  config.trainBenchOwnerUserId ?? target.user_id,
                  config.trainBenchDomainId
                );
              if (config.testBenchDomainId)
                await domain(
                  config.testBenchOwnerUserId ?? target.user_id,
                  config.testBenchDomainId
                );
            }
          }
          if (parts[2] && !["steps", "log-archives"].includes(parts[2]))
            throw fail(404, "Unsupported task endpoint");
          if (parts[2] === "steps") {
            const step = await input.tasks.findStep(parts[3] ?? "");
            if (!step || step.task_id !== parts[1])
              throw fail(404, "Step not found");
            const read = new Set([
              undefined,
              "diff",
              "pack-download-url",
              "logs",
              "log",
              "artifacts",
              "sessions",
            ]);
            if (
              req.method === "GET"
                ? !read.has(parts[4])
                : req.method !== "POST" ||
                  !["retry", "cancel"].includes(parts[4] ?? "")
            )
              throw fail(404, "Unsupported step endpoint");
          } else if (parts[2] === "log-archives") {
            if (
              parts[3]
                ? req.method !== "GET" || parts[4] !== "download-url"
                : !["GET", "POST"].includes(req.method)
            )
              throw fail(404, "Unsupported log endpoint");
          } else if (req.method !== "GET")
            throw fail(404, "Unsupported task endpoint");
        } else if (parts[0] === "packs" && parts[1] && req.method === "GET")
          await pack(parts[1]);
        else if (
          req.method === "GET" &&
          ["/tasks", "/versions", "/packs", "/model-options"].includes(req.path)
        ) {
          if (req.query.taskId) await task(req.query.taskId);
        } else if (
          req.method === "POST" &&
          [
            "/tasks",
            "/diagnoses",
            "/benches",
            "/bench-optimizations",
            "/optimizations",
            "/packs",
            "/pack-restores",
          ].includes(req.path)
        ) {
          if (
            req.path === "/diagnoses" &&
            body.taskType != null &&
            body.taskType !== "diagnose"
          )
            throw fail(422, "Unsupported diagnosis type");
          if (
            [
              "input",
              "targetSkillAssetId",
              "nodeCommandYamls",
              "stageSelection",
              "stageExtensions",
              "apiKey",
              "callbackUrl",
            ].some((key) => body[key] != null)
          )
            throw fail(422, "Unsupported advanced task input");
          if (
            req.path === "/tasks" &&
            (!primary.has(body.taskType) ||
              !["diagnose", "full"].includes(body.taskType) ||
              body.targetSkillAssetId ||
              (body.taskType === "full" && body.inputMode !== "direct_goal"))
          )
            throw fail(422, "Unsupported task type");
          const bot = scope.bots.find(
            (b) =>
              b.ownerId === body.userId &&
              b.botId === body.botId &&
              b.env === body.botEnv &&
              b.activeEngine.toLowerCase() === "openclaw"
          );
          if (!bot) throw fail(404, "Bot not found in current space");
          if (req.path === "/benches")
            await domain(
              body.benchDomainOwnerUserId ?? body.userId,
              body.benchDomainId
            );
          if (req.path === "/bench-optimizations") {
            await domain(
              body.trainBenchOwnerUserId ?? body.userId,
              body.trainBenchDomainId
            );
            await domain(
              body.testBenchOwnerUserId ?? body.userId,
              body.testBenchDomainId
            );
          }
          if (
            body.sourceDiagnosisTaskIds != null &&
            !Array.isArray(body.sourceDiagnosisTaskIds)
          )
            throw fail(400, "Invalid diagnosis sources");
          for (const id of body.sourceDiagnosisTaskIds ?? []) {
            const source = await task(id);
            const config = JSON.parse(source.config_json);
            if (
              source.user_id !== bot.ownerId ||
              source.bot_id !== bot.botId ||
              (String(config.botEnv ?? "") ||
                (bot.unambiguousEnvironment ? bot.env : "")) !== bot.env
            )
              throw fail(422, "Diagnosis source Bot/environment mismatch");
            const plan = (await input.tasks.listSteps(id))
              .filter((s) => s.step_type === "plan" && s.status === "succeeded")
              .at(-1);
            const domains = plan
              ? JSON.parse(plan.output_json ?? "{}")?.benchDomains
              : null;
            if (domains?.trainBenchDomainId)
              await domain(source.user_id, domains.trainBenchDomainId);
            if (domains?.testBenchDomainId)
              await domain(source.user_id, domains.testBenchDomainId);
          }
          if (body.sourceTaskId) await task(body.sourceTaskId);
          if (body.packId) await pack(body.packId);
        } else throw fail(404, "Unsupported TeamClaw endpoint");
      } else {
        if (parts[0] === "domains") {
          if (parts.length === 1) {
            if (
              req.method === "POST" &&
              (body.sourceTaskId != null || body.sourceStepId != null)
            )
              throw fail(
                400,
                "Skill source context is not a browser creation field"
              );
            if (!["GET", "POST"].includes(req.method))
              throw fail(404, "Unsupported endpoint");
          } else {
            if (parts.length < 3)
              throw fail(400, "Complete Bench reference is required");
            await domain(parts[1], parts[2]);
            if (
              parts[3] &&
              !["templates", "uploads", "summary"].includes(parts[3])
            )
              throw fail(404, "Unsupported Domain endpoint");
          }
        } else if (parts[0] === "runs" && req.method === "GET") {
          if (parts[1]) {
            const run = await input.runs.findByBenchRunId(parts[1]);
            if (!run) throw fail(404, "Bench Run not found");
            await domain(String(run.owner_user_id), String(run.domain_id));
          } else await domain(req.query.ownerUserId, req.query.domainId);
        } else throw fail(404, "Unsupported Bench endpoint");
      }
      input.legacy(req, res, next);
    } catch (error) {
      const status = Number((error as { status?: number }).status ?? 503);
      res.status(status >= 400 && status < 600 ? status : 503).json({
        error:
          status < 500
            ? (error as Error).message
            : "Space resource service unavailable",
      });
    }
  });
  return router;
}
