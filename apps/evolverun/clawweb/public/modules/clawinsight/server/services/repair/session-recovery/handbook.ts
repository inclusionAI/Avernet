import type { RecoveryRequest } from "./contracts.js";
import type { RecoveryStoredTask } from "./task-contracts.js";

/** Content is owned by the AIS Agent Skill; this catalog contains references only. */
const CHAPTERS: Record<string, readonly string[]> = {
  "TC.MCP": ["mcp/handbook.md", "file-download/handbook.md"],
  "TC.MODEL": ["file-download/handbook.md"],
  "TC.TOOL": ["file-download/handbook.md"],
  "TC.SKILL": ["file-download/handbook.md"],
  "TC.SESSION_CONTEXT": ["file-download/handbook.md"],
};
export function recoveryHandbook(code: string | null) {
  const category = code?.match(/^TC\.([A-Z_]+)(?:\.|$)/)?.[0].replace(/\.$/, "") ?? null;
  const chapters = category ? [...(CHAPTERS[category] ?? [])] : [];
  return { skillName: "clawevolve-recovery-handbook", handbookVersion: "clawevolve-recovery-handbook/v2", category, chapters,
    verificationReference: "对照原先失败的操作与其后实际返回，确认原故障解除并推动原任务；工具传输成功或口头宣称不构成恢复证据。" };
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
