import type { Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { RequestIdentity } from "@avernet/clawweb-shared/server/types/request-identity";
import type { EvolveRepository } from "../repositories/evolve-repository.js";
import type { BenchDomainRepository } from "../repositories/bench-domain-repository.js";

/** A trusted Host lookup, not a space ID supplied by the Skill. */
export type GeneratedBenchBotSpaceResolver = (input: {
  ownerId: string; botId: string; environment: string; identity: RequestIdentity;
}) => Promise<string>;

/** Register before the original Bench router. Requests without a TeamClaw task
 * context continue through the original handler, with its existing semantics. */
export function registerGeneratedBenchDomainRoute(router: Router, input: {
  tasks: Pick<EvolveRepository, "findTask" | "findStep">;
  domains: BenchDomainRepository;
  resolveBotSpace?: GeneratedBenchBotSpaceResolver;
}): void {
  router.post("/domains", asyncHandler(async (req, res, next) => {
    const { sourceTaskId, sourceStepId } = req.body ?? {};
    if (sourceTaskId === undefined && sourceStepId === undefined) { next(); return; }
    if (typeof sourceTaskId !== "string" || !sourceTaskId.trim()
        || typeof sourceStepId !== "string" || !sourceStepId.trim()) {
      res.status(400).json({ error: "sourceTaskId and sourceStepId must be supplied together" }); return;
    }
    // Preserve the original Skill authentication channel. IDs are references,
    // never authorization credentials; verify the caller and the Step linkage.
    const actor = req.header("X-User-Id")?.trim();
    if (!actor) { res.status(401).json({ error: "Missing user identity" }); return; }
    const [task, step] = await Promise.all([
      input.tasks.findTask(sourceTaskId.trim()), input.tasks.findStep(sourceStepId.trim()),
    ]);
    if (!task || String(task.user_id) !== actor || !step || String(step.task_id) !== String(task.task_id)) {
      res.status(404).json({ error: "Source Plan task/step is unavailable" }); return;
    }
    const config: Record<string, unknown> = JSON.parse(task.config_json);
    // Only the new frontend's server-side creation adapter sets this marker.
    // No space snapshot is stored in the task, and legacy tasks stay personal.
    if (config.entryPoint !== "teamclaw") { next(); return; }
    if (String(step.step_type) !== "plan") { res.status(404).json({error:"Source Plan task/step is unavailable"}); return; }
    const domainId = String(req.body?.domainId ?? "").trim();
    const name = String(req.body?.name ?? "").trim();
    if (!domainId || !name) { res.status(400).json({ error: "Missing domainId or name" }); return; }
    if (await input.domains.findByOwnerAndDomainId(actor, domainId)) {
      // A retry must not move an existing Domain when the Bot has moved spaces.
      res.status(409).json({ error: "Domain already exists" }); return;
    }
    if (!input.resolveBotSpace || typeof config.botEnv !== "string" || !config.botEnv.trim()) {
      res.status(503).json({ error: "Generated Bench space lookup is unavailable" }); return;
    }
    const spaceId = await input.resolveBotSpace({
      ownerId: String(task.user_id), botId: String(task.bot_id), environment: config.botEnv,
      identity: { userId: actor },
    });
    if (typeof spaceId !== "string" || !spaceId.trim()) {
      res.status(503).json({ error: "Generated Bench space lookup is incomplete" }); return;
    }
    const row = await input.domains.create({
      domainId, name, description: req.body?.description, ownerUserId: actor,
      spaceId, // Do not read space_id/spaceId or Owner claims from the request.
    });
    res.status(201).json({
      id: row.id, domainId: String(row.domain_id), name: String(row.name),
      description: row.description == null ? null : String(row.description),
      status: String(row.status), createdBy: row.created_by == null ? null : String(row.created_by),
      ownerUserId: String(row.owner_user_id),
      gmtCreate: row.gmt_create, gmtModified: row.gmt_modified,
    });
  }));
}
