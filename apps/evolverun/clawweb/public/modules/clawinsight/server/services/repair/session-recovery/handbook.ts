import type { RecoveryRequest } from "./contracts.js";
import type { RecoveryStoredTask } from "./task-contracts.js";
import { SessionRecoveryError } from "./errors.js";

export const SESSION_RECOVERY_HANDBOOKS_KEY = "session_recovery_handbooks";
export type RecoveryHandbookConfigStore = {
  findByKey(key: string): Promise<{ enabled: number; config_json: string } | null>;
};
type BotTeam = { env: "pre" | "prod"; bot_id: string; owner_id: string; team_key: string };
type HandbookConfig = { bot_teams: BotTeam[]; team_routes: Record<string, Record<string, string[]>> };

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function invalid(): never {
  throw new SessionRecoveryError(503, "handbook_configuration_invalid", "团队手册配置无效，未创建自愈任务");
}
function parseConfig(raw: string): HandbookConfig {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { invalid(); }
  if (!record(value) || Object.keys(value).some(key => !["bot_teams", "team_routes"].includes(key))
    || !record(value.team_routes)) invalid();
  const teamKey = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  const botTeams: unknown[] = [];
  if (Array.isArray(value.bot_teams)) botTeams.push(...value.bot_teams);
  else {
    if (!record(value.bot_teams)) invalid();
    for (const [team, identities] of Object.entries(value.bot_teams)) {
      if (!teamKey.test(team) || !Object.hasOwn(value.team_routes, team) || !Array.isArray(identities)) invalid();
      for (const identity of identities) {
        if (typeof identity !== "string") invalid();
        const parts = identity.split("|");
        if (parts.length !== 3) invalid();
        const [env, bot_id, owner_id] = parts;
        botTeams.push({ env, bot_id, owner_id, team_key: team });
      }
    }
  }
  const targets = new Set<string>();
  for (const item of botTeams) {
    if (!record(item) || Object.keys(item).length !== 4
      || Object.keys(item).some(key => !["env", "bot_id", "owner_id", "team_key"].includes(key))
      || item.env !== "pre" && item.env !== "prod"
      || typeof item.bot_id !== "string" || !/^[A-Za-z0-9_-]+$/.test(item.bot_id)
      || typeof item.owner_id !== "string" || !/^[A-Za-z0-9_.@+-]+$/.test(item.owner_id) || item.owner_id.includes("..")
      || typeof item.team_key !== "string" || !teamKey.test(item.team_key)
      || !Object.hasOwn(value.team_routes, item.team_key)) invalid();
    const target = JSON.stringify([item.env, item.bot_id, item.owner_id]);
    if (targets.has(target)) invalid();
    targets.add(target);
  }
  for (const [team, routes] of Object.entries(value.team_routes)) {
    if (!teamKey.test(team) || !record(routes)) invalid();
    for (const [category, paths] of Object.entries(routes)) {
      if (!/^TC\.[A-Z_]+$/.test(category) || !Array.isArray(paths)
        || paths.some(path => typeof path !== "string" || path.length > 256
          || !/^(?:[a-z0-9][a-z0-9_-]*\/)+handbook\.md$/.test(path))
        || new Set(paths).size !== paths.length) invalid();
    }
  }
  return { bot_teams: botTeams as BotTeam[], team_routes: value.team_routes as HandbookConfig["team_routes"] };
}

/** Content is owned by the AIS Agent Skill; this catalog contains references only. */
const CHAPTERS: Record<string, readonly string[]> = {
  "TC.MCP": ["common/mcp/handbook.md", "common/file-download/handbook.md"],
  "TC.MODEL": ["common/file-download/handbook.md"],
  "TC.TOOL": ["common/file-download/handbook.md"],
  "TC.SKILL": ["common/skill/handbook.md", "common/file-download/handbook.md"],
  "TC.SESSION_CONTEXT": ["common/file-download/handbook.md"],
};
export function recoveryHandbook(code: string | null) {
  const category = code?.match(/^TC\.([A-Z_]+)(?:\.|$)/)?.[0].replace(/\.$/, "") ?? null;
  const chapters = category ? [...(CHAPTERS[category] ?? [])] : [];
  return { skillName: "clawevolve-recovery-handbook", handbookVersion: "clawevolve-recovery-handbook/v3", category, teamKey: null as string | null, chapters,
    verificationReference: "对照原先失败的操作与其后实际返回，确认原故障解除并推动原任务；工具传输成功或口头宣称不构成恢复证据。" };
}
/** Resolve once at creation; resumed tasks keep their saved team and chapter references. */
export async function resolveRecoveryHandbook(request: RecoveryRequest, config: RecoveryHandbookConfigStore) {
  const handbook = recoveryHandbook(request.tc_fault_label);
  const row = await config.findByKey(SESSION_RECOVERY_HANDBOOKS_KEY);
  if (!row || row.enabled === 0) return handbook;
  if (row.enabled !== 1) invalid();
  const prepared = parseConfig(row.config_json);
  const target = request.target;
  const team = prepared.bot_teams.find(item => item.env === target.env
    && item.bot_id === target.bot_id && item.owner_id === target.entity_id)?.team_key ?? null;
  const custom = team && handbook.category ? prepared.team_routes[team][handbook.category] ?? [] : [];
  return { ...handbook, teamKey: team,
    chapters: [...new Set([...custom.map(path => `custom/${team}/${path}`), ...handbook.chapters])] };
}
type RecoveryHandbook = ReturnType<typeof recoveryHandbook>;
type LegacyRecoveryHandbook = Omit<RecoveryHandbook, "chapters"> & { chapter: string | null };
/** Use the frozen creation snapshot, not the current handbook release, on resume. */
export function savedRecoveryHandbook(task: RecoveryStoredTask) {
  const prepared = [...task.timeline].reverse().find(event => event.kind === "handbook");
  const saved = (prepared ?? task.timeline.find(event => event.kind === "created"))?.payload.handbook as RecoveryHandbook | LegacyRecoveryHandbook | undefined;
  if (!saved || "chapters" in saved) return saved;
  const { chapter, ...reference } = saved;
  return { ...reference, chapters: chapter ? [chapter] : [] };
}
