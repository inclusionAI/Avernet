import { SessionRecoveryError } from "./errors.js";
import type { RecoveryStoredTask } from "./task-contracts.js";

export const SESSION_RECOVERY_HANDBOOKS_KEY = "session_recovery_handbooks";
export type RecoveryHandbookConfigStore = {
  findByKey(key: string): Promise<{ enabled: number; config_json: string } | null>;
};
/** Content is owned by the AIS Agent Skill; this catalog contains references only. */
const CHAPTERS: Record<string, readonly string[]> = {
  "TC.MCP": ["common/mcp/handbook.md", "common/file-download/handbook.md", "common/network/handbook.md"],
  "TC.MODEL": ["common/file-download/handbook.md", "common/network/handbook.md"],
  "TC.TOOL": ["common/file-download/handbook.md", "common/network/handbook.md"],
  "TC.SKILL": ["common/skill/handbook.md", "common/file-download/handbook.md", "common/network/handbook.md"],
  "TC.SESSION_CONTEXT": ["common/file-download/handbook.md"],
};
export function recoveryHandbook(code: string | null) {
  const category = code?.match(/^TC\.([A-Z_]+)(?:\.|$)/)?.[0].replace(/\.$/, "") ?? null;
  const chapters = category ? [...(CHAPTERS[category] ?? [])] : [];
  return { skillName: "clawevolve-recovery-handbook", handbookVersion: "clawevolve-recovery-handbook/v3", category, teamKey: null as string | null, chapters,
    verificationReference: "对照原先失败的操作与其后实际返回，确认原故障解除并推动原任务；工具传输成功或口头宣称不构成恢复证据。" };
}
/** Compose references from an already resolved team; source compatibility belongs to bot-configuration. */
export function recoveryHandbookForTeam(code: string | null, team: string | null, routes: Record<string, Record<string, string[]>>) {
  const handbook = recoveryHandbook(code);
  const custom = team && handbook.category ? routes[team]?.[handbook.category] ?? [] : [];
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

/** Shared current route schema, independent of the historical Bot roster. */
export function parseRecoveryHandbookRoutes(value: unknown): Record<string, Record<string, string[]>> {
  const record = (input: unknown): input is Record<string, unknown> => input !== null && typeof input === 'object' && !Array.isArray(input);
  const invalid = (): never => { throw new SessionRecoveryError(503, 'handbook_configuration_invalid', '团队手册配置无效，未创建自愈任务'); };
  if (!record(value)) return invalid();
  for (const [team, routes] of Object.entries(value)) {
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(team) || !record(routes)) return invalid();
    for (const [category, paths] of Object.entries(routes)) {
      if (!/^TC\.[A-Z_]+$/.test(category) || !Array.isArray(paths)
        || paths.some(path => typeof path !== 'string' || path.length > 256
          || !/^(?:[a-z0-9][a-z0-9_-]*\/)+handbook\.md$/.test(path))
        || new Set(paths).size !== paths.length) return invalid();
    }
  }
  return value as Record<string, Record<string, string[]>>;
}
