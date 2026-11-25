import type { Request } from "express";
import type { SpaceDirectory } from "../contracts/space-directory.js";
import type { EvolveTaskRow } from "../repositories/evolve-repository.js";
import { teamclawScope } from "../services/evolve/teamclaw-scope.js";

function configOf(task: EvolveTaskRow): Record<string, unknown> {
  try {
    const config: unknown = JSON.parse(task.config_json);
    return config && typeof config === "object" && !Array.isArray(config)
      ? config as Record<string, unknown> : {};
  } catch { return {}; }
}

function skillTarget(task: EvolveTaskRow): Record<string, unknown> | null {
  const target = configOf(task).targetSkill;
  return target && typeof target === "object" && !Array.isArray(target)
    ? target as Record<string, unknown> : null;
}

export function isSkillTask(task: EvolveTaskRow): boolean {
  const target = skillTarget(task);
  return typeof target?.assetId === "string" && Boolean(target.assetId);
}

/** Browser task permissions reuse the Host's live space membership. A frozen
 * implementation's space is never evidence of the target Skill's ownership. */
export function createEvolveTaskAccess(input: {
  spaces?: SpaceDirectory;
  resolveUserId: (request: Request) => string | null;
}) {
  const memberships = new WeakMap<Request, Promise<string[]>>();
  function teamSpaceIds(req: Request): Promise<string[]> {
    const actor = input.resolveUserId(req);
    if (!actor || !input.spaces) return Promise.resolve([]);
    let pending = memberships.get(req);
    if (!pending) {
      pending = input.spaces.listAccessibleSpaces({ identity: {
        userId: actor, authorization: req.header("Authorization"), cookie: req.header("Cookie"),
        origin: req.header("Origin"), referer: req.header("Referer"),
      } }).then(spaces => spaces.filter(space => space.type === "TEAM"
        && (space.role === "ADMIN" || space.role === "MEMBER")).map(space => space.id));
      memberships.set(req, pending);
    }
    return pending;
  }
  async function teamMember(req: Request, task: EvolveTaskRow): Promise<boolean> {
    const target = skillTarget(task);
    if (!isSkillTask(task) || !target || target.spaceType !== "TEAM" || typeof target.spaceId !== "string"
      || !target.spaceId) return false;
    return (await teamSpaceIds(req)).includes(target.spaceId);
  }
  async function canReadTask(req: Request, task: EvolveTaskRow): Promise<boolean> {
    const scope = teamclawScope(req);
    if (scope) return scope.taskIds.has(String(task.task_id));
    const actor = input.resolveUserId(req);
    return Boolean(actor && (actor === "dev_local" || task.user_id === actor || task.created_by === actor
      || req.isClawEvolveAdmin || configOf(task).shared === true || await teamMember(req, task)));
  }
  async function canManageTask(req: Request, task: EvolveTaskRow): Promise<boolean> {
    const scope = teamclawScope(req);
    if (scope) return scope.taskIds.has(String(task.task_id));
    const actor = input.resolveUserId(req);
    return Boolean(actor && (actor === "dev_local" || task.created_by === actor
      || req.isClawEvolveAdmin || await teamMember(req, task)));
  }
  async function canManageTaskLogs(req: Request, task: EvolveTaskRow): Promise<boolean> {
    const scope = teamclawScope(req);
    if (scope) return scope.taskIds.has(String(task.task_id));
    const actor = input.resolveUserId(req);
    return Boolean(actor && (actor === task.user_id || await canManageTask(req, task)));
  }
  return { canReadTask, canManageTask, canManageTaskLogs, teamSpaceIds };
}
