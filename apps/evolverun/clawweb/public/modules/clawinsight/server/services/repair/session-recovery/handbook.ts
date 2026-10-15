import type { RecoveryRequest } from "./contracts.js";
import type { RecoveryStoredTask } from "./task-contracts.js";

/** Content is owned by the AIS Agent Skill; this catalog contains references only. */
const CHAPTERS: Record<string, string> = { "TC.MCP": "mcp/handbook.md" };
export function recoveryHandbook(code: string | null) {
  const category = code?.match(/^TC\.([A-Z_]+)(?:\.|$)/)?.[0].replace(/\.$/, "") ?? null;
  const chapter = category ? CHAPTERS[category] ?? null : null;
  return { skillName: "clawevolve-recovery-handbook", handbookVersion: "clawevolve-recovery-handbook/v1", category, chapter,
    verificationReference: "对照原先失败的操作与其后实际返回，确认原故障解除并推动原任务；工具传输成功或口头宣称不构成恢复证据。" };
}
/** Use the frozen creation snapshot, not the current handbook release, on resume. */
export function savedRecoveryHandbook(task: RecoveryStoredTask) {
  const prepared = [...task.timeline].reverse().find(event => event.kind === "handbook");
  return (prepared ?? task.timeline.find(event => event.kind === "created"))?.payload.handbook as ReturnType<typeof recoveryHandbook>;
}
