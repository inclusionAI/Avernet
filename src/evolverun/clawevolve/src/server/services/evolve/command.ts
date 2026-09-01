const MAX_RENDERED_COMMAND_BYTES = 64 * 1024;
const MAX_EVOLUTION_GOAL_LENGTH = 2000;
const MAX_DIAGNOSE_INTENT_LENGTH = 4000;

export type NodeCommandKey = "diagnose" | "plan" | "bench" | "bench_plan" | "optimize";
export type NodeCommandYamls = Partial<Record<NodeCommandKey, string>>;
export type DiagnoseJudgeBackend = "subagent" | "api";
export type OpenClawExecutionMode = "local" | "gateway";

export function resolveOpenClawExecutionMode(value: unknown): OpenClawExecutionMode {
  const normalized = String(value ?? "local").trim().toLowerCase();
  if (normalized !== "local" && normalized !== "gateway") {
    throw new Error("openclawExecutionMode 必须是 local 或 gateway");
  }
  return normalized;
}

export function resolveDiagnoseJudgeBackend(value: unknown, apiKey: unknown): DiagnoseJudgeBackend {
  const explicit = String(value ?? "").trim().toLowerCase();
  if (explicit && explicit !== "subagent" && explicit !== "api") {
    throw new Error("judgeBackend 必须是 subagent 或 api");
  }
  return explicit === "api" || explicit === "subagent"
    ? explicit
    : String(apiKey ?? "").trim() ? "api" : "subagent";
}

export function readDiagnoseJudgeBackend(command: string): DiagnoseJudgeBackend {
  const match = command.match(/(?:^|\s)--judge[_-]backend(?:=|\s+)([^\s]+)/i);
  return match?.[1]?.toLowerCase() === "subagent" ? "subagent" : "api";
}

export function redactSecret(value: unknown, secret: string): unknown {
  if (!secret) return value;
  if (typeof value === "string") return value.replaceAll(secret, "******");
  if (Array.isArray(value)) return value.map((item) => redactSecret(item, secret));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, child]) => [key, redactSecret(child, secret)]),
    );
  }
  return value;
}

export function normalizeEvolutionGoal(value: unknown): string {
  if (value == null) return "";
  if (typeof value !== "string") throw new Error("goal 必须是字符串");
  if (value.includes("\0")) throw new Error("goal 不能包含 NUL 字符");
  const normalized = value.trim().replace(/\s+/g, " ");
  if (normalized.length > MAX_EVOLUTION_GOAL_LENGTH) {
    throw new Error(`goal 不能超过 ${MAX_EVOLUTION_GOAL_LENGTH} 个字符`);
  }
  return normalized;
}

export function normalizeDiagnoseIntent(value: unknown): string {
  if (typeof value !== "string") throw new Error("diagnoseIntent 必须是字符串");
  if (value.includes("\0")) throw new Error("diagnoseIntent 不能包含 NUL 字符");
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) throw new Error("diagnoseIntent 不能为空");
  if (normalized.length > MAX_DIAGNOSE_INTENT_LENGTH) {
    throw new Error(`diagnoseIntent 不能超过 ${MAX_DIAGNOSE_INTENT_LENGTH} 个字符`);
  }
  return normalized;
}

export function quoteCommandArgument(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function readNodeCommandOption(
  command: string,
  name: "model" | "suite" | "judge",
): string | undefined {
  const match = command.match(new RegExp(`(?:^|\\s)--${name}(?:=|\\s+)([^\\s]+)`));
  return match?.[1];
}

export function renderCommand(
  template: string,
  values: Record<string, string | number | undefined>,
  systemArgs: Array<[string, string | number]>,
): string {
  let command = template;
  for (const [key, value] of Object.entries(values)) {
    command = command.replaceAll(`{{${key}}}`, value == null ? "" : String(value));
  }
  const unresolved = command.match(/\{\{[^}]+\}\}/)?.[0];
  if (unresolved) throw new Error(`命令模板存在未解析变量: ${unresolved}`);
  const rendered = `${command} ${systemArgs.map(([key, value]) => `--${key} ${value}`).join(" ")}`.trim();
  if (Buffer.byteLength(rendered, "utf8") > MAX_RENDERED_COMMAND_BYTES) {
    throw new Error("渲染后的节点命令不能超过 64 KiB");
  }
  return rendered;
}
