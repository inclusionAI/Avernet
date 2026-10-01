import type { ControllerAction } from "./types.js";
import type { ResolvedWorkflowFacade } from "./facades/registry.js";
import type { InjectLevel } from "./inject-level.js";
import path from "node:path";

const FACADE_DEFAULT_WORKFLOW_ACTIONS = new Set([
  "run",
  "help",
  "validate",
  "detail",
  "cutover-check",
  "reopen",
  "confirm",
  "revise",
  "reject",
  "submit",
]);

const HUMAN_INTERACTION_COMMANDS = new Set([
  "confirm",
  "revise",
  "reject",
  "submit",
]);

// Actions that route to the active waiting flow (no workflowId injection needed).
// When a facade receives /<facade> confirm|revise|reject, we pass the action
// through directly — the controller locates the waiting node by flowId.
const FACADE_HUMAN_GATE_ACTIONS = new Set([
  "confirm",
  "revise",
  "reject",
]);

function normalizeFacadeName(value: string | undefined): string {
  return value?.trim().replace(/^\/+/, "").toLowerCase() ?? "";
}

function parseParams(parts: string[]): Record<string, string> {
  const params: Record<string, string> = {};
  let i = 0;
  while (i < parts.length) {
    if (parts[i].startsWith("--")) {
      const key = parts[i].substring(2);
      const values: string[] = [];
      i++;
      while (i < parts.length && !parts[i].startsWith("--")) {
        values.push(parts[i]);
        i++;
      }
      params[key] = values.join(" ");
    } else {
      i++;
    }
  }
  return params;
}

type RunCommandArgs = {
  params: Record<string, string>;
  message?: string;
  files: string[];
  debug?: boolean;
  chatInjectLevel?: InjectLevel;
};

const VALID_INJECT_LEVELS = new Set<InjectLevel>(["perf", "simple", "full"]);

function parseRunCommandArgs(parts: string[]): RunCommandArgs {
  const params: Record<string, string> = {};
  const files: string[] = [];
  const messageParts: string[] = [];
  let chatInjectLevel: InjectLevel | undefined;
  let i = 0;

  const RUN_BOOLEAN_FLAGS = new Set(["debug"]);

  while (i < parts.length) {
    const part = parts[i];
    if (!part.startsWith("--")) {
      messageParts.push(part);
      i += 1;
      continue;
    }

    // --chat-inject-level <perf|simple|full> : per-run chatInject level override.
    // Accepts space form (`--chat-inject-level perf`) and `=` form, plus the
    // camelCase alias. Intercept BEFORE the generic `--key value` branch so it
    // is NOT swallowed into workflow input params.
    let levelConsumed = false;
    if (part === "--chat-inject-level" || part === "--chatInjectLevel") {
      const value = parts[i + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("用法: --chat-inject-level <perf|simple|full>");
      }
      if (!VALID_INJECT_LEVELS.has(value as InjectLevel)) {
        throw new Error(`--chat-inject-level 取值无效: ${value}（应为 perf|simple|full）`);
      }
      chatInjectLevel = value as InjectLevel;
      i += 2;
      levelConsumed = true;
    } else if (part.startsWith("--chat-inject-level=") || part.startsWith("--chatInjectLevel=")) {
      const value = part.slice(part.indexOf("=") + 1);
      if (!VALID_INJECT_LEVELS.has(value as InjectLevel)) {
        throw new Error(`--chat-inject-level 取值无效: ${value}（应为 perf|simple|full）`);
      }
      chatInjectLevel = value as InjectLevel;
      i += 1;
      levelConsumed = true;
    }
    if (levelConsumed) continue;

    // Boolean flags (no value needed)
    if (RUN_BOOLEAN_FLAGS.has(part.substring(2))) {
      params[part.substring(2)] = "true";
      i += 1;
      continue;
    }

    const value = parts[i + 1];
    if (!value || value.startsWith("--")) {
      throw new Error("用法: /workflow run <workflowId> [--key value ...] [--file <path>] [--debug] [message...]");
    }

    if (part === "--file") {
      files.push(value);
    } else {
      params[part.substring(2)] = value;
    }
    i += 2;
  }

  const debug = flagEnabled(params, "debug");
  delete params.debug;
  const message = messageParts.join(" ").trim();
  return {
    params,
    files,
    ...(debug ? { debug: true } : {}),
    ...(message ? { message } : {}),
    ...(chatInjectLevel ? { chatInjectLevel } : {}),
  };
}

export function tokenizeCommand(value: string): string[] {
  const parts: string[] = [];
  let current = "";
  let inQuote = false;
  let escaped = false;

  for (const char of value) {
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\" && inQuote) {
      escaped = true;
      continue;
    }
    if (inQuote) {
      if (char === "\"") {
        inQuote = false;
      } else {
        current += char;
      }
      continue;
    }
    // Only treat double-quote as a quoting character.
    // Single quotes are preserved as literal characters — they appear frequently
    // in business input (e.g. 'TR20260725...', '交易ID') and treating them as
    // quotes causes content from separate tokens to be incorrectly merged.
    if (char === "\"") {
      inQuote = true;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }

  if (inQuote) throw new Error("命令参数引号未闭合");
  if (escaped) current += "\\";
  if (current) parts.push(current);
  return parts;
}

function parsePositiveInt(value: string | undefined): number | undefined {
  if (value == null) return undefined;
  if (!/^\d+$/.test(value)) throw new Error("--limit 必须是正整数");
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) throw new Error("--limit 必须是正整数");
  return parsed;
}

function flagEnabled(params: Record<string, string>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(params, key);
}

const FLOW_FILTER_PARAMS = new Set(["workflowId", "identityKey", "status", "includeHidden", "global", "limit"]);
const FLOW_BUSINESS_FILTER_ERROR = "flows 不支持业务参数筛选，请使用 --identityKey <identity> 或 --workflowId <workflowId>";
const REPAIR_LEGACY_IDENTITY_USAGE = "用法: /workflow repair legacy-identity --workflowId <workflowId> [--flowId <flowId>] [--dryRun]";
const REPAIR_EXTERNAL_PACK_PIN_USAGE = "用法: /workflow repair external-pack-pin --workflowId <workflowId> [--flowId <flowId>] [--dryRun]";

function assertSupportedFlowFilters(params: Record<string, string>): void {
  const unknownParam = Object.keys(params).find((key) => !FLOW_FILTER_PARAMS.has(key));
  if (unknownParam) throw new Error(FLOW_BUSINESS_FILTER_ERROR);
}

type ApprovalCommandArgs = {
  nodeId?: string;
  flowId?: string;
  text: string;
};

function parseApprovalCommandArgs(parts: string[]): ApprovalCommandArgs {
  const textParts: string[] = [];
  let nodeId: string | undefined;
  let flowId: string | undefined;
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];
    if (part === "--node") {
      nodeId = parts[i + 1];
      i += 2;
      continue;
    }
    if (part === "--flowId") {
      flowId = parts[i + 1];
      i += 2;
      continue;
    }
    textParts.push(part);
    i++;
  }

  return {
    nodeId,
    flowId,
    text: textParts.join(" ").trim(),
  };
}

type SubmitCommandArgs = {
  nodeId: string;
  flowId?: string;
  resultJson?: string;
  text?: string;
};

const SUBMIT_USAGE = "用法: /workflow submit --node <nodeId> [--flowId <flowId>] [--result-json '<json>'] [文本...]";

function parseSubmitOptionValue(parts: string[], index: number): string {
  const value = parts[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(SUBMIT_USAGE);
  }
  return value;
}

function parseSubmitCommandArgs(parts: string[]): SubmitCommandArgs {
  let nodeId: string | undefined;
  let flowId: string | undefined;
  let resultJson: string | undefined;
  const textParts: string[] = [];
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];
    if (part === "--node") {
      nodeId = parseSubmitOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part === "--flowId") {
      flowId = parseSubmitOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part === "--result-json" || part === "--resultJson") {
      resultJson = parseSubmitOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part.startsWith("--")) throw new Error(`未知 submit 参数: ${part}`);
    textParts.push(part);
    i += 1;
  }

  const text = textParts.join(" ").trim();
  if (!nodeId) throw new Error(SUBMIT_USAGE);
  return {
    nodeId,
    ...(flowId ? { flowId } : {}),
    ...(resultJson ? { resultJson } : {}),
    ...(text ? { text } : {}),
  };
}

type RetryCommandArgs = {
  nodeId?: string;
  flowId?: string;
  reason?: string;
  useCurrentDef?: boolean;
  debug?: boolean;
  inputOverrides?: Record<string, string>;
};

function parseRetryCommandArgs(parts: string[]): RetryCommandArgs {
  // Dedicated loop (not parseParams): parseParams can't express boolean flags
  // (--use-current-def / --debug take no value) nor repeatable --set (a repeated
  // key would be overwritten). Mirrors parseRunCommandArgs' *_BOOLEAN_FLAGS idiom.
  const RETRY_BOOLEAN_FLAGS = new Set(["use-current-def", "debug"]);
  const RETRY_VALUE_FLAGS = new Set(["node", "flowId", "reason"]);
  const RESERVED_KEYS = new Set(["node", "flowId", "reason", "use-current-def", "debug", "set"]);

  const out: RetryCommandArgs = {};
  const inputOverrides: Record<string, string> = {};
  let i = 0;

  const USAGE = "用法: retry [--node <id>] [--flowId <id>] [--reason <text>] [--use-current-def] [--debug] [--set <key>=<value> ...]";

  while (i < parts.length) {
    const part = parts[i];
    if (!part.startsWith("--")) {
      // retry has no positional args; ignore stray tokens
      i += 1;
      continue;
    }
    const key = part.substring(2);

    if (RETRY_BOOLEAN_FLAGS.has(key)) {
      if (key === "use-current-def") out.useCurrentDef = true;
      else out.debug = true;
      i += 1;
      continue;
    }

    if (key === "set") {
      const value = parts[i + 1];
      if (!value || value.startsWith("--")) {
        throw new Error(USAGE);
      }
      const eq = value.indexOf("=");
      if (eq <= 0) {
        throw new Error(`--set 需要 <key>=<value> 形式,收到: "${value}"`);
      }
      const k = value.substring(0, eq);
      if (RESERVED_KEYS.has(k)) {
        throw new Error(`--set 的 key "${k}" 与 retry 保留参数同名,请换一个名字`);
      }
      inputOverrides[k] = value.substring(eq + 1);
      i += 2;
      continue;
    }

    if (RETRY_VALUE_FLAGS.has(key)) {
      const values: string[] = [];
      i += 1;
      while (i < parts.length && !parts[i].startsWith("--")) {
        values.push(parts[i]);
        i += 1;
      }
      if (values.length === 0) {
        throw new Error(`--${key} 需要一个值。${USAGE}`);
      }
      const joined = values.join(" ");
      if (key === "node") out.nodeId = joined;
      else if (key === "flowId") out.flowId = joined;
      else out.reason = joined;
      continue;
    }

    throw new Error(`未知参数: --${key}。${USAGE}`);
  }

  if (Object.keys(inputOverrides).length > 0) out.inputOverrides = inputOverrides;
  return out;
}

type DebugCommandArgs = {
  flowId?: string;
  full?: boolean;
};

function parseDebugCommandArgs(parts: string[]): DebugCommandArgs {
  let flowId: string | undefined;
  let full = false;

  for (const part of parts) {
    if (part === "--full") {
      full = true;
      continue;
    }
    if (part.startsWith("--")) {
      throw new Error("用法: /workflow debug [flowId] [--full]");
    }
    if (flowId) {
      throw new Error("用法: /workflow debug [flowId] [--full]");
    }
    flowId = part;
  }

  return {
    ...(flowId ? { flowId } : {}),
    ...(full ? { full } : {}),
  };
}

type SkipCommandArgs = {
  nodeId: string;
  flowId?: string;
  reason: string;
  resultJson?: string;
  runHooks: boolean;
};

function parseRequiredOptionValue(parts: string[], index: number): string {
  const value = parts[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`用法: /workflow skip --node <nodeId> <reason> [--flowId <flowId>] [--result-json '<json>'] [--no-hooks]`);
  return value;
}

function parseSkipCommandArgs(parts: string[]): SkipCommandArgs {
  let nodeId: string | undefined;
  let flowId: string | undefined;
  let resultJson: string | undefined;
  let runHooks = true;
  const reasonParts: string[] = [];
  let i = 0;

  while (i < parts.length) {
    const part = parts[i];
    if (part === "--node") {
      nodeId = parseRequiredOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part === "--flowId") {
      flowId = parseRequiredOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part === "--result-json") {
      resultJson = parseRequiredOptionValue(parts, i);
      i += 2;
      continue;
    }
    if (part === "--no-hooks") {
      runHooks = false;
      i += 1;
      continue;
    }
    if (part.startsWith("--")) throw new Error(`未知 skip 参数: ${part}`);
    reasonParts.push(part);
    i += 1;
  }

  const reason = reasonParts.join(" ").trim();
  if (!nodeId || !reason) {
    throw new Error("用法: /workflow skip --node <nodeId> <reason> [--flowId <flowId>] [--result-json '<json>'] [--no-hooks]");
  }

  return {
    nodeId,
    reason,
    runHooks,
    ...(flowId ? { flowId } : {}),
    ...(resultJson ? { resultJson } : {}),
  };
}

function facadeDisplayName(commandName: string | undefined, facade: ResolvedWorkflowFacade): string {
  const normalized = normalizeFacadeName(commandName);
  return `/${normalized || facade.command}`;
}

function rawWithoutFirstToken(raw: string, firstToken: string): string {
  return raw.trim().slice(firstToken.length).trimStart();
}

function normalizeRawCommandForFacade(raw: string, commandName: string | undefined, facade: ResolvedWorkflowFacade): string {
  const trimmed = raw.trim();
  if (!trimmed) return `help ${facade.defaultWorkflow}`;

  const parts = tokenizeCommand(trimmed);
  const cmd = parts[0]?.toLowerCase() ?? "help";

  // Human gate actions (confirm/revise/reject) — pass through directly.
  // These target the active waiting flow and don't need a workflowId injected.
  if (FACADE_HUMAN_GATE_ACTIONS.has(cmd)) {
    return trimmed;
  }

  if (!FACADE_DEFAULT_WORKFLOW_ACTIONS.has(cmd)) {
    const firstToken = parts[0] ?? cmd;
    if (!firstToken) {
      return ["run", facade.defaultWorkflow].join(" ");
    }
    // 不以 -- 开头、不是全 ASCII 字母数字 token → 视为消息保留而非 workflowId
    const maybeWorkflowId = /^[A-Za-z0-9_\-]+$/.test(firstToken);
    if (!maybeWorkflowId || parts.length === 1) {
      // 首 token 不像 workflowId → 整个 raw 作为消息，不丢弃首 token
      // （首 token 可能是用户自然语言输入的一部分，如 "帮我分析..." 或 "apikey:xxx"）
      return ["run", facade.defaultWorkflow, trimmed].filter(Boolean).join(" ");
    }
    // firstToken 像 workflowId 但不在动作列表中 → 可能是显式 workflowId + 消息
    const suffix = rawWithoutFirstToken(trimmed, firstToken);
    return ["run", facade.defaultWorkflow, suffix].filter(Boolean).join(" ");
  }

  const secondToken = parts[1];
  // A pure numeric token (e.g. "4000009") is a parameter, not a workflow ID.
  // Only treat it as an explicit workflow ID if it contains at least one letter.
  const looksLikeWorkflowId = secondToken && !secondToken.startsWith("--") && /[a-zA-Z]/.test(secondToken);
  if (looksLikeWorkflowId) {
    if (secondToken !== facade.defaultWorkflow) {
      throw new Error(
        `${facadeDisplayName(commandName, facade)} is bound to default workflow "${facade.defaultWorkflow}", but command targets "${secondToken}". Use /workflow ${trimmed} to run another workflow.`,
      );
    }
    return raw;
  }

  const suffix = rawWithoutFirstToken(trimmed, parts[0] ?? cmd);

  // 人工交互命令（confirm, revise, reject, submit）不需要在命令中注入 default workflowId
  if (HUMAN_INTERACTION_COMMANDS.has(cmd)) {
    return [cmd, suffix].filter(Boolean).join(" ");
  }

  return [cmd, facade.defaultWorkflow, suffix].filter(Boolean).join(" ");
}

/** Built-in command verbs that should NOT be treated as implicit workflow IDs. */
const BUILTIN_COMMAND_VERBS = new Set([
  "help", "run", "inspect", "state", "logs", "runs", "flows", "packs", "pack", "cutover-check",
  "detail", "confirm", "repair", "retry", "submit", "skip", "revise", "reject",
  "reopen", "resume", "debug", "export", "import", "validate", "test", "list",
  "schedule", "webhook",
  "deploy", "pull", "rollback", "deploys", "history", "status", "share", "unshare", "clawmind",
  "install-pack",
  "debug-segment",
]);

export function parseWorkflowCommandWithFacade(
  raw: string,
  options: { commandName?: string; skillName?: string; facade?: ResolvedWorkflowFacade } = {},
): ControllerAction {
  if (options.facade) {
    return parseCommand(normalizeRawCommandForFacade(raw, options.commandName ?? options.skillName, options.facade));
  }

  // No facade resolved — check if the first token looks like a workflow ID
  // rather than a built-in command verb. This produces a more informative
  // "Workflow 'X' 未找到" error instead of the misleading "未知命令: X".
  const trimmed = raw.trim().replace(/^\/+/, "");
  const firstToken = trimmed.split(/\s+/)[0]?.toLowerCase() ?? "";
  if (firstToken && !BUILTIN_COMMAND_VERBS.has(firstToken)) {
    // Treat as implicit "run <workflowId>" — handleRun will produce the
    // accurate "Workflow not found" error with available workflow list.
    return parseCommand(`run ${trimmed}`);
  }

  return parseCommand(raw);
}

export function parseCommand(raw: string): ControllerAction {
  const trimmed = raw.trim();
  if (!trimmed) return { action: "help" };

  const parts = tokenizeCommand(trimmed);
  const cmd = parts[0]?.toLowerCase();

  switch (cmd) {
    case "help":
      return { action: "help", ...(parts[1] ? { workflowId: parts[1] } : {}) };

    case "run": {
      if (!parts[1]) throw new Error("用法: /workflow run <workflowId> [--key value ...] [--file <path>] [--debug] [message...]");
      return {
        action: "run",
        workflowId: parts[1],
        ...parseRunCommandArgs(parts.slice(2)),
      } as ControllerAction;
    }

    case "inspect": {
      const inspectParams = parseParams(parts.slice(1));
      const positionalFlowId = parts[1]?.startsWith("--") ? undefined : parts[1];
      return {
        action: "inspect",
        flowId: inspectParams.flowId ?? positionalFlowId,
        ...(flagEnabled(inspectParams, "analyze") ? { analyze: true } : {}),
        ...(flagEnabled(inspectParams, "full") ? { full: true } : {}),
      };
    }

    case "state": {
      // Deprecated — maps to inspect for backward compatibility
      const params = parseParams(parts.slice(1));
      const positionalFlowId = parts[1]?.startsWith("--") ? undefined : parts[1];
      return { action: "inspect", flowId: params.flowId ?? positionalFlowId };
    }

    case "logs": {
      // Deprecated — maps to inspect for backward compatibility
      const params = parseParams(parts.slice(1));
      return {
        action: "inspect",
        flowId: params.flowId,
      };
    }

    case "runs":
    case "flows": {  // backward compat alias
      const params = parseParams(parts.slice(1));
      if (parts[1]?.toLowerCase() === "cleanup") {
        const cleanupParams = parseParams(parts.slice(2));
        assertSupportedFlowFilters(cleanupParams);
        if (!cleanupParams.identityKey || cleanupParams.status !== "failed") {
          throw new Error("用法: /workflow runs cleanup --identityKey <identity> --status failed");
        }
        return {
          action: "runsCleanup",
          identityKey: cleanupParams.identityKey,
          ...(cleanupParams.workflowId ? { workflowId: cleanupParams.workflowId } : {}),
          status: "failed",
        };
      }
      assertSupportedFlowFilters(params);
      return {
        action: "runs",
        limit: parsePositiveInt(params.limit),
        ...(flagEnabled(params, "includeHidden") ? { includeHidden: true } : {}),
        ...(flagEnabled(params, "global") ? { global: true } : {}),
        ...(params.identityKey ? { identityKey: params.identityKey } : {}),
        ...(params.workflowId ? { workflowId: params.workflowId } : {}),
        ...(params.status ? { status: params.status } : {}),
      };
    }

    case "packs":
      return { action: "packs" };

    case "pack": {
      const subcommand = parts[1]?.toLowerCase();
      const packId = parts[2];
      if ((subcommand !== "inspect" && subcommand !== "validate") || !packId) {
        throw new Error("用法: /workflow pack inspect <packId> 或 /workflow pack validate <packId>");
      }
      return subcommand === "inspect"
        ? { action: "packInspect", packId }
        : { action: "packValidate", packId };
    }

    case "cutover-check":
      if (!parts[1]) throw new Error("用法: /workflow cutover-check <workflowId>");
      return { action: "cutoverCheck", workflowId: parts[1] };

    case "detail": {
      if (!parts[1]) throw new Error("用法: /workflow detail <workflowId> [--source pack|db] [--debug]");
      const detailParams = parseParams(parts.slice(2));
      // --debug is alias for --source pack
      let detailSource: "pack" | "db" | undefined;
      if (detailParams.source === "pack" || detailParams.source === "db") {
        detailSource = detailParams.source;
      } else if (flagEnabled(detailParams, "debug")) {
        detailSource = "pack";
      }
      return {
        action: "detail",
        workflowId: parts[1],
        ...(detailSource ? { source: detailSource } : {}),
      };
    }

    case "confirm": {
      // Support "confirm choice: <value> [备注: <note>] [--flags]" syntax used by
      // workflow_choice tool and L1 hook dispatch. When "choice:" prefix is present,
      // preserve both the choice value (for parseHumanInput to match inputSchema.enum)
      // and the optional "备注:" suffix. Otherwise keep the full remaining text as note.
      const rest = parts.slice(1);

      // Extract 备注: value before parseParams, because parseParams greedily consumes
      // non-flag tokens into the preceding flag value (e.g. --flow-id flow-1 备注: xxx).
      let beiZhuValue: string | undefined;
      const beiZhuIndex = rest.indexOf("备注:");
      if (beiZhuIndex >= 0 && rest[beiZhuIndex + 1] && !rest[beiZhuIndex + 1].startsWith("--")) {
        beiZhuValue = rest[beiZhuIndex + 1];
      }
      const argsForParams = beiZhuValue !== undefined
        ? [...rest.slice(0, beiZhuIndex), ...rest.slice(beiZhuIndex + 2)]
        : rest;
      const params = parseParams(argsForParams);

      let note: string | undefined;
      if (rest[0] === "choice:") {
        const choiceIndex = rest.findIndex((p, i) => i > 0 && !p.startsWith("--") && p !== "备注:");
        const choiceValue = choiceIndex >= 0 ? rest[choiceIndex] : undefined;
        if (beiZhuValue) {
          note = choiceValue
            ? `${choiceValue} 备注: ${beiZhuValue}`
            : `备注: ${beiZhuValue}`;
        } else {
          note = choiceValue;
        }
      } else {
        // Remove --flags from note text for plain confirm
        const noteParts = rest.filter((p) => !p.startsWith("--"));
        note = noteParts.join(" ") || undefined;
      }
      return {
        action: "confirm",
        ...(note ? { note } : {}),
        ...(params.flowId ?? params["flow-id"] ? { flowId: params.flowId ?? params["flow-id"] } : {}),
      };
    }

    case "repair": {
      const repairTarget = parts[1]?.toLowerCase();
      const params = parseParams(parts.slice(2));
      if (repairTarget === "legacy-identity") {
        if (!params.workflowId) throw new Error(REPAIR_LEGACY_IDENTITY_USAGE);
        return {
          action: "repairLegacyIdentity",
          workflowId: params.workflowId,
          ...(params.flowId ? { flowId: params.flowId } : {}),
          ...(flagEnabled(params, "dryRun") ? { dryRun: true } : {}),
        };
      }
      if (repairTarget === "external-pack-pin") {
        if (!params.workflowId) throw new Error(REPAIR_EXTERNAL_PACK_PIN_USAGE);
        return {
          action: "repairExternalPackPin",
          workflowId: params.workflowId,
          ...(params.flowId ? { flowId: params.flowId } : {}),
          ...(flagEnabled(params, "dryRun") ? { dryRun: true } : {}),
        };
      }
      throw new Error(`${REPAIR_LEGACY_IDENTITY_USAGE}\n${REPAIR_EXTERNAL_PACK_PIN_USAGE}`);
    }

    case "retry":
      return { action: "retry", ...parseRetryCommandArgs(parts.slice(1)) };

    case "submit":
      return { action: "submit", ...parseSubmitCommandArgs(parts.slice(1)) };

    case "skip":
      return { action: "skip", ...parseSkipCommandArgs(parts.slice(1)) };

    case "revise": {
      const args = parseApprovalCommandArgs(parts.slice(1));
      if (!args.text) {
        throw new Error("用法: /workflow revise [--node <approvalNodeId>] <审批意见> [--flowId <flowId>]");
      }
      return {
        action: "revise",
        note: args.text,
        ...(args.nodeId ? { nodeId: args.nodeId } : {}),
        ...(args.flowId ? { flowId: args.flowId } : {}),
      };
    }

    case "reject": {
      const rejectRest = parts.slice(1);
      const rejectParams = parseParams(rejectRest);
      const rejectNoteParts = rejectRest.filter((p) => !p.startsWith("--"));
      return {
        action: "reject",
        note: rejectNoteParts.join(" ") || undefined,
        ...(rejectParams.flowId ?? rejectParams["flow-id"] ? { flowId: rejectParams.flowId ?? rejectParams["flow-id"] } : {}),
      };
    }

    case "reopen": {
      if (!parts[1]) throw new Error("用法: /workflow reopen <workflowId> [--key value ...] [--reason ...]");
      const params = parseParams(parts.slice(2));
      return { action: "reopen", workflowId: parts[1], params };
    }

    case "resume":
      if (!parts[1] || !parts[2]) throw new Error("用法: /workflow resume <flowId> <revision>");
      return { action: "resume", flowId: parts[1], revision: parseInt(parts[2], 10) };

    case "debug": {
      // Deprecated — maps to inspect for backward compatibility
      const debugArgs = parseDebugCommandArgs(parts.slice(1));
      return {
        action: "inspect",
        flowId: debugArgs.flowId,
        ...(debugArgs.full ? { full: true } : {}),
      };
    }

    case "export": {
      const params = parseParams(parts.slice(1));
      if (!params.flowId) throw new Error("用法: /workflow export --flowId <flowId>");
      return { action: "export", flowId: params.flowId };
    }

    case "import":
      if (!parts[1]) throw new Error("用法: /workflow import <exportToken>");
      return { action: "import", token: parts.slice(1).join(" ") };

    case "validate": {
      if (!parts[1]) throw new Error("用法: /workflow validate <workflowId> [--file <path>]");
      const vParams = parseParams(parts.slice(1));
      const file = vParams.file ? String(vParams.file) : undefined;
      const workflowId = parts[1].startsWith("--") ? "" : parts[1];
      let finalId = workflowId;
      if (file && !workflowId) {
        finalId = path.basename(file, file.endsWith(".yml") ? ".yml" : ".yaml");
      }
      if (!finalId && !file) throw new Error("用法: /workflow validate <workflowId> [--file <path>]");
      return {
        action: "validate",
        workflowId: finalId,
        ...(file ? { file } : {}),
      };
    }

    case "test": {
      if (!parts[1]) throw new Error("用法: /workflow test <workflowId> [--mock <file>] [--no-assert] [--json]");
      const testParams = parseParams(parts.slice(2));
      return {
        action: "test",
        workflowId: parts[1],
        dryRun: true,
        ...(testParams.mock ? { mockFile: testParams.mock } : {}),
        assertEnabled: !flagEnabled(testParams, "noAssert") && !flagEnabled(testParams, "no-assert"),
        json: flagEnabled(testParams, "json"),
      };
    }

    case "list": {
      const listParams = parseParams(parts.slice(1));
      return { action: "list", ...(listParams.filter ? { filter: listParams.filter } : {}) };
    }

    case "schedule": {
      const rawArgs = parts.slice(1).join(" ");
      return { action: "schedule", rawArgs };
    }

    case "webhook": {
      const rawArgs = parts.slice(1).join(" ");
      return { action: "webhook", rawArgs };
    }

    case "deploy": {
      if (!parts[1]) throw new Error("用法: /workflow deploy <workflowId> [--yes] [--file <path>] [--force] [--note <text>]");
      const deployParams = parseParams(parts.slice(2));
      const deployFile = deployParams.file as string | undefined;
      const deployNote = deployParams.note as string | undefined;
      return {
        action: "deploy",
        workflowId: parts[1],
        ...(flagEnabled(deployParams, "yes") ? { yes: true } : {}),
        ...(deployFile ? { file: deployFile } : {}),
        ...(flagEnabled(deployParams, "force") ? { force: true } : {}),
        ...(deployNote ? { note: deployNote } : {}),
      };
    }

    case "install-pack": {
      if (!parts[1]) throw new Error("用法: /workflow install-pack <packDir> [--only <wfId>] [--force] [--move]");
      const installParams = parseParams(parts.slice(2));
      const only = installParams.only as string | undefined;
      return {
        action: "install-pack",
        packDir: parts[1],
        ...(only ? { only } : {}),
        ...(flagEnabled(installParams, "force") ? { force: true } : {}),
        ...(flagEnabled(installParams, "move") ? { move: true } : {}),
      };
    }

    case "pull": {
      const pullWorkflowId = parts[1] && !parts[1].startsWith("--") ? parts[1] : undefined;
      return {
        action: "pull",
        ...(pullWorkflowId ? { workflowId: pullWorkflowId } : {}),
      };
    }

    case "rollback": {
      if (!parts[1]) throw new Error("用法: /workflow rollback <workflowId> [--version <v>] [--deploy-number <N>] [--pack] [--tag #N] [--note <text>]");
      const rbParams = parseParams(parts.slice(2));
      const rbNote = rbParams.note as string | undefined;
      return {
        action: "rollback",
        workflowId: parts[1],
        ...(rbParams.version ? { version: parseInt(rbParams.version, 10) } : {}),
        ...(rbParams["deploy-number"] || rbParams.deployNumber ? { deployNumber: parseInt(rbParams["deploy-number"] || rbParams.deployNumber, 10) } : {}),
        ...(flagEnabled(rbParams, "pack") ? { pack: true } : {}),
        ...(rbParams.tag ? { tag: rbParams.tag } : {}),
        ...(rbNote ? { note: rbNote } : {}),
      };
    }

    case "deploys":
    case "history": {  // backward compat alias
      if (!parts[1]) throw new Error("用法: /workflow deploys <workflowId> [--limit N] [--version N] [--deploy-number N]");
      const histParams = parseParams(parts.slice(2));
      return {
        action: "deploys",
        workflowId: parts[1],
        ...(histParams.limit ? { limit: parseInt(histParams.limit, 10) } : {}),
        ...(histParams.version ? { detailVersion: parseInt(histParams.version, 10) } : {}),
        ...(histParams.deployNumber || histParams["deploy-number"] ? { detailDeployNumber: parseInt(histParams.deployNumber || histParams["deploy-number"], 10) } : {}),
      };
    }

    case "debug-segment": {
      // Scalars come from the command string; the nested upstream context
      // (nodeOutput/workflowData/input) cannot be encoded in the command string,
      // so they are left empty here. dispatchWorkflowCommand merges them from
      // inlineDebugContext (sourced from workflow_engine_dispatch's sibling schema
      // fields) when action.action==="debug-segment" — see index.ts. The empty
      // nodeOutput placeholder keeps the returned action satisfying the
      // ControllerAction variant.
      if (!parts[1] || !parts[2]) {
        throw new Error("用法: /workflow debug-segment <workflowId> <fromNode> [--to <toNode>]");
      }
      const segParams = parseParams(parts.slice(3));
      const toNode = segParams.to as string | undefined;
      return {
        action: "debug-segment",
        workflowId: parts[1],
        fromNode: parts[2],
        nodeOutput: {},
        ...(toNode ? { toNode } : {}),
      };
    }

    case "status": {
      const statusWorkflowId = parts[1] && !parts[1].startsWith("--") ? parts[1] : undefined;
      const statusParams = parseParams(parts.slice(statusWorkflowId ? 2 : 1));
      return {
        action: "status",
        ...(statusWorkflowId ? { workflowId: statusWorkflowId } : {}),
        ...(flagEnabled(statusParams, "diff") ? { diff: true } : {}),
        ...(flagEnabled(statusParams, "git-diff") || flagEnabled(statusParams, "gitDiff") ? { gitDiff: true } : {}),
      };
    }

    case "share": {
      if (!parts[1]) throw new Error("用法: /workflow share <workflowId> --to <ownerId>/<botId>");
      const shareParams = parseParams(parts.slice(2));
      if (!shareParams.to) throw new Error("用法: /workflow share <workflowId> --to <ownerId>/<botId>");
      return {
        action: "share",
        workflowId: parts[1],
        to: shareParams.to,
      };
    }

    case "unshare": {
      if (!parts[1]) throw new Error("用法: /workflow unshare <workflowId> --from <ownerId>/<botId>");
      const unshareParams = parseParams(parts.slice(2));
      if (!unshareParams.from) throw new Error("用法: /workflow unshare <workflowId> --from <ownerId>/<botId>");
      return {
        action: "unshare",
        workflowId: parts[1],
        from: unshareParams.from,
      };
    }

    default:
      throw new Error(`未知命令: ${cmd}。可用: run, inspect, submit, state, logs, runs, packs, pack, cutover-check, repair, retry, skip, export, import, detail, confirm, revise, reject, reopen, resume, debug, validate, test, list, schedule, webhook, deploy, pull, rollback, deploys, status, share, unshare, help`);
  }
}

export const parseCommandForTest = parseCommand;
