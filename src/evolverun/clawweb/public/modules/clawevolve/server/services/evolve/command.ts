import { parse as parseYaml } from "yaml";

export type NodeCommandKey = "diagnose" | "plan" | "bench" | "bench_plan" | "optimize";
export type NodeCommandYamls = Partial<Record<NodeCommandKey, string>>;

const MAX_RENDERED_COMMAND_BYTES = 64 * 1024;
const MAX_EVOLUTION_GOAL_LENGTH = 2000;
const MAX_DIAGNOSE_INTENT_LENGTH = 4000;
const FORBIDDEN_NODE_CONTROL_ARGUMENTS = /--(?:task[_-]id|step[_-]id|judge[_-]backend|max[_-]sessions|domain[_-]id|train[_-](?:bench[_-])?domain[_-]id|test[_-](?:bench[_-])?domain[_-]id|owner[_-]id|round|action|final[_-]action|prepare[_-]only|workspace|skill[_-]base[_-]dir|clawweb[_-]url(?:[_-](?:legacy|camel))?|runner[_-]path|local[_-](?:opt|val)[_-]template[_-]dir|bench[_-]mode|openclaw[_-]execution[_-]mode|clawbench[_-]home|bench[_-]run[_-]id|bench[_-]dir|output[_-]dir|result[_-]path|artifact[_-]path|baseline[_-]artifact|start[_-]round|skip[_-](?:clawweb|oss)|no[_-]resume)(?:\s|=|$)/i;

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

export function parseNodeCommandYaml(value: unknown, node: NodeCommandKey): string | undefined {
  if (value == null || value === "") return undefined;
  if (typeof value !== "string") throw new Error(`${node} 节点 YAML 必须是字符串`);
  let parsed: unknown;
  try { parsed = parseYaml(value); }
  catch (error) { throw new Error(`${node} 节点 YAML 格式错误: ${error instanceof Error ? error.message : String(error)}`); }
  const record = parsed as { version?: unknown; command?: unknown } | null;
  if (record?.version !== "1.0") throw new Error(`${node} 节点 YAML version 必须是 "1.0"`);
  if (typeof record.command !== "string" || !/^\/claw[^\s]*/.test(record.command.trim())) {
    throw new Error(`${node} 节点 command 必须是 /claw 开头的 Bot Message 指令`);
  }
  const command = record.command.trim();
  const expected: Partial<Record<NodeCommandKey, RegExp>> = {
    diagnose: /^\/clawevolve-diagnose(?:\s|$)/,
    plan: /^\/clawevolve-plan(?:\s|$)/,
    bench: /^\/clawevolve-bench(?:\s|$)/,
    bench_plan: /^\/clawevolve-workflow\s+--stage\s+bench-plan(?:\s|$)/,
    optimize: /^\/clawevolve-workflow\s+--stage\s+optimize(?:\s|$)/,
  };
  if (!expected[node]?.test(command)) throw new Error(`${node} 节点 command 与节点类型不匹配`);
  if ((node === "bench_plan" || node === "optimize") && (command.match(/--stage(?:\s|=)/g)?.length ?? 0) !== 1) {
    throw new Error(`${node} 节点只能声明一次固定 stage`);
  }
  if (FORBIDDEN_NODE_CONTROL_ARGUMENTS.test(command)) {
    throw new Error(`${node} 节点不允许定义身份、路径、运行模式或上报地址等系统参数`);
  }
  return command;
}

export function readNodeCommandOption(command: string, name: "model" | "suite" | "judge"): string | undefined {
  const match = command.match(new RegExp(`(?:^|\\s)--${name}(?:=|\\s+)([^\\s]+)`));
  return match?.[1];
}

export function parseNodeCommandYamls(value: unknown, allowed: NodeCommandKey[]): Partial<Record<NodeCommandKey, string>> {
  if (value == null) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("nodeCommandYamls 必须是对象");
  const input = value as Record<string, unknown>;
  const unknown = Object.keys(input).find((key) => !allowed.includes(key as NodeCommandKey));
  if (unknown) throw new Error(`当前流程不支持节点 ${unknown}`);
  return Object.fromEntries(allowed.flatMap((node) => {
    const command = parseNodeCommandYaml(input[node], node);
    return command ? [[node, command]] : [];
  }));
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

export function withoutDiagnoseApiKey(command: string): string {
  const stripped = command.replace(
    /(^|\s)--api-key(?:=|\s+)(?:\{\{api_key\}\}|\*+)(?=\s|$)/gi,
    "$1",
  ).trim();
  if (/(?:^|\s)--api-key(?:=|\s|$)/i.test(stripped)) {
    throw new Error("BaaS Diagnose 命令不允许包含 --api-key");
  }
  return stripped;
}
