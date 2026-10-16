import type { Request } from "express";
import type { RequestIdentity } from "../../contracts/request-identity.js";
import type { AccessibleSpace } from "../../contracts/space-directory.js";

export type SpaceBot = {
  ownerId: string;
  botId: string;
  env: string;
  botName: string;
  activeEngine: string;
  deviceProvider?: string | null;
  botType?: string | null;
  hasServiceBot?: boolean;
  unambiguousEnvironment?: boolean;
};
export type TeamclawBotDirectory = (input: {
  identity: RequestIdentity;
  space: AccessibleSpace;
}) => Promise<SpaceBot[]>;
export type TeamclawScope = {
  identity: RequestIdentity;
  space: AccessibleSpace;
  bots: SpaceBot[];
  taskIds: Set<string>;
  domainKeys: Set<string>;
};
// Only a verified server middleware can create this context; headers/body cannot.
const scopes = new WeakMap<object, TeamclawScope>();
export const teamclawScope = (request: object) => scopes.get(request);
export const setTeamclawScope = (request: object, scope: TeamclawScope) =>
  scopes.set(request, scope);
export const domainKey = (owner: string, domain: string) =>
  JSON.stringify([owner, domain]);
export const teamclawTaskConfig = (
  request: Request,
  config: Record<string, unknown>
) => (teamclawScope(request) ? { ...config, entryPoint: "teamclaw" } : config);
export function spaceBotPredicate(
  bots: SpaceBot[],
  alias = "",
  sqlite = false
) {
  const c = (name: string) => `${alias ? `${alias}.` : ""}${name}`;
  const json = `CASE WHEN JSON_VALID(${c("config_json")}) THEN ${c(
    "config_json"
  )} ELSE '{}' END`;
  const rawEnv = `JSON_EXTRACT(${json}, '$.botEnv')`;
  const env = sqlite
    ? rawEnv
    : `CASE WHEN JSON_TYPE(${rawEnv}) = 'NULL' THEN NULL ELSE JSON_UNQUOTE(${rawEnv}) END`;
  const target = `JSON_EXTRACT(${json}, '$.targetSkillAssetId')`;
  const targetValue = sqlite
    ? target
    : `CASE WHEN JSON_TYPE(${target}) = 'NULL' THEN NULL ELSE JSON_UNQUOTE(${target}) END`;
  // Five-type filtering belongs to the task list. Historical registered versions
  // must not disappear just because their source type has no creation form here.
  const boundary = `JSON_VALID(${c(
    "config_json"
  )}) AND LOWER(JSON_TYPE(${json})) = 'object' AND COALESCE(${targetValue}, '') = ''`;
  return {
    sql: bots.length
      ? `(${boundary} AND (${bots
          .map(
            (bot) =>
              `(${c("user_id")} = ? AND ${c(
                "bot_id"
              )} = ? AND (COALESCE(${env}, '') = ?${
                bot.unambiguousEnvironment
                  ? ` OR COALESCE(${env}, '') = ''`
                  : ""
              }))`
          )
          .join(" OR ")}))`
      : "1 = 0",
    values: bots.flatMap((bot) => [bot.ownerId, bot.botId, bot.env]),
  };
}
export function matchesSpaceBot(
  scope: TeamclawScope,
  task: { user_id: string; bot_id: string; config_json: string }
) {
  let env: string;
  try {
    const config = JSON.parse(task.config_json);
    if (
      !config ||
      typeof config !== "object" ||
      Array.isArray(config) ||
      config.targetSkillAssetId
    )
      return false;
    env = String(config.botEnv ?? "");
  } catch {
    return false;
  }
  return scope.bots.some(
    (bot) =>
      bot.ownerId === String(task.user_id) &&
      bot.botId === String(task.bot_id) &&
      (env === bot.env || (!env && bot.unambiguousEnvironment))
  );
}

export function teamclawCanManageDomainOwner(
  request: object,
  owner: string
): boolean {
  const scope = teamclawScope(request);
  return (
    !!scope && [...scope.domainKeys].some((key) => JSON.parse(key)[0] === owner)
  );
}
