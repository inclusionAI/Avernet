import { z } from "zod";
import type { ActionRegistry } from "../actions/types.js";
import type {
  Assertion,
  AssertionMatcher,
  BcsRouteSpec,
  CollaborationDeliverySpec,
  CollaborationOnFeedback,
  ApprovalDeliverySpec,
  ApprovalDeliveryMode,
  HumanCommandHints,
  HumanGateActions,
  HumanInputSchema,
  HookActionSpec,
  MockConfig,
  NodeRetryFailureReason,
  NodeAlertingSpec,
  NodeExecutor,
  NodeOnResult,
  NodeRetrySpec,
  NodeValidationFailureAction,
  NodeValidationSpec,
  OutputContractSchema,
  OutputContractSpec,
  TestCase,
  TriggerRule,
  WorkflowContextPolicy,
  WorkflowIdentitySpec,
  WorkflowInputSpec,
  WorkflowNode,
  WorkflowOutputsSpec,
  WorkflowPreflightActionSpec,
  WorkflowSpec,
  WorkflowNotifications,
  DingTalkUserTarget,
  DingTalkGroupTarget,
  DingTalkMessageConfig,
  HttpCallbackNotification,
  NotifyEvent,
  LoopGroupExecutor,
  SubworkflowExecutor,
  NodeTemplate,
  GoalLoopExecutor,
  AvailableAction,
} from "../types.js";
import { MAX_SUBWORKFLOW_DEPTH } from "../types.js";
import { isSaveAsCapableExecutor } from "../legacy-runtime.js";

export type ValidationIssue = { path: string; message: string; severity?: "error" | "warning" };

export type ValidationWarning = { path: string; message: string };

export function formatValidationIssues(issues: ValidationIssue[]): string {
  return issues.map((issue) => `${issue.path}: ${issue.message}`).join("\n");
}

export class WorkflowValidationError extends Error {
  constructor(public readonly issues: ValidationIssue[]) {
    super(formatValidationIssues(issues));
    this.name = "WorkflowValidationError";
  }
}

const objectSchema = z.record(z.string(), z.unknown());
const explicitTriggerRuleNodes = new WeakSet<WorkflowNode>();
const reservedLoopRuntimeNodeIdPattern = /__iter\d+__/;
const reservedLoopIterationVars = new Set([
  "params",
  "input",
  "workflowData",
  "nodeOutput",
  "actionOutputs",
  "flowHooks",
  "result",
  "skillRoot",
  "current",
  "businessStatus",
  "currentPhase",
  "loop",
  "templateAliases",
  "user",
  "workflow",
]);

function fail(path: string, message: string): never {
  throw new WorkflowValidationError([{ path, message }]);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function requireRecord(raw: unknown, ctx: string): Record<string, unknown> {
  const parsed = objectSchema.safeParse(raw);
  if (!parsed.success || !isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  return raw;
}

function requireString(obj: Record<string, unknown>, key: string, ctx: string): string {
  const val = obj[key];
  if (typeof val !== "string" || !val.trim()) {
    fail(ctx, `"${key}" must be a non-empty string`);
  }
  return val;
}

function optionalString(obj: Record<string, unknown>, key: string): string | undefined {
  const val = obj[key];
  if (val == null) return undefined;
  if (typeof val !== "string") return undefined;
  return val;
}

function optionalStringArray(obj: Record<string, unknown>, key: string, ctx: string): string[] | undefined {
  const val = obj[key];
  if (val == null) return undefined;
  if (!Array.isArray(val) || val.some((item) => typeof item !== "string" || !item.trim())) {
    fail(ctx, `"${key}" must be an array of non-empty strings`);
  }
  return val;
}

function optionalBoolean(obj: Record<string, unknown>, key: string): boolean | undefined {
  const val = obj[key];
  return typeof val === "boolean" ? val : undefined;
}

function optionalNumber(obj: Record<string, unknown>, key: string, ctx: string): number | undefined {
  const val = obj[key];
  if (val == null) return undefined;
  if (typeof val !== "number" || !Number.isFinite(val)) {
    fail(`${ctx}.${key}`, "must be a number");
  }
  return val;
}

function optionalRecord(
  obj: Record<string, unknown>,
  key: string,
  ctx: string,
): Record<string, unknown> | undefined {
  const val = obj[key];
  if (val == null) return undefined;
  if (!isPlainRecord(val)) {
    fail(ctx, `"${key}" must be an object`);
  }
  return val;
}

function normalizeCliArgs(
  raw: unknown,
  ctx: string,
): string[] | Record<string, string> | undefined {
  if (raw == null) return undefined;
  if (Array.isArray(raw)) {
    if (raw.some((item) => typeof item !== "string")) {
      fail(ctx, "array items must be strings");
    }
    return raw as string[];
  }
  if (isPlainRecord(raw)) {
    const result: Record<string, string> = {};
    for (const [key, val] of Object.entries(raw)) {
      if (typeof val !== "string") {
        fail(`${ctx}.${key}`, "must be a string");
      }
      result[key] = val;
    }
    return result;
  }
  fail(ctx, "must be an array of strings or a key-value object");
  return undefined;
}

const forbiddenWorkflowDataPathSegments = new Set(["__proto__", "prototype", "constructor"]);

export function validateWorkflowDataPath(path: string, ctx: string): void {
  if (!path.startsWith("workflowData.")) {
    fail(ctx, `must start with workflowData.: ${path}`);
  }
  const segments = path.substring("workflowData.".length).split(".");
  if (segments.length === 0 || segments.some((segment) => !segment.trim())) {
    fail(ctx, `must include a non-empty workflowData path: ${path}`);
  }
  for (const segment of segments) {
    if (forbiddenWorkflowDataPathSegments.has(segment)) {
      fail(ctx, `contains forbidden path segment: ${segment}`);
    }
  }
}

function normalizeSaveAs(
  raw: unknown,
  ctx: string,
  options: { validateTargets?: boolean } = {},
): Record<string, string> | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`${ctx}.saveAs`, "must be an object");
  }
  for (const [target, template] of Object.entries(raw)) {
    if (options.validateTargets) {
      validateWorkflowDataPath(target, `${ctx}.saveAs.${target}`);
    }
    if (typeof template !== "string") {
      fail(`${ctx}.saveAs`, `saveAs "${target}" must be a string`);
    }
  }
  return raw as Record<string, string>;
}

function normalizeHumanInputSchema(raw: unknown, ctx: string): HumanInputSchema | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  if (raw.type !== undefined && raw.type !== "object") {
    fail(`${ctx}.type`, 'must be "object"');
  }
  if (raw.required !== undefined && (!Array.isArray(raw.required) || raw.required.some((item) => typeof item !== "string" || !item.trim()))) {
    fail(`${ctx}.required`, "must be a string array");
  }
  const validateFieldSpec = (fieldName: string, fieldSpec: unknown, fieldCtx: string): void => {
    if (!isPlainRecord(fieldSpec)) {
      fail(fieldCtx, "must be an object");
    }
    if (fieldSpec.type !== undefined && fieldSpec.type !== "string" && fieldSpec.type !== "number" && fieldSpec.type !== "boolean") {
      fail(`${fieldCtx}.type`, "must be one of string, number, boolean");
    }
    if (fieldSpec.parse !== undefined) {
      if (!isPlainRecord(fieldSpec.parse)) {
        fail(`${fieldCtx}.parse`, "must be an object");
      }
      if (fieldSpec.parse.regex !== undefined && typeof fieldSpec.parse.regex !== "string") {
        fail(`${fieldCtx}.parse.regex`, "must be a string");
      }
    }
    if (fieldSpec.regex !== undefined && typeof fieldSpec.regex !== "string") {
      fail(`${fieldCtx}.regex`, "must be a string");
    }
    if (fieldSpec.parser !== undefined && typeof fieldSpec.parser !== "string" && !isPlainRecord(fieldSpec.parser)) {
      fail(`${fieldCtx}.parser`, "must be a string or object");
    }
    if (fieldSpec.pattern !== undefined && typeof fieldSpec.pattern !== "string") {
      fail(`${fieldCtx}.pattern`, "must be a string");
    }
    if (
      fieldSpec.enum !== undefined
      && (
        !Array.isArray(fieldSpec.enum)
        || fieldSpec.enum.some((item) => typeof item !== "string" && typeof item !== "number" && typeof item !== "boolean")
      )
    ) {
      fail(`${fieldCtx}.enum`, "must be an array of string, number, or boolean");
    }
    void fieldName;
  };
  if (raw.properties !== undefined) {
    if (!isPlainRecord(raw.properties)) {
      fail(`${ctx}.properties`, "must be an object");
    }
    for (const [fieldName, fieldSpec] of Object.entries(raw.properties)) {
      validateFieldSpec(fieldName, fieldSpec, `${ctx}.properties.${fieldName}`);
    }
  }
  if (raw.fields !== undefined) {
    if (!isPlainRecord(raw.fields)) {
      fail(`${ctx}.fields`, "must be an object");
    }
    for (const [fieldName, fieldSpec] of Object.entries(raw.fields)) {
      validateFieldSpec(fieldName, fieldSpec, `${ctx}.fields.${fieldName}`);
    }
  }
  return raw as HumanInputSchema;
}

function normalizeHumanGateActions(raw: unknown, ctx: string): HumanGateActions | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }

  const actions: HumanGateActions = {};

  if (raw.confirm !== undefined) {
    if (!isPlainRecord(raw.confirm)) {
      fail(`${ctx}.confirm`, "must be an object");
    }
    const next = optionalString(raw.confirm, "next");
    if (next !== undefined && next !== "succeed-current") {
      fail(`${ctx}.confirm.next`, 'must be "succeed-current"');
    }
    actions.confirm = {
      inputSchema: normalizeHumanInputSchema(raw.confirm.inputSchema, `${ctx}.confirm.inputSchema`),
      saveAs: normalizeSaveAs(raw.confirm.saveAs, `${ctx}.confirm`, { validateTargets: true }),
      ...(next ? { next: "succeed-current" } : {}),
    };
  }

  if (raw.revise !== undefined) {
    if (!isPlainRecord(raw.revise)) {
      fail(`${ctx}.revise`, "must be an object");
    }
    const next = requireString(raw.revise, "next", `${ctx}.revise`);
    if (next !== "rerun-target") {
      fail(`${ctx}.revise.next`, 'must be "rerun-target"');
    }
    const feedbackPath = requireString(raw.revise, "feedbackPath", `${ctx}.revise`);
    validateWorkflowDataPath(feedbackPath, `${ctx}.revise.feedbackPath`);
    const historyPath = optionalString(raw.revise, "historyPath");
    if (historyPath !== undefined) {
      validateWorkflowDataPath(historyPath, `${ctx}.revise.historyPath`);
    }
    const reset = optionalString(raw.revise, "reset");
    if (reset !== undefined && reset !== "target-and-descendants") {
      fail(`${ctx}.revise.reset`, 'must be "target-and-descendants"');
    }
    const feedbackMode = optionalString(raw.revise, "feedbackMode");
    if (feedbackMode !== undefined && feedbackMode !== "replace" && feedbackMode !== "append-line") {
      fail(`${ctx}.revise.feedbackMode`, 'must be "replace" or "append-line"');
    }
    actions.revise = {
      inputSchema: normalizeHumanInputSchema(raw.revise.inputSchema, `${ctx}.revise.inputSchema`),
      feedbackPath,
      feedbackTemplate: optionalString(raw.revise, "feedbackTemplate"),
      ...(feedbackMode ? { feedbackMode: feedbackMode as "replace" | "append-line" } : {}),
      ...(historyPath ? { historyPath } : {}),
      target: requireString(raw.revise, "target", `${ctx}.revise`),
      ...(reset ? { reset: "target-and-descendants" } : {}),
      next: "rerun-target",
    };
  }

  if (raw.reject !== undefined) {
    if (!isPlainRecord(raw.reject)) {
      fail(`${ctx}.reject`, "must be an object");
    }
    const next = optionalString(raw.reject, "next");
    if (next !== undefined && next !== "fail-flow" && next !== "block-flow") {
      fail(`${ctx}.reject.next`, 'must be "fail-flow" or "block-flow"');
    }
    actions.reject = {
      inputSchema: normalizeHumanInputSchema(raw.reject.inputSchema, `${ctx}.reject.inputSchema`),
      saveAs: normalizeSaveAs(raw.reject.saveAs, `${ctx}.reject`, { validateTargets: true }),
      ...(next ? { next: next as "fail-flow" | "block-flow" } : {}),
    };
  }

  return actions;
}

const humanCommandHintKeys = new Set(["confirm", "reject", "revise"]);

function normalizeHumanCommandHints(
  raw: unknown,
  actions: HumanGateActions | undefined,
  ctx: string,
): HumanCommandHints | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }

  const enabledActions = actions
    ? new Set(Object.keys(actions))
    : new Set(["confirm", "reject"]);
  const normalized: HumanCommandHints = {};

  for (const [action, hintRaw] of Object.entries(raw)) {
    if (!humanCommandHintKeys.has(action)) {
      fail(`${ctx}.${action}`, "unsupported command hint action");
    }
    if (!enabledActions.has(action)) {
      fail(`${ctx}.${action}`, `commandHints.${action} is not enabled by actions`);
    }
    if (!isPlainRecord(hintRaw)) {
      fail(`${ctx}.${action}`, "must be an object");
    }
    const label = requireString(hintRaw, "label", `${ctx}.${action}`);
    const args = optionalStringArray(hintRaw, "args", `${ctx}.${action}`);
    normalized[action as keyof HumanCommandHints] = args ? { label, args } : { label };
  }

  return normalized;
}

function normalizeCollaborationOnFeedback(raw: unknown, nodeId: string): CollaborationOnFeedback | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`node ${nodeId}.executor.onFeedback`, "must be an object");
  }
  const reset = optionalString(raw, "reset");
  if (reset !== undefined && reset !== "target-and-descendants") {
    fail(`node ${nodeId}.executor.onFeedback.reset`, 'must be "target-and-descendants"');
  }
  const normalizedReset = reset === "target-and-descendants" ? reset : undefined;
  return {
    target: requireString(raw, "target", `node ${nodeId}.executor.onFeedback`),
    feedbackPath: optionalString(raw, "feedbackPath"),
    historyPath: optionalString(raw, "historyPath"),
    reset: normalizedReset,
  };
}

export function normalizeActors(raw: unknown): undefined {
  if (raw == null) return undefined;
  fail("defaults.actors", "is no longer supported; use collaboration executor participant or route");
}

export function normalizeProgress(raw: unknown): WorkflowSpec["defaults"]["progress"] | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("defaults.progress", "must be an object");
  }

  if (typeof raw.enabled !== "boolean") {
    fail("defaults.progress.enabled", "must be a boolean");
  }
  if (typeof raw.sink !== "string" || !raw.sink.trim()) {
    fail("defaults.progress.sink", "must be a non-empty string");
  }

  return {
    enabled: raw.enabled,
    sink: raw.sink,
  };
}

export function normalizeUser(raw: unknown): WorkflowSpec["defaults"]["user"] | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("defaults.user", "must be an object");
  }

  const source = optionalString(raw, "source");
  if (source !== undefined && source !== "fixed") {
    fail("defaults.user.source", 'must be "fixed"');
  }

  return {
    ...(optionalString(raw, "id") ? { id: optionalString(raw, "id") } : {}),
    ...(optionalString(raw, "name") ? { name: optionalString(raw, "name") } : {}),
    ...(source ? { source: source as "fixed" } : {}),
  };
}

const bcsRouteSelectorTypesWithValue = new Set(["bot", "name", "role", "capability"]);

function normalizeBcsRouteSelector(raw: unknown, ctx: string): NonNullable<BcsRouteSpec["to"]>[number] {
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const type = requireString(raw, "type", ctx);
  if (bcsRouteSelectorTypesWithValue.has(type)) {
    return {
      type: type as "bot" | "name" | "role" | "capability",
      value: requireString(raw, "value", ctx),
    };
  }
  if (type === "participants") {
    const value = requireString(raw, "value", ctx);
    if (value !== "all" && value !== "others") {
      fail(`${ctx}.value`, 'must be "all" or "others"');
    }
    return { type, value };
  }
  if (type === "originator" || type === "driver") {
    if (raw.value !== undefined) {
      fail(`${ctx}.value`, "must be omitted");
    }
    return { type };
  }
  fail(`${ctx}.type`, "must be one of bot, name, role, capability, participants, originator, driver");
}

function normalizeBcsRoute(raw: unknown, ctx: string): BcsRouteSpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const provider = optionalString(raw, "provider");
  if (provider !== undefined && provider !== "bcs") {
    fail(`${ctx}.provider`, 'must be "bcs"');
  }
  const mode = optionalString(raw, "mode");
  if (mode !== undefined && mode !== "auto" && mode !== "tool" && mode !== "cli") {
    fail(`${ctx}.mode`, 'must be "auto", "tool", or "cli"');
  }
  let to: BcsRouteSpec["to"];
  if (raw.to !== undefined) {
    if (!Array.isArray(raw.to) || raw.to.length === 0) {
      fail(`${ctx}.to`, "must be a non-empty array");
    }
    to = raw.to.map((item, index) => normalizeBcsRouteSelector(item, `${ctx}.to[${index}]`));
  }
  return {
    ...(provider ? { provider: "bcs" as const } : {}),
    ...(mode ? { mode: mode as "auto" | "tool" | "cli" } : {}),
    ...(to ? { to } : {}),
    ...(optionalString(raw, "reason") ? { reason: optionalString(raw, "reason") } : {}),
  };
}

function optionalParticipantString(obj: Record<string, unknown>, key: "role" | "id" | "name", ctx: string): string | undefined {
  const val = obj[key];
  if (val == null) return undefined;
  if (typeof val !== "string" || !val.trim()) {
    fail(`${ctx}.${key}`, "must be a non-empty string");
  }
  return val;
}

function normalizeWorkflowParticipant(raw: unknown, ctx: string): { role?: string; id?: string; name?: string } | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const role = optionalParticipantString(raw, "role", ctx);
  const id = optionalParticipantString(raw, "id", ctx);
  const name = optionalParticipantString(raw, "name", ctx);
  if (!role && !id && !name) {
    fail(ctx, "must include at least one of role, id, or name");
  }
  return {
    ...(role ? { role } : {}),
    ...(id ? { id } : {}),
    ...(name ? { name } : {}),
  };
}

const collaborationDeliveryPrimaries = new Set(["subagent", "embedded-agent", "bcs-route", "bcs-cli", "card-dingtalk", "card-secoc", "card-web"]);

function normalizeCollaborationDeliveryMode(raw: unknown, ctx: string): NonNullable<CollaborationDeliverySpec["private"]> {
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const primary = requireString(raw, "primary", ctx);
  if (!collaborationDeliveryPrimaries.has(primary)) {
    fail(`${ctx}.primary`, "must be one of subagent, embedded-agent, bcs-route, bcs-cli, card-dingtalk, card-secoc, card-web");
  }
  const action = optionalString(raw, "action");
  if (primary === "bcs-cli" && !action) {
    fail(`${ctx}.action`, 'is required when primary is "bcs-cli"');
  }
  return {
    primary: primary as NonNullable<CollaborationDeliverySpec["private"]>["primary"],
    ...(action ? { action } : {}),
  };
}

function normalizeCollaborationDelivery(raw: unknown, nodeId: string): CollaborationDeliverySpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`node ${nodeId}.executor.delivery`, "must be an object");
  }
  return {
    private: raw.private !== undefined
      ? normalizeCollaborationDeliveryMode(raw.private, `node ${nodeId}.executor.delivery.private`)
      : undefined,
    collaboration: raw.collaboration !== undefined
      ? normalizeCollaborationDeliveryMode(raw.collaboration, `node ${nodeId}.executor.delivery.collaboration`)
      : undefined,
  };
}

function normalizeApprovalDelivery(raw: unknown, nodeId: string): ApprovalDeliverySpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`node ${nodeId}.executor.delivery`, "must be an object");
  }
  return {
    private: raw.private !== undefined
      ? normalizeCollaborationDeliveryMode(raw.private, `node ${nodeId}.executor.delivery.private`) as ApprovalDeliveryMode
      : undefined,
    dingtalkGroup: (raw as Record<string, unknown>).dingtalkGroup !== undefined
      ? normalizeCollaborationDeliveryMode((raw as Record<string, unknown>).dingtalkGroup, `node ${nodeId}.executor.delivery.dingtalkGroup`) as ApprovalDeliveryMode
      : undefined,
    collaboration: raw.collaboration !== undefined
      ? normalizeCollaborationDeliveryMode(raw.collaboration, `node ${nodeId}.executor.delivery.collaboration`) as ApprovalDeliveryMode
      : undefined,
  };
}

function normalizeApprovalOnRevise(raw: unknown, nodeId: string): ImportApprovalOnRevise | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`node ${nodeId}.executor.onRevise`, "must be an object");
    return undefined;
  }
  return {
    target: requireString(raw, "target", `node ${nodeId}.executor.onRevise`),
    feedbackPath: optionalString(raw, "feedbackPath"),
    historyPath: optionalString(raw, "historyPath"),
    ...(raw.reset === "target-and-descendants" ? { reset: "target-and-descendants" as const } : {}),
  };
}

type ImportApprovalOnRevise = {
  target: string;
  feedbackPath?: string;
  historyPath?: string;
  reset?: "target-and-descendants";
};

function normalizeApprovers(raw: unknown, ctx: string): ImportWorkflowApprover[] {
  if (raw == null) return [];
  if (!Array.isArray(raw)) {
    fail(ctx, "must be an array");
    return [];
  }
  return raw.map((item, index) => {
    if (!isPlainRecord(item)) {
      fail(`${ctx}[${index}]`, "must be an object");
      return { empId: "", name: "" };
    }
    return {
      empId: requireString(item, "empId", `${ctx}[${index}]`),
      name: requireString(item, "name", `${ctx}[${index}]`),
      ...(item.role !== undefined ? { role: String(item.role) } : {}),
    };
  });
}

type ImportWorkflowApprover = {
  empId: string;
  name: string;
  role?: string;
};

function normalizeCardFields(raw: unknown, ctx: string): ImportCardFieldDef[] {
  if (raw == null) return [];
  if (!Array.isArray(raw)) {
    fail(ctx, "must be an array");
    return [];
  }
  return raw.map((item, index) => {
    if (!isPlainRecord(item)) {
      fail(`${ctx}[${index}]`, "must be an object");
      return { label: "", value: "" };
    }
    return {
      label: requireString(item, "label", `${ctx}[${index}]`),
      value: requireString(item, "value", `${ctx}[${index}]`),
    };
  });
}

type ImportCardFieldDef = {
  label: string;
  value: string;
};

type ImportSectionFieldActionDef = {
  key: string;
  label: string;
  type?: "primary" | "default" | "danger";
  autoFill?: string;
};

type ImportSectionFieldDef = {
  id: string;
  label: string;
  expected?: string;
  expectedLabel?: string;
  actual?: string;
  actualLabel?: string;
  actions?: ImportSectionFieldActionDef[];
  customizable?: boolean;
  placeholder?: string;
};

type ImportCardSectionDef = {
  id: string;
  title: string;
  icon?: string;
  description?: string;
  style?: "default" | "warning" | "info" | "danger";
  fields: ImportSectionFieldDef[];
};

function normalizeCardSections(raw: unknown, ctx: string): ImportCardSectionDef[] {
  if (raw == null) return [];
  if (!Array.isArray(raw)) {
    fail(ctx, "must be an array");
    return [];
  }
  return raw.map((section, sIndex) => {
    if (!isPlainRecord(section)) {
      fail(`${ctx}[${sIndex}]`, "must be an object");
      return { id: "", title: "", fields: [] };
    }
    const fieldsRaw = section.fields;
    if (!Array.isArray(fieldsRaw)) {
      fail(`${ctx}[${sIndex}].fields`, "must be an array");
    }
    const fields = (fieldsRaw ?? []).map((field, fIndex) => {
      if (!isPlainRecord(field)) {
        fail(`${ctx}[${sIndex}].fields[${fIndex}]`, "must be an object");
        return { id: "", label: "" };
      }
      const actionsRaw = field.actions;
      let actions: ImportSectionFieldActionDef[] | undefined;
      if (actionsRaw !== undefined) {
        if (!Array.isArray(actionsRaw)) {
          fail(`${ctx}[${sIndex}].fields[${fIndex}].actions`, "must be an array");
        }
        actions = actionsRaw.map((action, aIndex) => {
          if (!isPlainRecord(action)) {
            fail(`${ctx}[${sIndex}].fields[${fIndex}].actions[${aIndex}]`, "must be an object");
            return { key: "", label: "" };
          }
          return {
            key: requireString(action, "key", `${ctx}[${sIndex}].fields[${fIndex}].actions[${aIndex}]`),
            label: requireString(action, "label", `${ctx}[${sIndex}].fields[${fIndex}].actions[${aIndex}]`),
            type: optionalString(action, "type") as "primary" | "default" | "danger" | undefined,
            autoFill: optionalString(action, "autoFill"),
          };
        });
      }
      return {
        id: requireString(field, "id", `${ctx}[${sIndex}].fields[${fIndex}]`),
        label: requireString(field, "label", `${ctx}[${sIndex}].fields[${fIndex}]`),
        expected: optionalString(field, "expected"),
        expectedLabel: optionalString(field, "expectedLabel"),
        actual: optionalString(field, "actual"),
        actualLabel: optionalString(field, "actualLabel"),
        actions,
        customizable: optionalBoolean(field, "customizable"),
        placeholder: optionalString(field, "placeholder"),
      };
    });
    return {
      id: requireString(section, "id", `${ctx}[${sIndex}]`),
      title: requireString(section, "title", `${ctx}[${sIndex}]`),
      icon: optionalString(section, "icon"),
      description: optionalString(section, "description"),
      style: optionalString(section, "style") as "default" | "warning" | "info" | "danger" | undefined,
      fields,
    };
  });
}

function normalizeWorkflowCollaboration(raw: unknown): WorkflowSpec["collaboration"] {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("collaboration", "must be an object");
  }
  if (raw.provider !== "bcs") {
    fail("collaboration.provider", 'must be "bcs"');
  }
  if (!isPlainRecord(raw.subject)) {
    fail("collaboration.subject", "must be an object");
  }
  const subject = {
    type: typeof raw.subject.type === "string" && raw.subject.type.trim()
      ? raw.subject.type
      : fail("collaboration.subject.type", "must be a non-empty string"),
    id: typeof raw.subject.id === "string" && raw.subject.id.trim()
      ? raw.subject.id
      : fail("collaboration.subject.id", "must be a non-empty string"),
    title: optionalString(raw.subject, "title"),
  };

  let group: NonNullable<WorkflowSpec["collaboration"]>["group"];
  if (raw.group !== undefined) {
    if (!isPlainRecord(raw.group)) {
      fail("collaboration.group", "must be an object");
    }
    const mode = optionalString(raw.group, "mode");
    if (mode !== undefined && mode !== "existing" && mode !== "create") {
      fail("collaboration.group.mode", 'must be "existing" or "create"');
    }
    group = {
      ...(mode ? { mode: mode as "existing" | "create" } : {}),
      ...(optionalString(raw.group, "id") ? { id: optionalString(raw.group, "id") } : {}),
    };
  }

  let routing: NonNullable<WorkflowSpec["collaboration"]>["routing"];
  if (raw.routing !== undefined) {
    if (!isPlainRecord(raw.routing)) {
      fail("collaboration.routing", "must be an object");
    }
    const defaultMode = optionalString(raw.routing, "defaultMode");
    if (defaultMode !== undefined && defaultMode !== "auto" && defaultMode !== "tool" && defaultMode !== "cli") {
      fail("collaboration.routing.defaultMode", 'must be "auto", "tool", or "cli"');
    }
    routing = {
      ...(defaultMode ? { defaultMode: defaultMode as "auto" | "tool" | "cli" } : {}),
      ...(optionalString(raw.routing, "defaultReason") ? { defaultReason: optionalString(raw.routing, "defaultReason") } : {}),
    };
  }

  return {
    provider: "bcs",
    subject,
    ...(group ? { group } : {}),
    ...(routing ? { routing } : {}),
  };
}

const contextHistoryModes = new Set(["structured", "isolated", "inherit", "tail"]);

function normalizeContextPolicy(raw: unknown, ctx: string): WorkflowContextPolicy | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }

  const policy: WorkflowContextPolicy = {};
  if (raw.history !== undefined) {
    if (typeof raw.history !== "string" || !contextHistoryModes.has(raw.history)) {
      fail(`${ctx}.history`, "must be one of structured, isolated, inherit, tail");
    }
    policy.history = raw.history as WorkflowContextPolicy["history"];
  }
  if (raw.includeSessionHistory !== undefined) {
    if (typeof raw.includeSessionHistory !== "boolean") {
      fail(`${ctx}.includeSessionHistory`, "must be a boolean");
    }
    policy.includeSessionHistory = raw.includeSessionHistory;
  }
  if (raw.tailMessages !== undefined) {
    const tailMessages = raw.tailMessages;
    if (typeof tailMessages !== "number" || !Number.isInteger(tailMessages) || tailMessages < 1) {
      fail(`${ctx}.tailMessages`, "must be an integer >= 1");
    }
    policy.tailMessages = tailMessages;
  }
  if (raw.excludeInjectMessages !== undefined) {
    if (typeof raw.excludeInjectMessages !== "boolean") {
      fail(`${ctx}.excludeInjectMessages`, "must be a boolean");
    }
    policy.excludeInjectMessages = raw.excludeInjectMessages;
  }
  if (raw.compression !== undefined) {
    policy.compression = normalizeCompressionConfig(raw.compression, `${ctx}.compression`);
  }
  return policy;
}

const VALID_COMPRESSION_STRATEGIES = new Set([
  "verbatim", "dedup", "fuzzy-dedup", "error-purge", "truncate", "priority-evict", "key-value-extract", "sentence-score", "llm-summarize",
]);

function normalizeCompressionConfig(raw: unknown, ctx: string): import("../context/types.js").ContextCompressionConfig | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
    return undefined;
  }

  const config: import("../context/types.js").ContextCompressionConfig = {};

  if (raw.budget !== undefined) {
    if (!isPlainRecord(raw.budget)) {
      fail(`${ctx}.budget`, "must be an object");
    } else {
      const budget: import("../context/types.js").TokenBudget = { maxTokens: 8000 };
      if (raw.budget.maxTokens !== undefined) {
        if (typeof raw.budget.maxTokens !== "number" || !Number.isInteger(raw.budget.maxTokens) || raw.budget.maxTokens < 100) {
          fail(`${ctx}.budget.maxTokens`, "must be an integer >= 100");
        } else {
          budget.maxTokens = raw.budget.maxTokens;
        }
      }
      if (raw.budget.warningThreshold !== undefined) {
        if (typeof raw.budget.warningThreshold !== "number" || raw.budget.warningThreshold <= 0 || raw.budget.warningThreshold > 1) {
          fail(`${ctx}.budget.warningThreshold`, "must be a number in (0, 1]");
        } else {
          budget.warningThreshold = raw.budget.warningThreshold;
        }
      }
      if (raw.budget.overflowStrategy !== undefined) {
        if (typeof raw.budget.overflowStrategy !== "string" || !VALID_COMPRESSION_STRATEGIES.has(raw.budget.overflowStrategy)) {
          fail(`${ctx}.budget.overflowStrategy`, `must be one of: ${[...VALID_COMPRESSION_STRATEGIES].join(", ")}`);
        } else {
          budget.overflowStrategy = raw.budget.overflowStrategy as import("../context/types.js").CompressionStrategy;
        }
      }
      config.budget = budget;
    }
  }

  if (raw.steps !== undefined) {
    if (!Array.isArray(raw.steps)) {
      fail(`${ctx}.steps`, "must be an array");
    } else {
      config.steps = raw.steps.map((step: unknown, i: number) => {
        if (!isPlainRecord(step)) {
          fail(`${ctx}.steps[${i}]`, "must be an object");
          return { strategy: "dedup" as const };
        }
        if (typeof step.strategy !== "string" || !VALID_COMPRESSION_STRATEGIES.has(step.strategy)) {
          fail(`${ctx}.steps[${i}].strategy`, `must be one of: ${[...VALID_COMPRESSION_STRATEGIES].join(", ")}`);
          return { strategy: "dedup" as const };
        }
        return {
          strategy: step.strategy as import("../context/types.js").CompressionStrategy,
          ...(isPlainRecord(step.params) ? { params: step.params as Record<string, unknown> } : {}),
        };
      });
    }
  }

  if (raw.logStats !== undefined) {
    if (typeof raw.logStats !== "boolean") {
      fail(`${ctx}.logStats`, "must be a boolean");
    } else {
      config.logStats = raw.logStats;
    }
  }

  return config;
}

function normalizeContextPolicyDefaults(raw: unknown): WorkflowSpec["defaults"]["contextPolicy"] | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("defaults.contextPolicy", "must be an object");
  }
  return {
    embeddedAgent: normalizeContextPolicy(raw.embeddedAgent, "defaults.contextPolicy.embeddedAgent"),
    subagent: normalizeContextPolicy(raw.subagent, "defaults.contextPolicy.subagent"),
  };
}

function normalizeExecutor(raw: unknown, nodeId: string): NodeExecutor {
  const normalized = normalizeExecutorByType(raw, nodeId);
  // Executor-level passthrough: keep unknown executor fields (e.g. `skipWhen`,
  // which the engine reads at runtime via readExecutorSkipWhen) instead of
  // silently dropping them. Aligns with ClawWeb's `{ ...node.executor }`
  // whole-object passthrough so deploy 写盘保真,不再"每次漏一个字段"。
  if (isPlainRecord(raw)) {
    for (const key of Object.keys(raw)) {
      if (!(key in normalized)) {
        (normalized as Record<string, unknown>)[key] = raw[key];
      }
    }
  }
  return normalized;
}

function normalizeExecutorByType(raw: unknown, nodeId: string): NodeExecutor {
  const obj = requireRecord(raw, `node ${nodeId} executor`);
  const type = requireString(obj, "type", `node ${nodeId} executor`);

  switch (type) {
    case "embedded-agent": {
      const contextPolicy = normalizeContextPolicy(obj.contextPolicy, `node ${nodeId}.executor.contextPolicy`);
      return {
        type: "embedded-agent",
        skillName: optionalString(obj, "skillName"),
        outputMode: (optionalString(obj, "outputMode") as "text" | "json") ?? "json",
        prompt: requireString(obj, "prompt", `node ${nodeId}`),
        timeoutSeconds: typeof obj.timeoutSeconds === "number" ? obj.timeoutSeconds : undefined,
        ...(contextPolicy ? { contextPolicy } : {}),
      };
    }

    case "subagent": {
      const contextPolicy = normalizeContextPolicy(obj.contextPolicy, `node ${nodeId}.executor.contextPolicy`);
      return {
        type: "subagent",
        skillName: requireString(obj, "skillName", `node ${nodeId}`),
        prompt: requireString(obj, "prompt", `node ${nodeId}`),
        timeoutSeconds: typeof obj.timeoutSeconds === "number" ? obj.timeoutSeconds : undefined,
        ...(contextPolicy ? { contextPolicy } : {}),
      };
    }

    case "collaboration": {
      const contextPolicy = normalizeContextPolicy(obj.contextPolicy, `node ${nodeId}.executor.contextPolicy`);
      const timeoutSeconds = optionalNumber(obj, "timeoutSeconds", `node ${nodeId}.executor`);
      const route = normalizeBcsRoute(obj.route, `node ${nodeId}.executor.route`);
      const participant = normalizeWorkflowParticipant(obj.participant, `node ${nodeId}.executor.participant`);
      const onFeedback = normalizeCollaborationOnFeedback(obj.onFeedback, nodeId);
      const delivery = normalizeCollaborationDelivery(obj.delivery, nodeId);
      return {
        type: "collaboration",
        taskKind: optionalString(obj, "taskKind"),
        skillName: optionalString(obj, "skillName"),
        routeDisplayName: optionalString(obj, "routeDisplayName"),
        ...(route ? { route } : {}),
        ...(participant ? { participant } : {}),
        message: requireString(obj, "message", `node ${nodeId}`),
        timeoutSeconds,
        ...(onFeedback ? { onFeedback } : {}),
        ...(delivery ? { delivery } : {}),
        ...(contextPolicy ? { contextPolicy } : {}),
      };
    }

    case "approval": {
      const contextPolicy = normalizeContextPolicy(obj.contextPolicy, `node ${nodeId}.executor.contextPolicy`);
      const timeoutSeconds = optionalNumber(obj, "timeoutSeconds", `node ${nodeId}.executor`);
      const route = normalizeBcsRoute(obj.route, `node ${nodeId}.executor.route`);
      const delivery = normalizeApprovalDelivery(obj.delivery, nodeId);
      const onRevise = normalizeApprovalOnRevise(obj.onRevise, nodeId);
      const approvers = normalizeApprovers(obj.approvers, `node ${nodeId}.executor.approvers`);
      const cardFields = normalizeCardFields(obj.cardFields, `node ${nodeId}.executor.cardFields`);
      const cardSections = normalizeCardSections(obj.cardSections, `node ${nodeId}.executor.cardSections`);
      const approvalPolicy = optionalString(obj, "approvalPolicy");
      if (approvalPolicy !== undefined && !["any", "all", "majority"].includes(approvalPolicy)) {
        fail(`node ${nodeId}.executor.approvalPolicy`, "must be one of any, all, majority");
      }
      return {
        type: "approval" as const,
        skillName: requireString(obj, "skillName", `node ${nodeId}`),
        approvalType: optionalString(obj, "approvalType"),
        routeDisplayName: optionalString(obj, "routeDisplayName"),
        ...(route ? { route } : {}),
        reviewerRef: optionalString(obj, "reviewerRef"),
        message: requireString(obj, "message", `node ${nodeId}`),
        timeoutSeconds,
        ...(onRevise ? { onRevise } : {}),
        ...(delivery ? { delivery } : {}),
        ...(contextPolicy ? { contextPolicy } : {}),
        cardId: optionalString(obj, "cardId"),
        ...(approvers.length > 0 ? { approvers } : {}),
        ...(cardFields.length > 0 ? { cardFields } : {}),
        ...(cardSections.length > 0 ? { cardSections } : {}),
        ...(approvalPolicy ? { approvalPolicy: approvalPolicy as "any" | "all" | "majority" } : {}),
        cardTitle: optionalString(obj, "cardTitle"),
        statusLabel: optionalString(obj, "statusLabel"),
        actionLabel: optionalString(obj, "actionLabel"),
        workflowUrl: optionalString(obj, "workflowUrl"),
        saveAs: normalizeSaveAs(obj.saveAs, `node ${nodeId}.executor`, { validateTargets: true }),
      };
    }

    case "bcs-route":
      return {
        type: "bcs-route",
        target: requireString(obj, "target", `node ${nodeId}`),
        message: requireString(obj, "message", `node ${nodeId}`),
        timeout: typeof obj.timeout === "number" ? obj.timeout : undefined,
      };

    case "cli-script": {
      const normalizedArgs = normalizeCliArgs(obj.args, `node ${nodeId}.executor.args`);
      return {
        type: "cli-script",
        command: requireString(obj, "command", `node ${nodeId}`),
        args: normalizedArgs,
        env: obj.env as Record<string, string> | undefined,
        outputMode: (optionalString(obj, "outputMode") as "text" | "json") ?? "json",
        timeoutMs: typeof obj.timeoutMs === "number" ? obj.timeoutMs : undefined,
        timeoutSeconds: typeof obj.timeoutSeconds === "number" ? obj.timeoutSeconds : undefined,
      };
    }

    case "mcp-call":
      return {
        type: "mcp-call",
        server: requireString(obj, "server", `node ${nodeId}`),
        tool: requireString(obj, "tool", `node ${nodeId}`),
        args: (obj.args as Record<string, string>) ?? {},
        outputMode: (optionalString(obj, "outputMode") as "text" | "json") ?? "json",
        timeoutMs: typeof obj.timeoutMs === "number" ? obj.timeoutMs : undefined,
        timeoutSeconds: typeof obj.timeoutSeconds === "number" ? obj.timeoutSeconds : undefined,
      };

    case "baas-call":
      {
        const mode = (optionalString(obj, "mode") as "run" | "message") ?? "run";
        const botId = optionalString(obj, "botId");
        if (mode === "message" && !botId) {
          fail(`node ${nodeId}.executor.botId`, "required when mode is 'message'");
        }
        return {
          type: "baas-call",
          mode,
          botId: botId ?? undefined,
          message: requireString(obj, "message", `node ${nodeId}`),
          apiKeyRef: optionalString(obj, "apiKeyRef") ?? "BAAS_API_KEY",
          baseUrl: optionalString(obj, "baseUrl") ?? "https://secbaas-prod.alipay.com",
          iamToken: optionalString(obj, "iamToken") ?? undefined,
          timeoutMs: typeof obj.timeoutMs === "number" ? obj.timeoutMs : 120_000,
          timeoutSeconds: typeof obj.timeoutSeconds === "number" ? obj.timeoutSeconds : undefined,
          pollIntervalMs: typeof obj.pollIntervalMs === "number" ? obj.pollIntervalMs : 3_000,
          outputMode: (optionalString(obj, "outputMode") as "text" | "json") ?? "json",
        };
      }

    case "action":
      return {
        type: "action",
        action: requireString(obj, "action", `node ${nodeId}.executor`),
        args: optionalRecord(obj, "args", `node ${nodeId}.executor`),
      };

    case "human":
      {
        const actions = normalizeHumanGateActions(obj.actions, `node ${nodeId}.executor.actions`);
        const commandHints = normalizeHumanCommandHints(
          obj.commandHints,
          actions,
          `node ${nodeId}.executor.commandHints`,
        );
        return {
          type: "human",
          prompt: requireString(obj, "prompt", `node ${nodeId}`),
          waitKind: optionalString(obj, "waitKind"),
          inputSchema: normalizeHumanInputSchema(obj.inputSchema, `node ${nodeId}.executor.inputSchema`),
          saveAs: normalizeSaveAs(obj.saveAs, `node ${nodeId}.executor`, { validateTargets: true }),
          actions,
          commandHints,
        };
      }

    case "async-callback":
      {
        const timeout = optionalString(obj, "timeout");
        const callbackBaseUrl = optionalString(obj, "callbackBaseUrl");
        let auth: import("../types.js").AsyncCallbackAuthConfig | undefined;
        if (obj.auth !== undefined) {
          const authObj = obj.auth as Record<string, unknown> | undefined;
          if (authObj && typeof authObj === "object") {
            const mode = authObj.mode as string;
            if (mode !== "hmac" && mode !== "x-one-id") {
              fail(`node ${nodeId}.executor.auth.mode`, 'must be "hmac" or "x-one-id"');
            }
            auth = {
              mode: mode as "hmac" | "x-one-id",
              ...(authObj.secret ? { secret: String(authObj.secret) } : {}),
              ...(authObj.allowedUsers ? { allowedUsers: authObj.allowedUsers as string[] } : {}),
            };
          }
        }
        return {
          type: "async-callback",
          ...(timeout ? { timeout } : {}),
          ...(callbackBaseUrl ? { callbackBaseUrl } : {}),
          ...(auth ? { auth } : {}),
          saveAs: normalizeSaveAs(obj.saveAs, `node ${nodeId}.executor`, { validateTargets: true }),
        };
      }

    case "subworkflow": {
      const workflowId = requireString(obj, "workflowId", `node ${nodeId}`);
      const packId = optionalString(obj, "packId");
      let params: Record<string, string> | undefined;
      if (obj.params !== undefined) {
        if (!isPlainRecord(obj.params)) {
          fail(`node ${nodeId}.executor.params`, "must be an object");
        }
        for (const [key, value] of Object.entries(obj.params)) {
          if (typeof value !== "string") {
            fail(`node ${nodeId}.executor.params.${key}`, "must be a string");
          }
        }
        params = obj.params as Record<string, string>;
      }
      const onFailure = optionalString(obj, "onFailure");
      if (onFailure !== undefined && onFailure !== "fail" && onFailure !== "retry" && onFailure !== "skip") {
        fail(`node ${nodeId}.executor.onFailure`, 'must be "fail", "retry", or "skip"');
      }
      return {
        type: "subworkflow",
        workflowId,
        ...(packId ? { packId } : {}),
        ...(params ? { params } : {}),
        ...(onFailure ? { onFailure: onFailure as "fail" | "retry" | "skip" } : {}),
      };
    }

    case "done":
      return { type: "done" };

    case "dynamic-template": {
      const template = requireString(obj, "template", `node ${nodeId}`);
      const forEach = requireString(obj, "forEach", `node ${nodeId}`);
      const iterationVar = optionalString(obj, "iterationVar") ?? "item";
      const maxItems = optionalNumber(obj, "maxItems", `node ${nodeId}`);
      if (maxItems !== undefined && (!Number.isInteger(maxItems) || maxItems < 1)) {
        fail(`node ${nodeId}.executor.maxItems`, "must be an integer >= 1");
      }
      return {
        type: "dynamic-template",
        template,
        forEach,
        iterationVar,
        ...(maxItems ? { maxItems } : {}),
      };
    }

    case "goal-evaluator": {
      const goal = requireString(obj, "goal", `node ${nodeId}`);
      const evaluatorObj = isPlainRecord(obj.evaluator) ? obj.evaluator as Record<string, unknown> : undefined;
      if (!evaluatorObj) {
        fail(`node ${nodeId}.executor.evaluator`, "must be an object");
      }
      let evaluator: NonNullable<import("../types.js").GoalEvaluatorExecutor["evaluator"]>;
      if (evaluatorObj) {
        evaluator = {
          prompt: requireString(evaluatorObj, "prompt", `node ${nodeId}.executor.evaluator`),
          ...(typeof evaluatorObj.model === "string" ? { model: evaluatorObj.model } : {}),
          ...(typeof evaluatorObj.temperature === "number" ? { temperature: evaluatorObj.temperature } : {}),
          ...(typeof evaluatorObj.timeoutMs === "number" ? { timeoutMs: evaluatorObj.timeoutMs } : {}),
        };
      } else {
        evaluator = { prompt: "" };
      }
      const maxAttempts = optionalNumber(obj, "maxAttempts", `node ${nodeId}`);
      if (maxAttempts !== undefined && (!Number.isInteger(maxAttempts) || maxAttempts < 1)) {
        fail(`node ${nodeId}.executor.maxAttempts`, "must be an integer >= 1");
      }
      let onNotMet: import("../types.js").GoalEvaluatorExecutor["onNotMet"];
      if (isPlainRecord(obj.onNotMet)) {
        const action = requireString(obj.onNotMet as Record<string, unknown>, "action", `node ${nodeId}.executor.onNotMet`);
        if (action !== "fail" && action !== "complete") {
          fail(`node ${nodeId}.executor.onNotMet.action`, 'must be "fail" or "complete"');
        }
        onNotMet = {
          action: action as "fail" | "complete",
          ...(typeof (obj.onNotMet as Record<string, unknown>).message === "string" ? { message: (obj.onNotMet as Record<string, unknown>).message as string } : {}),
        };
      }
      return {
        type: "goal-evaluator",
        goal,
        evaluator,
        ...(maxAttempts ? { maxAttempts } : {}),
        ...(onNotMet ? { onNotMet } : {}),
      };
    }

    case "llm-orchestrator": {
      const goal = requireString(obj, "goal", `node ${nodeId}`);
      if (!Array.isArray(obj.availableActions) || obj.availableActions.length === 0) {
        fail(`node ${nodeId}.executor.availableActions`, "must be a non-empty array");
      }
      const availableActions: import("../types.js").AvailableAction[] = (obj.availableActions as Record<string, unknown>[]).map((rawAction, idx) => {
        const name = requireString(rawAction, "name", `node ${nodeId}.executor.availableActions[${idx}]`);
        const actionType = requireString(rawAction, "type", `node ${nodeId}.executor.availableActions[${idx}]`);
        return {
          name,
          type: actionType as import("../types.js").NodeExecutor["type"],
          ...(isPlainRecord(rawAction.params) ? { params: rawAction.params as Record<string, unknown> } : {}),
          ...(typeof rawAction.description === "string" ? { description: rawAction.description } : {}),
        };
      });
      const maxIterations = optionalNumber(obj, "maxIterations", `node ${nodeId}`);
      if (maxIterations !== undefined && (!Number.isInteger(maxIterations) || maxIterations < 1)) {
        fail(`node ${nodeId}.executor.maxIterations`, "must be an integer >= 1");
      }
      let budget: import("../types.js").FlowBudget | undefined;
      if (isPlainRecord(obj.budget)) {
        const budgetObj = obj.budget as Record<string, unknown>;
        budget = {};
        if (budgetObj.maxTokens !== undefined) {
          if (typeof budgetObj.maxTokens !== "number" || !Number.isInteger(budgetObj.maxTokens) || budgetObj.maxTokens < 100) {
            fail(`node ${nodeId}.executor.budget.maxTokens`, "must be an integer >= 100");
          }
          budget.maxTokens = budgetObj.maxTokens as number;
        }
        if (budgetObj.maxIterations !== undefined) {
          if (typeof budgetObj.maxIterations !== "number" || !Number.isInteger(budgetObj.maxIterations) || budgetObj.maxIterations < 1) {
            fail(`node ${nodeId}.executor.budget.maxIterations`, "must be an integer >= 1");
          }
          budget.maxIterations = budgetObj.maxIterations as number;
        }
        if (typeof budgetObj.strategy === "string") {
          budget.strategy = budgetObj.strategy as import("../types.js").FlowBudget["strategy"];
        }
      }
      let verification: import("../types.js").LlmOrchestratorExecutor["verification"];
      if (isPlainRecord(obj.verification)) {
        const verObj = obj.verification as Record<string, unknown>;
        verification = {
          prompt: requireString(verObj, "prompt", `node ${nodeId}.executor.verification`),
          ...(typeof verObj.model === "string" ? { model: verObj.model } : {}),
          ...(typeof verObj.minVotes === "number" ? { minVotes: verObj.minVotes } : {}),
          ...(typeof verObj.totalVoters === "number" ? { totalVoters: verObj.totalVoters } : {}),
        };
      }
      return {
        type: "llm-orchestrator",
        goal,
        availableActions,
        ...(maxIterations ? { maxIterations } : {}),
        ...(budget ? { budget } : {}),
        ...(verification ? { verification } : {}),
      };
    }

    case "goal-loop": {
      return normalizeGoalLoopExecutor(obj, nodeId);
    }

    default:
      fail(`node ${nodeId}`, `unknown executor type "${type}"`);
  }
}

function normalizeGoalLoopExecutor(obj: Record<string, unknown>, nodeId: string): GoalLoopExecutor {
  const goal = requireString(obj, "goal", `node ${nodeId}.executor.goal`);
  if (!goal.trim()) {
    fail(`node ${nodeId}.executor.goal`, "must not be empty");
  }

  // initialPlan (default: synthesize)
  let initialPlan: GoalLoopExecutor["initialPlan"];
  const ipRaw = obj.initialPlan;
  if (ipRaw === undefined || ipRaw === null) {
    initialPlan = { type: "synthesize" };
  } else if (isPlainRecord(ipRaw)) {
    const ipType = optionalString(ipRaw, "type") ?? "synthesize";
    if (ipType !== "synthesize" && ipType !== "spec") {
      fail(`node ${nodeId}.executor.initialPlan.type`, `must be "synthesize" or "spec"`);
    }
    initialPlan = { type: ipType as "synthesize" | "spec" };
    if (ipType === "spec") {
      if (!isPlainRecord(ipRaw.spec)) {
        fail(`node ${nodeId}.executor.initialPlan.spec`, "required when type is 'spec'");
      }
      initialPlan.spec = normalizeWorkflowSpec(ipRaw.spec);
    }
    const hintsRaw = (ipRaw as Record<string, unknown>).hints;
    if (Array.isArray(hintsRaw)) {
      initialPlan.hints = hintsRaw.filter((h): h is string => typeof h === "string");
    }
  } else {
    fail(`node ${nodeId}.executor.initialPlan`, "must be an object");
  }

  // evaluation (required)
  const evalRaw = obj.evaluation;
  if (!isPlainRecord(evalRaw)) {
    fail(`node ${nodeId}.executor.evaluation`, "is required");
  }
  const criteria = (evalRaw as Record<string, unknown>).criteria;
  if (!Array.isArray(criteria) || criteria.length === 0) {
    fail(`node ${nodeId}.executor.evaluation.criteria`, "must be a non-empty array");
  }
  const evaluation: GoalLoopExecutor["evaluation"] = {
    criteria: criteria.filter((c): c is string => typeof c === "string" && c.trim() !== ""),
  };
  if (evaluation.criteria.length === 0) {
    fail(`node ${nodeId}.executor.evaluation.criteria`, "must contain at least one non-empty string");
  }
  const evalModel = optionalString(evalRaw as Record<string, unknown>, "model");
  if (evalModel) evaluation.model = evalModel;
  const evalTemp = optionalNumber(evalRaw as Record<string, unknown>, "temperature", `node ${nodeId}.executor.evaluation`);
  if (evalTemp !== undefined) evaluation.temperature = evalTemp;

  // repair (default: adaptive, maxReplans=3, maxLocalRepairs=3)
  let repair: GoalLoopExecutor["repair"] | undefined;
  const repairRaw = obj.repair;
  if (isPlainRecord(repairRaw)) {
    const mode = optionalString(repairRaw as Record<string, unknown>, "mode") ?? "adaptive";
    if (!["adaptive", "local-only", "replan-only"].includes(mode)) {
      fail(`node ${nodeId}.executor.repair.mode`, `must be "adaptive", "local-only", or "replan-only"`);
    }
    repair = { mode: mode as "adaptive" | "local-only" | "replan-only" };
    const maxReplans = optionalNumber(repairRaw as Record<string, unknown>, "maxReplans", `node ${nodeId}.executor.repair`);
    if (maxReplans !== undefined) repair.maxReplans = maxReplans;
    const maxLocalRepairs = optionalNumber(repairRaw as Record<string, unknown>, "maxLocalRepairs", `node ${nodeId}.executor.repair`);
    if (maxLocalRepairs !== undefined) repair.maxLocalRepairs = maxLocalRepairs;
    const allowDyn = (repairRaw as Record<string, unknown>).allowDynamicActions;
    if (typeof allowDyn === "boolean") repair.allowDynamicActions = allowDyn;
    // repairActions
    const raRaw = (repairRaw as Record<string, unknown>).repairActions;
    if (Array.isArray(raRaw)) {
      repair.repairActions = raRaw.filter(isPlainRecord).map((ra, i) => {
        const name = requireString(ra as Record<string, unknown>, "name", `node ${nodeId}.executor.repair.repairActions[${i}].name`);
        const raType = requireString(ra as Record<string, unknown>, "type", `node ${nodeId}.executor.repair.repairActions[${i}].type`);
        const action: AvailableAction = { name, type: raType as NodeExecutor["type"] };
        const desc = optionalString(ra as Record<string, unknown>, "description");
        if (desc) action.description = desc;
        if (isPlainRecord((ra as Record<string, unknown>).params)) {
          action.params = (ra as Record<string, unknown>).params as Record<string, unknown>;
        }
        return action;
      });
    }
  }
  // Semantic validation: adaptive and local-only require repairActions
  if (repair && repair.mode !== "replan-only" && (!repair.repairActions || repair.repairActions.length === 0)) {
    fail(`node ${nodeId}.executor.repair.repairActions`, `is required when repair.mode is "${repair.mode}"`);
  }

  // budget (optional)
  let budget: GoalLoopExecutor["budget"];
  const budgetRaw = obj.budget;
  if (isPlainRecord(budgetRaw)) {
    budget = {};
    const maxTokens = optionalNumber(budgetRaw as Record<string, unknown>, "maxTokens", `node ${nodeId}.executor.budget`);
    if (maxTokens !== undefined) budget.maxTokens = maxTokens;
    const maxIter = optionalNumber(budgetRaw as Record<string, unknown>, "maxIterations", `node ${nodeId}.executor.budget`);
    if (maxIter !== undefined) budget.maxIterations = maxIter;
    const maxNodes = optionalNumber(budgetRaw as Record<string, unknown>, "maxNodes", `node ${nodeId}.executor.budget`);
    if (maxNodes !== undefined) budget.maxNodes = maxNodes;
  }

  // verification (optional)
  let verification: GoalLoopExecutor["verification"];
  const verRaw = obj.verification;
  if (isPlainRecord(verRaw)) {
    const prompt = requireString(verRaw as Record<string, unknown>, "prompt", `node ${nodeId}.executor.verification`);
    verification = { prompt };
    const vModel = optionalString(verRaw as Record<string, unknown>, "model");
    if (vModel) verification.model = vModel;
    const minVotes = optionalNumber(verRaw as Record<string, unknown>, "minVotes", `node ${nodeId}.executor.verification`);
    if (minVotes !== undefined) verification.minVotes = minVotes;
    const totalVoters = optionalNumber(verRaw as Record<string, unknown>, "totalVoters", `node ${nodeId}.executor.verification`);
    if (totalVoters !== undefined) verification.totalVoters = totalVoters;
  }

  // convergence (optional, defaults: noProgressIterations=3, repeatedFailures=5)
  let convergence: GoalLoopExecutor["convergence"];
  const convRaw = obj.convergence;
  if (isPlainRecord(convRaw)) {
    convergence = {};
    const npi = optionalNumber(convRaw as Record<string, unknown>, "noProgressIterations", `node ${nodeId}.executor.convergence`);
    if (npi !== undefined) convergence.noProgressIterations = npi;
    const rf = optionalNumber(convRaw as Record<string, unknown>, "repeatedFailures", `node ${nodeId}.executor.convergence`);
    if (rf !== undefined) convergence.repeatedFailures = rf;
  }

  // maxIterations (default: 20)
  const maxIterations = optionalNumber(obj, "maxIterations", `node ${nodeId}.executor`) ?? 20;

  return {
    type: "goal-loop",
    goal,
    ...(initialPlan ? { initialPlan } : {}),
    evaluation,
    ...(repair ? { repair } : {}),
    ...(budget ? { budget } : {}),
    ...(verification ? { verification } : {}),
    ...(convergence ? { convergence } : {}),
    maxIterations,
  };
}

function normalizeLoopGroupExecutor(obj: Record<string, unknown>, nodeId: string): LoopGroupExecutor {
  const maxIterations = obj.maxIterations;
  if (!Number.isInteger(maxIterations) || (maxIterations as number) < 1) {
    fail(`node ${nodeId}.maxIterations`, "must be an integer >= 1");
  }
  const iterationVar = optionalString(obj, "iterationVar") ?? "iteration";
  if (reservedLoopIterationVars.has(iterationVar)) {
    fail(`node ${nodeId}.iterationVar`, `"${iterationVar}" is reserved by the template context`);
  }
  if (!Array.isArray(obj.body) || obj.body.length === 0) {
    fail(`node ${nodeId}.body`, "must contain at least one node");
  }

  const body = obj.body.map((entry, index) => {
    if (isPlainRecord(entry) && entry.type === "loop-group") {
      fail(`node ${nodeId}.body[${index}]`, "nested loop-group is not supported");
    }
    return normalizeNode(entry);
  });

  const untilRaw = obj.until;
  let until: LoopGroupExecutor["until"];
  if (untilRaw !== undefined) {
    if (!isPlainRecord(untilRaw)) fail(`node ${nodeId}.until`, "must be an object");
    until = {
      node: requireString(untilRaw, "node", `node ${nodeId}.until`),
      path: requireString(untilRaw, "path", `node ${nodeId}.until`),
      ...(Object.prototype.hasOwnProperty.call(untilRaw, "equals")
        ? { equals: untilRaw.equals as string | number | boolean | null }
        : {}),
    };
  }

  const onMaxRaw = obj.onMaxIterations;
  let onMaxIterations: LoopGroupExecutor["onMaxIterations"];
  if (onMaxRaw !== undefined) {
    if (!isPlainRecord(onMaxRaw)) fail(`node ${nodeId}.onMaxIterations`, "must be an object");
    const action = requireString(onMaxRaw, "action", `node ${nodeId}.onMaxIterations`);
    if (action !== "continue" && action !== "fail") {
      fail(`node ${nodeId}.onMaxIterations.action`, 'must be "continue" or "fail"');
    }
    onMaxIterations = {
      action,
      ...(typeof onMaxRaw.saveLastIteration === "boolean" ? { saveLastIteration: onMaxRaw.saveLastIteration } : {}),
    };
  }

  return {
    type: "loop-group",
    maxIterations: maxIterations as number,
    iterationVar,
    body,
    ...(until ? { until } : {}),
    ...(onMaxIterations ? { onMaxIterations } : {}),
  };
}

function normalizeHookRetry(raw: unknown, ctx: string): Required<NonNullable<HookActionSpec["retry"]>> {
  if (raw == null) return { maxAttempts: 1, backoffMs: 0 };
  if (!isPlainRecord(raw)) {
    fail(`${ctx}.retry`, "must be an object");
  }

  const maxAttempts = typeof raw.maxAttempts === "number" ? raw.maxAttempts : 1;
  const backoffMs = typeof raw.backoffMs === "number" ? raw.backoffMs : 0;
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    fail(`${ctx}.retry.maxAttempts`, "must be an integer >= 1");
  }
  if (!Number.isInteger(backoffMs) || backoffMs < 0) {
    fail(`${ctx}.retry.backoffMs`, "must be an integer >= 0");
  }
  return { maxAttempts, backoffMs };
}

function normalizeTriggerRule(join: unknown, rawTriggerRule: unknown, nodeId: string): TriggerRule {
  if (rawTriggerRule == null) {
    if (join === "any") return "one_success";
    return "all_success";
  }
  if (rawTriggerRule === "all_success" || rawTriggerRule === "one_success" || rawTriggerRule === "all_done") {
    return rawTriggerRule;
  }
  fail(`node ${nodeId}.triggerRule`, "must be one of all_success, one_success, all_done");
}

const nodeRetryFailureReasons = new Set<NodeRetryFailureReason>([
  "executor-failed",
  "output-contract-failed",
  "validation-failed",
]);

function normalizeNodeRetry(raw: unknown): Required<NodeRetrySpec> {
  if (raw == null) return { maxAttempts: 1, backoffMs: 0, on: ["executor-failed"] };
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new WorkflowValidationError([{ path: "retry", message: "must be an object" }]);
  }
  const obj = raw as Record<string, unknown>;
  const maxAttempts = typeof obj.maxAttempts === "number" ? obj.maxAttempts : 1;
  const backoffMs = typeof obj.backoffMs === "number" ? obj.backoffMs : 0;
  const on = obj.on === undefined ? ["executor-failed"] : obj.on;
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new WorkflowValidationError([{ path: "retry.maxAttempts", message: "must be an integer >= 1" }]);
  }
  if (!Number.isInteger(backoffMs) || backoffMs < 0) {
    throw new WorkflowValidationError([{ path: "retry.backoffMs", message: "must be an integer >= 0" }]);
  }
  if (!Array.isArray(on) || on.length === 0) {
    throw new WorkflowValidationError([{ path: "retry.on", message: "must be a non-empty array" }]);
  }
  for (const reason of on) {
    if (typeof reason !== "string" || !nodeRetryFailureReasons.has(reason as NodeRetryFailureReason)) {
      throw new WorkflowValidationError([{
        path: "retry.on",
        message: "must contain only executor-failed, output-contract-failed, or validation-failed",
      }]);
    }
  }
  return { maxAttempts, backoffMs, on: on as NodeRetryFailureReason[] };
}

const outputContractSchemaTypes = new Set(["object", "array", "string", "number", "boolean"]);

function normalizeOutputContractSchema(raw: unknown, ctx: string): OutputContractSchema {
  if (!isPlainRecord(raw)) {
    fail(ctx, "schema must be an object");
  }
  const type = raw.type;
  if (typeof type !== "string" || !outputContractSchemaTypes.has(type)) {
    fail(`${ctx}.type`, "must be one of object, array, string, number, boolean");
  }

  const schema: OutputContractSchema = { type: type as OutputContractSchema["type"] };

  if (raw.required !== undefined) {
    if (!Array.isArray(raw.required) || raw.required.some((item) => typeof item !== "string")) {
      fail(`${ctx}.required`, "must be a string array");
    }
    schema.required = raw.required as string[];
  }

  if (raw.properties !== undefined) {
    if (type !== "object") {
      fail(`${ctx}.properties`, "is only allowed for object schema");
    }
    if (!isPlainRecord(raw.properties)) {
      fail(`${ctx}.properties`, "must be an object");
    }
    schema.properties = {};
    for (const [key, value] of Object.entries(raw.properties)) {
      schema.properties[key] = normalizeOutputContractSchema(value, `${ctx}.properties.${key}`);
    }
  }

  if (raw.items !== undefined) {
    if (type !== "array") {
      fail(`${ctx}.items`, "is only allowed for array schema");
    }
    schema.items = normalizeOutputContractSchema(raw.items, `${ctx}.items`);
  }

  const nullable = optionalBoolean(raw, "nullable");
  if (nullable !== undefined) {
    schema.nullable = nullable;
  }

  if (raw.enum !== undefined) {
    if (
      !Array.isArray(raw.enum)
      || raw.enum.some((item) => item !== null && typeof item !== "string" && typeof item !== "number" && typeof item !== "boolean")
    ) {
      fail(`${ctx}.enum`, "must be an array of string, number, boolean, or null");
    }
    schema.enum = raw.enum as Array<string | number | boolean | null>;
  }

  return schema;
}

function normalizeOutputContract(raw: unknown, nodeId: string): OutputContractSpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(`node ${nodeId}.outputContract`, "must be an object");
  }
  if (raw.schema === undefined) {
    fail(`node ${nodeId}.outputContract.schema`, "is required");
  }
  return {
    required: optionalBoolean(raw, "required") ?? true,
    schema: normalizeOutputContractSchema(raw.schema, `node ${nodeId}.outputContract.schema`),
  };
}

function normalizeOutputSchema(raw: unknown, nodeId: string): OutputContractSchema | undefined {
  if (raw == null) return undefined;
  return normalizeOutputContractSchema(raw, `node ${nodeId}.outputSchema`);
}

const workflowInputModes = new Set(["params", "task", "mixed"]);

function normalizeWorkflowInputSources(raw: unknown): WorkflowInputSpec["sources"] {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("input.sources", "must be an object");
  }

  const filesRaw = raw.files;
  let files: NonNullable<WorkflowInputSpec["sources"]>["files"];
  if (filesRaw !== undefined) {
    if (!isPlainRecord(filesRaw)) {
      fail("input.sources.files", "must be an object");
    }
    const maxCount = optionalNumber(filesRaw, "maxCount", "input.sources.files");
    if (maxCount !== undefined && (!Number.isInteger(maxCount) || maxCount < 1)) {
      fail("input.sources.files.maxCount", "must be an integer >= 1");
    }
    const maxSizeMb = optionalNumber(filesRaw, "maxSizeMb", "input.sources.files");
    if (maxSizeMb !== undefined && maxSizeMb <= 0) {
      fail("input.sources.files.maxSizeMb", "must be > 0");
    }
    files = {
      ...(maxCount !== undefined ? { maxCount } : {}),
      ...(maxSizeMb !== undefined ? { maxSizeMb } : {}),
      ...(optionalStringArray(filesRaw, "allowedExtensions", "input.sources.files")
        ? { allowedExtensions: optionalStringArray(filesRaw, "allowedExtensions", "input.sources.files") }
        : {}),
    };
  }

  return {
    ...(typeof raw.params === "boolean" ? { params: raw.params } : {}),
    ...(typeof raw.message === "boolean" ? { message: raw.message } : {}),
    ...(files ? { files } : {}),
  };
}

function normalizeWorkflowInput(raw: unknown, legacyRequiredParams: string[] | undefined): WorkflowInputSpec | undefined {
  if (raw == null) {
    return legacyRequiredParams ? { requiredParams: legacyRequiredParams } : undefined;
  }
  if (!isPlainRecord(raw)) {
    fail("input", "must be an object");
  }

  const mode = optionalString(raw, "mode");
  if (mode !== undefined && !workflowInputModes.has(mode)) {
    fail("input.mode", "must be one of params, task, mixed");
  }

  return {
    ...(mode ? { mode: mode as WorkflowInputSpec["mode"] } : {}),
    requiredParams: optionalStringArray(raw, "requiredParams", "input") ?? legacyRequiredParams,
    schema: optionalRecord(raw, "schema", "input"),
    sources: normalizeWorkflowInputSources(raw.sources),
  };
}

const identityDuplicatePolicies = new Set(["reject-active", "allow", "reuse-active"]);
const identityTemplatePattern = /^\{\{\s*input\.(?:params\.[A-Za-z0-9_.-]+|digest|message)\s*\}\}$/;

function normalizeWorkflowIdentity(raw: unknown): WorkflowIdentitySpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("identity", "must be an object");
  }
  if (typeof raw.key !== "string" || !raw.key.trim()) {
    fail("identity.key", "\"key\" must be a non-empty string");
  }
  if (!identityTemplatePattern.test(raw.key)) {
    fail(
      "identity.key",
      "must be a single template expression such as {{input.params.ticketId}} or {{input.digest}}; for versioned keys, use one explicit input param such as {{input.params.versionedTaskId}}",
    );
  }
  const duplicatePolicy = optionalString(raw, "duplicatePolicy");
  if (duplicatePolicy !== undefined && !identityDuplicatePolicies.has(duplicatePolicy)) {
    fail("identity.duplicatePolicy", "must be one of reject-active, allow, reuse-active");
  }
  return {
    key: raw.key,
    label: optionalString(raw, "label"),
    duplicatePolicy: duplicatePolicy as WorkflowIdentitySpec["duplicatePolicy"] | undefined,
  };
}

function normalizeWorkflowOutputs(raw: unknown): WorkflowOutputsSpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("outputs", "must be an object");
  }

  const outputs: WorkflowOutputsSpec = {};
  for (const [name, value] of Object.entries(raw)) {
    if (!name.trim()) {
      fail("outputs", "output name must be non-empty");
    }
    if (!isPlainRecord(value)) {
      fail(`outputs.${name}`, "must be an object");
    }
    outputs[name] = {
      from: requireString(value, "from", `outputs.${name}`),
      public: optionalBoolean(value, "public"),
      description: optionalString(value, "description"),
    };
  }
  return outputs;
}

function normalizeHookActionSpec(raw: unknown, ctx: string): HookActionSpec {
  if (!isPlainRecord(raw)) {
    fail(ctx, "hook must be an object");
  }
  const saveAs = optionalRecord(raw, "saveAs", ctx);
  if (saveAs) {
    for (const [target, template] of Object.entries(saveAs)) {
      if (typeof template !== "string") {
        fail(ctx, `saveAs "${target}" must be a string`);
      }
    }
  }
  return {
    id: requireString(raw, "id", ctx),
    action: requireString(raw, "action", ctx),
    required: optionalBoolean(raw, "required") ?? false,
    args: optionalRecord(raw, "args", ctx),
    retry: normalizeHookRetry(raw.retry, ctx),
    saveAs: saveAs as Record<string, string> | undefined,
  };
}

function normalizeValidationActionSpec(raw: unknown, ctx: string): HookActionSpec {
  // Validation actions are always required; reuse hook normalization but force required=true.
  const spec = normalizeHookActionSpec(raw, ctx);
  return { ...spec, required: true };
}

export function normalizeHookActions(raw: unknown, ctx: string): HookActionSpec[] | undefined {
  if (raw == null) return undefined;
  if (!Array.isArray(raw)) {
    fail(ctx, "hooks must be an array");
  }

  return raw.map((entry, index) => normalizeHookActionSpec(entry, `${ctx}[${index}]`));
}

const validationFailureActions = new Set<NodeValidationFailureAction>(["fail-node", "block-node", "ignore"]);

function normalizeNodeValidation(raw: unknown, ctx: string): NodeValidationSpec | undefined {
  if (raw == null) return undefined;
  const obj = requireRecord(raw, ctx);
  const actionsRaw = obj.actions;
  if (!Array.isArray(actionsRaw) || actionsRaw.length === 0) {
    fail(`${ctx}.actions`, "must be a non-empty array");
  }

  const actions: HookActionSpec[] = [];
  for (let i = 0; i < actionsRaw.length; i += 1) {
    actions.push(normalizeValidationActionSpec(actionsRaw[i], `${ctx}.actions[${i}]`));
  }

  const onFailureRaw = obj.onFailure;
  let onFailure: NodeValidationFailureAction = "block-node";
  if (onFailureRaw != null) {
    if (!validationFailureActions.has(onFailureRaw as NodeValidationFailureAction)) {
      fail(`${ctx}.onFailure`, 'must be one of "fail-node", "block-node", "ignore"');
    }
    onFailure = onFailureRaw as NodeValidationFailureAction;
  }

  return { actions, onFailure };
}

function normalizeWorkflowPreflightActions(raw: unknown, ctx: string): WorkflowPreflightActionSpec[] | undefined {
  if (raw == null) return undefined;
  if (!Array.isArray(raw)) {
    fail(ctx, "preflight must be an array");
  }

  return raw.map((entry, index) => {
    if (!isPlainRecord(entry)) {
      fail(`${ctx}[${index}]`, "preflight action must be an object");
    }
    const saveAs = optionalRecord(entry, "saveAs", `${ctx}[${index}]`);
    if (saveAs) {
      for (const [target, template] of Object.entries(saveAs)) {
        if (typeof template !== "string") {
          fail(`${ctx}[${index}]`, `saveAs "${target}" must be a string`);
        }
      }
    }

    let abortIf: WorkflowPreflightActionSpec["abortIf"];
    if (entry.abortIf !== undefined) {
      if (!isPlainRecord(entry.abortIf)) {
        fail(`${ctx}[${index}].abortIf`, "must be an object");
      }
      const empty = entry.abortIf.empty;
      if (empty !== undefined && typeof empty !== "boolean" && typeof empty !== "string") {
        fail(`${ctx}[${index}].abortIf.empty`, "must be a boolean or template string");
      }
      abortIf = {
        ...(empty !== undefined ? { empty: empty as boolean | string } : {}),
        message: optionalString(entry.abortIf, "message"),
      };
      if (entry.abortIf.in !== undefined) {
        if (!isPlainRecord(entry.abortIf.in)) {
          fail(`${ctx}[${index}].abortIf.in`, "must be an object");
        }
        const value = entry.abortIf.in.value;
        const list = entry.abortIf.in.list;
        if (typeof value !== "string" || !value.trim()) {
          fail(`${ctx}[${index}].abortIf.in.value`, "must be a non-empty template string");
        }
        if (!Array.isArray(list) || list.length === 0) {
          fail(`${ctx}[${index}].abortIf.in.list`, "must be a non-empty array");
        }
        abortIf.in = {
          value,
          list,
          message: optionalString(entry.abortIf.in, "message"),
        };
      }
    }

    return {
      id: optionalString(entry, "id"),
      action: requireString(entry, "action", `${ctx}[${index}]`),
      required: optionalBoolean(entry, "required") ?? true,
      args: optionalRecord(entry, "args", `${ctx}[${index}]`),
      retry: normalizeHookRetry(entry.retry, `${ctx}[${index}]`),
      saveAs: saveAs as Record<string, string> | undefined,
      abortIf,
    };
  });
}

export function normalizeWorkflowLifecycle(raw: unknown): WorkflowSpec["workflow"] {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail("workflow", "must be an object");
  }
  return {
    preflight: normalizeWorkflowPreflightActions(raw.preflight, "workflow.preflight"),
    onStart: normalizeHookActions(raw.onStart, "workflow.onStart"),
    onFinish: normalizeHookActions(raw.onFinish, "workflow.onFinish"),
  };
}

function normalizeOnResult(raw: unknown, nodeId: string): NodeOnResult | undefined {
  if (raw == null) return undefined;
  const obj = isPlainRecord(raw) ? raw : {};
  const result: NodeOnResult = {};

  if (isPlainRecord(obj.if)) {
    result.if = obj.if as Record<string, string | number | boolean | null>;
  }

  if (isPlainRecord(obj.then)) {
    const then = obj.then;
    result.then = {};
    if (isPlainRecord(then.wait)) {
      const waitObj = then.wait;
      if (waitObj.type !== "human") {
        fail(`node ${nodeId}`, `onResult.then.wait.type must be "human"`);
      }
      const actions = normalizeHumanGateActions(waitObj.actions, `node ${nodeId}.onResult.then.wait.actions`);
      result.then.wait = {
        type: "human",
        prompt: requireString(waitObj, "prompt", `node ${nodeId} onResult.then.wait`),
        waitKind: optionalString(waitObj, "waitKind"),
        inputSchema: normalizeHumanInputSchema(waitObj.inputSchema, `node ${nodeId}.onResult.then.wait.inputSchema`),
        saveAs: normalizeSaveAs(waitObj.saveAs, `node ${nodeId}.onResult.then.wait`),
        actions,
        commandHints: normalizeHumanCommandHints(
          waitObj.commandHints,
          actions,
          `node ${nodeId}.onResult.then.wait.commandHints`,
        ),
      };
    }
    if (then.complete === true) result.then.complete = true;
    // then-level passthrough: keep unknown then-fields (rerun/saveAs/feedbackPath/
    // feedbackTemplate) so onResult 业务逻辑不被 normalize 掏空成 `then: {}`。
    // 对齐 ClawWeb 整体透传。
    for (const key of Object.keys(then)) {
      if (!(key in result.then)) {
        (result.then as Record<string, unknown>)[key] = then[key];
      }
    }
  }

  if (isPlainRecord(obj.else)) {
    const elseObj = obj.else;
    result.else = {};
    if (isPlainRecord(elseObj.wait)) {
      const waitObj = elseObj.wait;
      if (waitObj.type !== "human") {
        fail(`node ${nodeId}`, `onResult.else.wait.type must be "human"`);
      }
      const actions = normalizeHumanGateActions(waitObj.actions, `node ${nodeId}.onResult.else.wait.actions`);
      result.else.wait = {
        type: "human",
        prompt: requireString(waitObj, "prompt", `node ${nodeId} onResult.else.wait`),
        waitKind: optionalString(waitObj, "waitKind"),
        inputSchema: normalizeHumanInputSchema(waitObj.inputSchema, `node ${nodeId}.onResult.else.wait.inputSchema`),
        saveAs: normalizeSaveAs(waitObj.saveAs, `node ${nodeId}.onResult.else.wait`),
        actions,
        commandHints: normalizeHumanCommandHints(
          waitObj.commandHints,
          actions,
          `node ${nodeId}.onResult.else.wait.commandHints`,
        ),
      };
    }
    if (elseObj.complete === true) result.else.complete = true;
    // else-level passthrough (mirror then): keep unknown else-fields.
    for (const key of Object.keys(elseObj)) {
      if (!(key in result.else)) {
        (result.else as Record<string, unknown>)[key] = elseObj[key];
      }
    }
  }

  if (Array.isArray(obj.branches)) {
    if (result.if !== undefined) {
      fail(`node ${nodeId}`, `onResult cannot have both "branches" and "if/then/else" — they are mutually exclusive`);
    }
    result.branches = obj.branches.map((branch: unknown, idx: number) => {
      if (!isPlainRecord(branch)) {
        fail(`node ${nodeId}`, `onResult.branches[${idx}] must be an object`);
        return { match: {} };
      }
      if (!isPlainRecord(branch.match)) {
        fail(`node ${nodeId}`, `onResult.branches[${idx}].match must be an object`);
      }
      return {
        branchId: typeof branch.branchId === "string" ? branch.branchId : undefined,
        match: branch.match as Record<string, string | number | boolean | null>,
        complete: branch.complete === true,
      };
    });
  }

  if (isPlainRecord(obj.default)) {
    result.default = { complete: obj.default.complete === true };
  }

  return result;
}

/**
 * 深度保真:normalize 是白名单重建,会丢 raw 里它没列入的子字段(任意深度 ——
 * input.defaults/optionalParams、onResult.then.rerun、outputContract.schema.nullable、
 * version 字符串等)。在 normalize 构造完结果后调用,把 raw 有、normalize 结果缺失
 * (或显式 undefined)的字段补回,使 **保存和运行共用同一个全量 spec**(保存=运行 完全一致)。
 *
 * 规则:normalize 已设值的字段(含注入的 triggerRule/retry 默认)不动,只补 raw 有而结果缺失的。
 * 这样 normalize 仍负责校验(非法值 fail)+ 注入默认,但不许丢用户字段 —— 对齐 clawweb 的全量透传。
 */
function deepPreserveUnknown(raw: unknown, target: unknown): void {
  if (!isPlainRecord(raw) || !isPlainRecord(target)) return;
  for (const key of Object.keys(raw)) {
    const rv = (raw as Record<string, unknown>)[key];
    if (rv === undefined) continue;
    const tv = (target as Record<string, unknown>)[key];
    if (tv === undefined) {
      // normalize 重建丢了/没收这个字段 —— 补回 raw(深拷贝)
      (target as Record<string, unknown>)[key] = JSON.parse(JSON.stringify(rv));
      continue;
    }
    if (isPlainRecord(rv) && isPlainRecord(tv)) {
      deepPreserveUnknown(rv, tv); // 深度补内部子结构丢的字段
    }
    // 数组/原始值:保留 target 的 normalize 版(不覆盖注入的默认)
  }
}

export function normalizeNode(raw: unknown): WorkflowNode {
  const obj = requireRecord(raw, "node");
  const id = requireString(obj, "id", "node");
  const join = optionalString(obj, "join") as "all" | "any" | undefined;
  const executorObj = isPlainRecord(obj.executor) ? obj.executor as Record<string, unknown> : undefined;
  const isLoopGroup = optionalString(obj, "type") === "loop-group"
    || executorObj?.type === "loop-group";
  const node: WorkflowNode = {
    id,
    title: requireString(obj, "title", `node ${id}`),
    phase: requireString(obj, "phase", `node ${id}`),
    businessStatus: optionalString(obj, "businessStatus"),
    dependsOn: Array.isArray(obj.dependsOn) ? (obj.dependsOn as string[]) : [],
    branchId: optionalString(obj, "branchId"),
    join,
    triggerRule: normalizeTriggerRule(join, obj.triggerRule, id),
    retry: normalizeNodeRetry(obj.retry),
    executor: isLoopGroup ? normalizeLoopGroupExecutor(executorObj?.type === "loop-group" ? executorObj : obj, id) : normalizeExecutor(obj.executor, id),
    outputContract: normalizeOutputContract(obj.outputContract, id),
    outputSchema: normalizeOutputSchema(obj.outputSchema, id),
    onResult: normalizeOnResult(obj.onResult, id),
    onSuccess: normalizeHookActions(obj.onSuccess, `node ${id} onSuccess`),
    validation: normalizeNodeValidation(obj.validation, `node ${id}.validation`),
    progressMessage: optionalString(obj, "progressMessage"),
    skipWhen: optionalRecord(obj, "skipWhen", `node ${id} skipWhen`),
    skipResult: optionalRecord(obj, "skipResult", `node ${id} skipResult`),
    knowledge: optionalBoolean(obj, "knowledge"),
    knowledgeBaseId: optionalString(obj, "knowledgeBaseId"),
    knowledgeQuery: optionalString(obj, "knowledgeQuery"),
    alerting: normalizeNodeAlerting(obj.alerting, `node ${id} alerting`),
    validationTemplateId: optionalString(obj, "validationTemplateId"),
    validationMinScore: normalizeValidationMinScore(obj.validationMinScore, `node ${id} validationMinScore`),
    mock: normalizeMockConfig(obj.mock, `node ${id} mock`),
  };
  if (obj.triggerRule === "all_success" || obj.triggerRule === "one_success" || obj.triggerRule === "all_done") {
    explicitTriggerRuleNodes.add(node);
  }

  // Preserve unknown node-level fields (aligns with ClawWeb's normalizeNode).
  //
  // Why: normalizeNode is a whitelist constructor — any key not listed above is
  // silently dropped. That is fine for engine-owned fields (we normalize them
  // with correct types/defaults), but it eats user-authored metadata that the
  // engine doesn't read but that should round-trip through save/deploy/migration
  // intact (e.g. comments-as-fields, editor annotations, future fields added to
  // a yaml before ClawMind ships support). Dropping them caused real data loss:
  // skipWhen/skipResult/knowledge/etc. were eaten for a time before being added
  // to the whitelist. Passthrough makes normalize forward-compatible — a new
  // yaml field survives even before ClawMind learns to interpret it.
  //
  // Scope: NODE level only. Executor internals are deliberately NOT passthrough
  // — `normalizeExecutor` enforces per-type structure and injects engine
  // defaults (prompt required for embedded-agent, approvers for approval, etc.)
  // that the runtime depends on. Relaxing executor to a shallow copy would
  // discard that validation and change runtime behavior. ClawWeb can afford a
  // shallow executor copy because it only persists, never executes; ClawMind
  // cannot.
  for (const key of Object.keys(obj)) {
    if (key in node) continue;
    (node as Record<string, unknown>)[key] = obj[key];
  }
  // 深度保真:补 normalize 白名单重建在各子结构(executor/onResult/outputContract 等)
  // 里丢的字段 —— 让引擎运行的 spec 和保存的 spec 都是全量,且 skipWhen 等能被读到生效。
  deepPreserveUnknown(obj, node);

  // 打标:executor.saveAs 若存在但该 executor 不会执行 → 记入 _ignored 供 validate 告警。
  // 节点级 saveAs(sibling to executor)同理:无任何运行时钩子读取 node.saveAs,亦记入。
  // 这是"配套 validate"的一环:deepPreserveUnknown 让字段不丢,_ignored 让 validate 能看见
  // 它是 dead 的,从而给出正确方向(改用 nodeOutput/移到生效位置),而非静默吞字段。
  const ignored: string[] = [];
  const execAny = node.executor as Record<string, unknown> | undefined;
  if (execAny && "saveAs" in execAny && execAny.saveAs !== undefined && !isSaveAsCapableExecutor(node)) {
    ignored.push("executor.saveAs");
  }
  if ("saveAs" in obj && obj.saveAs !== undefined) {
    ignored.push("saveAs");
  }
  if (ignored.length > 0) {
    (node as WorkflowNode & { _ignored?: string[] })._ignored = ignored;
  }
  return node;
}

/**
 * Normalize a per-node alerting override (`NodeAlertingSpec`).
 *
 * Mirrors the global alerting shape but scoped to a single node: any subset of
 * `dingtalk` / `severity` / `keywords` may be given, others are dropped to
 * `undefined`. Severity must be one of the allowed levels when present.
 */
function normalizeNodeAlerting(
  raw: unknown,
  ctx: string,
): NodeAlertingSpec | undefined {
  if (raw == null) return undefined;
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const dingtalk = optionalBoolean(raw, "dingtalk");
  const severity = optionalString(raw, "severity") as NodeAlertingSpec["severity"] | undefined;
  if (severity !== undefined && severity !== "critical" && severity !== "warning" && severity !== "info") {
    fail(`${ctx}.severity`, "must be one of critical, warning, info");
  }
  const keywords = optionalStringArray(raw, "keywords", ctx);
  const result: NodeAlertingSpec = {};
  if (dingtalk !== undefined) result.dingtalk = dingtalk;
  if (severity !== undefined) result.severity = severity;
  if (keywords !== undefined) result.keywords = keywords;
  return Object.keys(result).length > 0 ? result : undefined;
}

/**
 * Normalize `validationMinScore`: a number in [0, 100]. Anything outside that
 * range is rejected rather than silently clamped, so a typo doesn't weaken
 * validation. `undefined` (field absent) is allowed and returned as-is.
 */
function normalizeValidationMinScore(raw: unknown, ctx: string): number | undefined {
  if (raw == null) return undefined;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0 || raw > 100) {
    fail(ctx, "must be a number in [0, 100]");
  }
  return raw;
}

function normalizeMessages(raw: unknown): WorkflowSpec["messages"] {
  if (raw == null || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  return {
    onCreated: optionalString(obj, "onCreated"),
    onFinished: optionalString(obj, "onFinished"),
    onFinishedVariants: optionalStringArray(obj, "onFinishedVariants", "messages"),
    onFinishedRareVariants: optionalStringArray(obj, "onFinishedRareVariants", "messages"),
  };
}

function normalizeDebug(raw: unknown): WorkflowSpec["debug"] {
  if (raw == null || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  const summaryKeys = optionalStringArray(obj, "summaryKeys", "debug");
  return summaryKeys ? { summaryKeys } : undefined;
}

function normalizeChatInjectLevel(raw: unknown, ctx: string): { level?: import("../inject-level.js").InjectLevel } | undefined {
  if (!isPlainRecord(raw)) {
    fail(`${ctx}.chatInject`, "must be an object");
  }
  const r = raw as { level?: unknown };
  if (r.level === undefined) return undefined;
  if (r.level !== "perf" && r.level !== "simple" && r.level !== "full") {
    fail(`${ctx}.chatInject.level`, `must be one of perf|simple|full, got ${String(r.level)}`);
  }
  return { level: r.level };
}

function normalizeNotifications(raw: unknown): WorkflowNotifications | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const obj = raw as Record<string, unknown>;

  // Always parse httpCallbacks regardless of dingtalk presence
  const httpCallbacksResult = normalizeHttpCallbacks(obj.httpCallbacks);

  const dingtalk = obj.dingtalk;
  if (!dingtalk || typeof dingtalk !== "object" || Array.isArray(dingtalk)) {
    // No dingtalk config — return httpCallbacks only if present
    return httpCallbacksResult.httpCallbacks ? { ...httpCallbacksResult } : undefined;
  }
  const dt = dingtalk as Record<string, unknown>;

  const robotCode = typeof dt.robotCode === "string" ? dt.robotCode : "";
  const appSecret = typeof dt.appSecret === "string" ? dt.appSecret : "";
  if (!robotCode || !appSecret) {
    // No valid dingtalk config — return httpCallbacks only if present
    return httpCallbacksResult.httpCallbacks ? { ...httpCallbacksResult } : undefined;
  }

  const onFailure = dt.onFailure;
  if (!onFailure || typeof onFailure !== "object" || Array.isArray(onFailure)) return { dingtalk: { robotCode, appSecret, onFailure: {} }, ...httpCallbacksResult };
  const of = onFailure as Record<string, unknown>;

  const users: DingTalkUserTarget[] | undefined = Array.isArray(of.users)
    ? of.users.filter((u: unknown) => u && typeof u === "object" && typeof (u as Record<string, unknown>).userId === "string")
        .map((u: unknown) => {
          const r = u as Record<string, unknown>;
          return { userId: r.userId as string, ...(typeof r.name === "string" ? { name: r.name } : {}) };
        })
    : undefined;

  const groups: DingTalkGroupTarget[] | undefined = Array.isArray(of.groups)
    ? of.groups.filter((g: unknown) => g && typeof g === "object" && typeof (g as Record<string, unknown>).openConversationId === "string")
        .map((g: unknown) => {
          const r = g as Record<string, unknown>;
          return { openConversationId: r.openConversationId as string, ...(typeof r.name === "string" ? { name: r.name } : {}) };
        })
    : undefined;

  let message: DingTalkMessageConfig | undefined;
  if (of.message && typeof of.message === "object" && !Array.isArray(of.message)) {
    const m = of.message as Record<string, unknown>;
    message = {
      ...(typeof m.title === "string" ? { title: m.title } : {}),
      ...(typeof m.includeRunLink === "boolean" ? { includeRunLink: m.includeRunLink } : {}),
    };
  }

  return {
    dingtalk: {
      robotCode,
      appSecret,
      onFailure: {
        ...(users && users.length > 0 ? { users } : {}),
        ...(groups && groups.length > 0 ? { groups } : {}),
        ...(message ? { message } : {}),
      },
    },
    ...httpCallbacksResult,
  };
}

/** Valid NotifyEvent values for YAML validation. */
const VALID_NOTIFY_EVENTS: NotifyEvent[] = [
  "workflow_started",
  "node_started",
  "node_succeeded",
  "node_failed",
  "node_rejected",
  "node_skipped",
  "workflow_succeeded",
  "workflow_failed",
  "workflow_cancelled",
];

function normalizeHttpCallbacks(raw: unknown): { httpCallbacks?: HttpCallbackNotification[] } {
  if (!raw || !Array.isArray(raw)) return {};
  const callbacks: HttpCallbackNotification[] = [];
  for (let i = 0; i < raw.length; i++) {
    const item = raw[i];
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      fail(`notifications.httpCallbacks[${i}]`, "must be an object");
      continue;
    }
    const r = item as Record<string, unknown>;

    const name = typeof r.name === "string" ? r.name : "";
    if (!name) {
      fail(`notifications.httpCallbacks[${i}].name`, "is required");
      continue;
    }

    const url = typeof r.url === "string" ? r.url : "";
    if (!url) {
      fail(`notifications.httpCallbacks[${i}].url`, "is required");
      continue;
    }
    if (!url.startsWith("https://")) {
      fail(`notifications.httpCallbacks[${i}].url`, "must use HTTPS");
    }

    const secret = typeof r.secret === "string" && r.secret ? r.secret : undefined;

    let notifyOn: NotifyEvent[] = [];
    if (Array.isArray(r.notifyOn)) {
      notifyOn = (r.notifyOn as unknown[]).filter((v): v is NotifyEvent =>
        typeof v === "string" && VALID_NOTIFY_EVENTS.includes(v as NotifyEvent)
      );
      if (notifyOn.length === 0) {
        fail(`notifications.httpCallbacks[${i}].notifyOn`, "must contain at least one valid event");
      }
    } else {
      fail(`notifications.httpCallbacks[${i}].notifyOn`, "is required and must be an array");
    }

    callbacks.push({
      name,
      url,
      ...(secret ? { secret } : {}),
      notifyOn,
      ...(typeof r.enabled === "boolean" ? { enabled: r.enabled } : {}),
      ...(typeof r.timeoutMs === "number" ? { timeoutMs: r.timeoutMs } : {}),
      ...(typeof r.maxRetries === "number" ? { maxRetries: r.maxRetries } : {}),
      ...(typeof r.retryDelayMs === "number" ? { retryDelayMs: r.retryDelayMs } : {}),
      ...(typeof r.includeNodeOutput === "boolean" ? { includeNodeOutput: r.includeNodeOutput } : {}),
    });
  }
  return callbacks.length > 0 ? { httpCallbacks: callbacks } : {};
}

function normalizeNodeTemplates(raw: unknown): Record<string, NodeTemplate> | undefined {
  if (raw == null || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const obj = raw as Record<string, unknown>;
  const result: Record<string, NodeTemplate> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (!isPlainRecord(value)) {
      fail(`workflow.nodeTemplates.${key}`, "must be an object");
    }
    if (!Array.isArray(value.body) || value.body.length === 0) {
      fail(`workflow.nodeTemplates.${key}.body`, "must contain at least one node");
    }
    result[key] = {
      body: (value.body as unknown[]).map(normalizeNode),
      ...(isPlainRecord(value.params) ? { params: value.params as Record<string, unknown> } : {}),
    };
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

export function normalizeWorkflowSpec(raw: unknown): WorkflowSpec {
  const obj = requireRecord(raw, "workflow");
  const rawDefaults = optionalRecord(obj, "defaults", "workflow");
  const requiredParams = optionalStringArray(obj, "requiredParams", "workflow");
  if (rawDefaults?.actors !== undefined) {
    normalizeActors(rawDefaults.actors);
  }
  const defaults = rawDefaults
    ? {
        progress: normalizeProgress(rawDefaults.progress),
        user: normalizeUser(rawDefaults.user),
        packRoot: optionalString(rawDefaults, "packRoot"),
        contextPolicy: normalizeContextPolicyDefaults(rawDefaults.contextPolicy),
      }
    : undefined;

  if (!Array.isArray(obj.nodes)) {
    fail("workflow.nodes", "must be an array");
  }

  const spec: WorkflowSpec = {
    id: requireString(obj, "id", "workflow"),
    version: typeof obj.version === "number" ? obj.version : undefined,
    title: requireString(obj, "title", "workflow"),
    configPath: optionalString(obj, "configPath"),
    config: (obj.config && typeof obj.config === "object" && !Array.isArray(obj.config)) ? obj.config as Record<string, unknown> : undefined,
    requiredParams,
    input: normalizeWorkflowInput(obj.input, requiredParams),
    identity: normalizeWorkflowIdentity(obj.identity),
    outputs: normalizeWorkflowOutputs(obj.outputs),
    debug: normalizeDebug(obj.debug),
    defaults,
    collaboration: normalizeWorkflowCollaboration(obj.collaboration),
    workflow: normalizeWorkflowLifecycle(obj.workflow),
    messages: normalizeMessages(obj.messages),
    notifications: normalizeNotifications(obj.notifications),
    chatInject: obj.chatInject === undefined ? undefined : normalizeChatInjectLevel(obj.chatInject, "workflow"),
    // Prefer the canonical `flowTimeoutMinutes`; fall back to the legacy
    // `timeoutMinutes` spelling so historical workflows still load. The legacy
    // spelling is purged from the output below (see orphan cleanup) so saved/
    // deployed specs converge on the new name.
    flowTimeoutMinutes:
      typeof obj.flowTimeoutMinutes === "number" ? obj.flowTimeoutMinutes
      : typeof obj.timeoutMinutes === "number" ? obj.timeoutMinutes
      : undefined,
    nodes: obj.nodes.map(normalizeNode),
    tests: normalizeTestCases(obj.tests, "tests"),
    nodeTemplates: normalizeNodeTemplates(obj.nodeTemplates),
  };

  // Preserve unknown top-level fields (aligns with ClawWeb's normalizeWorkflowSpec).
  // Same rationale as node-level passthrough in `normalizeNode`: keeps user/authored
  // metadata and forward-compatible fields round-tripping through save/deploy/migration
  // instead of being silently dropped by the whitelist constructor. Engine-known keys
  // are already set above and skipped via the `key in spec` guard.
  for (const key of Object.keys(obj)) {
    if (key in spec) continue;
    (spec as Record<string, unknown>)[key] = obj[key];
  }
  // 深度保真:补 normalize 重建在 spec 子结构(input.defaults/optionalParams/version 等)
  // 里丢的字段。保存和运行共用此 spec,全量 → 保存=运行 完全一致。
  deepPreserveUnknown(obj, spec);
  // Migrate legacy `timeoutMinutes` → `flowTimeoutMinutes`. The value was read
  // (with fallback) into `flowTimeoutMinutes` above, but the old spelling may
  // still be present after passthrough/deepPreserveUnknown. Drop it so the
  // emitted spec uses only the canonical name; reading still tolerates it.
  delete (spec as Record<string, unknown>).timeoutMinutes;
  return spec;
}

// ── 契约可满足性静态分析 ──
//
// 精度原则(North Star:不得误杀合法配置):
//   - 硬错误(error):只在「required 字段的唯一声明生产者是一个不会执行的 saveAs」时下,
//     因为此时运行时契约必缺字段,静态可证。其余一律不硬错误。
//   - 软警告(warning):外部输出型(mcp-call/cli-script/baas-call)的 required 字段
//     无生效生产者 → 工具可能返回但不保证;LLM 型(embedded-agent/subagent)的 required
//     字段名未出现在 prompt → 可能产但不保证。两者都放行,只提示。
//   - 放行:capable executor(human/async-callback/approval)的 executor.saveAs 提供;
//     skipResult 提供;onResult.rerun/wait.saveAs 提供。
//
// 生产者判定必须与 isSaveAsCapableExecutor 同源——capable 集合若变,此处同步。
const EXTERNAL_OUTPUT_EXECUTORS = new Set(["mcp-call", "cli-script", "baas-call", "action", "subworkflow"]);
const LLM_OUTPUT_EXECUTORS = new Set(["embedded-agent", "subagent"]);

/** 取一个 target 路径的末段字段名(workflowData.a.b → b),用于匹配 required 字段名。 */
function saveAsTargetField(target: string): string | undefined {
  const seg = target.replace(/^workflowData\./, "").split(".").pop();
  return seg && seg.trim() ? seg : undefined;
}

/** 收集一个节点所有「会生效的 saveAs」提供的字段名集合。 */
function liveSaveAsFields(node: WorkflowNode): Set<string> {
  const fields = new Set<string>();
  const push = (sa: Record<string, string> | undefined) => {
    if (!sa) return;
    for (const target of Object.keys(sa)) {
      const f = saveAsTargetField(target);
      if (f) fields.add(f);
    }
  };
  if (isSaveAsCapableExecutor(node)) {
    push((node.executor as { saveAs?: Record<string, string> }).saveAs);
  }
  push(node.onResult?.then?.rerun?.saveAs);
  push(node.onResult?.else?.rerun?.saveAs);
  push(node.onResult?.then?.wait?.saveAs);
  push(node.onResult?.else?.wait?.saveAs);
  return fields;
}

/** 收集一个节点所有「声明了但不会执行(dead)的 saveAs」提供的字段名集合。 */
function deadSaveAsFields(node: WorkflowNode): Set<string> {
  const ignored = (node as WorkflowNode & { _ignored?: string[] })._ignored ?? [];
  if (ignored.length === 0) return new Set();
  const fields = new Set<string>();
  const collect = (sa: Record<string, string> | undefined, tag: string) => {
    if (!sa || !ignored.includes(tag)) return;
    for (const target of Object.keys(sa)) {
      const f = saveAsTargetField(target);
      if (f) fields.add(f);
    }
  };
  collect((node.executor as { saveAs?: Record<string, string> }).saveAs, "executor.saveAs");
  collect((node as { saveAs?: Record<string, string> }).saveAs, "saveAs");
  return fields;
}

export function analyzeContractSatisfiability(workflow: WorkflowSpec): {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
} {
  const errors: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  for (const node of workflow.nodes) {
    const oc = node.outputContract;
    if (!oc || !oc.required) continue;
    const requiredFields = oc.schema?.required ?? [];
    if (requiredFields.length === 0) continue;

    const live = liveSaveAsFields(node);
    const dead = deadSaveAsFields(node);
    const skipFields = new Set(Object.keys(node.skipResult ?? {}));
    const prompt = (node.executor as { prompt?: string }).prompt ?? "";

    for (const f of requiredFields) {
      // 有合法生效的生产者 → 满足
      if (live.has(f) || skipFields.has(f)) continue;

      // 硬错误:唯一声明生产者是 dead saveAs(运行时契约必缺,静态可证)
      if (dead.has(f)) {
        errors.push({
          path: `nodes[${node.id}].outputContract.required`,
          message: `required 字段 "${f}" 的唯一声明生产者是 saveAs,但该 saveAs 不会执行(${node.executor.type} 不挂 applySaveAs)→ 契约必失败。删 saveAs,数据流转改用 {{nodeOutput.${node.id}.<field>}},或放宽 required。`,
          severity: "error",
        });
        continue;
      }

      // 软警告:外部输出型无静态保证(工具可能返回,但不保证)
      if (EXTERNAL_OUTPUT_EXECUTORS.has(node.executor.type)) {
        warnings.push({
          path: `nodes[${node.id}].outputContract.required`,
          message: `required 字段 "${f}" 依赖 ${node.executor.type} 实际返回,引擎无法静态保证。确认工具返回结构,下游用 {{nodeOutput.${node.id}.<field>}},或放宽 required。`,
          severity: "warning",
        });
        continue;
      }

      // 软警告:LLM 输出但 prompt 未提字段名
      if (LLM_OUTPUT_EXECUTORS.has(node.executor.type) && !prompt.includes(f)) {
        warnings.push({
          path: `nodes[${node.id}].outputContract.required`,
          message: `required 字段 "${f}" 未在 prompt 正文出现,LLM 可能不产。建议 prompt 写明「以 JSON 返回,含 ${f}」。`,
          severity: "warning",
        });
      }
    }
  }
  return { errors, warnings };
}

export function validateWorkflowSemantics(workflow: WorkflowSpec): { warnings: ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  const nodeIds = new Set<string>();
  const duplicateIds = new Set<string>();

  for (const node of workflow.nodes) {
    if (reservedLoopRuntimeNodeIdPattern.test(node.id)) {
      issues.push({ path: `nodes[${node.id}]`, message: "node id uses reserved loop runtime id format" });
    }
    if (nodeIds.has(node.id)) {
      duplicateIds.add(node.id);
      issues.push({ path: `nodes[${node.id}]`, message: "duplicate node id" });
    }
    nodeIds.add(node.id);
  }

  for (const node of workflow.nodes) {
    for (const dep of node.dependsOn) {
      if (!nodeIds.has(dep)) {
        issues.push({ path: `nodes[${node.id}].dependsOn`, message: `dependency "${dep}" is missing` });
      }
    }
    const isLegacyJoinAnyTriggerRule = node.join === "any" && !explicitTriggerRuleNodes.has(node);
    if (node.triggerRule === "one_success" && node.dependsOn.length === 0 && !isLegacyJoinAnyTriggerRule) {
      issues.push({ path: `nodes[${node.id}].triggerRule`, message: "one_success requires at least one dependency" });
    }
  }

  const validateHumanActionTargets = (path: string, actions: HumanGateActions | undefined): void => {
    const target = actions?.revise?.target;
    if (target && !nodeIds.has(target)) {
      issues.push({
        path: `${path}.actions.revise.target`,
        message: `target node "${target}" is missing`,
      });
    }
  };

  for (const node of workflow.nodes) {
    if (node.executor.type === "human") {
      validateHumanActionTargets(`nodes[${node.id}].executor`, node.executor.actions);
    }
    validateHumanActionTargets(`nodes[${node.id}].onResult.then.wait`, node.onResult?.then?.wait?.actions);
    validateHumanActionTargets(`nodes[${node.id}].onResult.else.wait`, node.onResult?.else?.wait?.actions);
  }

  const validateCollaborationExecutors = (
    nodes: WorkflowNode[],
    nodePath: (node: WorkflowNode) => string,
    targetIds: Set<string>,
  ): void => {
    for (const node of nodes) {
      const path = nodePath(node);
      if (node.executor.type === "collaboration") {
        const executor = node.executor;
        const hasRouteTargets = Boolean(executor.route?.to?.length);
        if (!executor.skillName?.trim() && !hasRouteTargets) {
          issues.push({
            path: `${path}.executor`,
            message: "collaboration executor requires skillName or route.to",
          });
        }
        if (executor.delivery?.collaboration?.primary === "bcs-route" && !hasRouteTargets) {
          issues.push({
            path: `${path}.executor.route.to`,
            message: 'is required when delivery.collaboration.primary is "bcs-route"',
          });
        }
        if (executor.onFeedback && !targetIds.has(executor.onFeedback.target)) {
          issues.push({
            path: `${path}.executor.onFeedback.target`,
            message: `target node "${executor.onFeedback.target}" is missing`,
          });
        }
      }

      if (node.executor.type === "loop-group") {
        const bodyIds = new Set(node.executor.body.map((bodyNode) => bodyNode.id));
        validateCollaborationExecutors(
          node.executor.body,
          (bodyNode) => `${path}.body[${bodyNode.id}]`,
          bodyIds,
        );
      }
    }
  };

  validateCollaborationExecutors(workflow.nodes, (node) => `nodes[${node.id}]`, nodeIds);

  // Validate async-callback executor nodes
  for (const node of workflow.nodes) {
    if (node.executor.type !== "async-callback") continue;
    const executor = node.executor;
    const path = `nodes[${node.id}].executor`;

    // Validate timeout format if provided
    if (executor.timeout && !/^(\d+)\s*(s|m|h|d)$/.test(executor.timeout.trim().toLowerCase())) {
      issues.push({
        path: `${path}.timeout`,
        message: `Invalid timeout format: "${executor.timeout}". Use e.g. "30m", "2h", "24h".`,
      });
    }

    // Validate auth config
    if (executor.auth) {
      if (executor.auth.mode === "hmac" && !executor.auth.secret) {
        issues.push({
          path: `${path}.auth.secret`,
          message: 'HMAC auth mode requires a secret',
        });
      }
      if (executor.auth.mode !== "hmac" && executor.auth.mode !== "x-one-id") {
        issues.push({
          path: `${path}.auth.mode`,
          message: `Unknown auth mode: "${executor.auth.mode}". Must be "hmac" or "x-one-id".`,
        });
      }
    }
  }

  for (const node of workflow.nodes) {
    if (node.executor.type !== "loop-group") continue;
    const bodyIds = new Set<string>();
    for (const bodyNode of node.executor.body) {
      if (reservedLoopRuntimeNodeIdPattern.test(bodyNode.id)) {
        issues.push({
          path: `nodes[${node.id}].body[${bodyNode.id}]`,
          message: "loop body node id uses reserved loop runtime id format",
        });
      }
      if (bodyIds.has(bodyNode.id)) {
        issues.push({ path: `nodes[${node.id}].body[${bodyNode.id}]`, message: "duplicate loop body node id" });
      }
      bodyIds.add(bodyNode.id);
    }
    for (const bodyNode of node.executor.body) {
      for (const dep of bodyNode.dependsOn) {
        if (!bodyIds.has(dep)) {
          issues.push({ path: `nodes[${node.id}].body[${bodyNode.id}].dependsOn`, message: `dependency "${dep}" must be inside the loop body` });
        }
      }
    }
    if (node.executor.until && !bodyIds.has(node.executor.until.node)) {
      issues.push({ path: `nodes[${node.id}].until.node`, message: `loop body node "${node.executor.until.node}" is missing` });
    }
  }

  const uniqueNodes = workflow.nodes.filter((node) => !duplicateIds.has(node.id));
  const byId = new Map(uniqueNodes.map((node) => [node.id, node]));
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  let cycle: string[] | undefined;

  const visit = (nodeId: string): void => {
    if (cycle || visited.has(nodeId)) return;
    if (visiting.has(nodeId)) {
      cycle = [...stack.slice(stack.indexOf(nodeId)), nodeId];
      return;
    }
    const node = byId.get(nodeId);
    if (!node) return;
    visiting.add(nodeId);
    stack.push(nodeId);
    for (const dep of node.dependsOn) {
      if (byId.has(dep)) visit(dep);
    }
    stack.pop();
    visiting.delete(nodeId);
    visited.add(nodeId);
  };

  for (const node of uniqueNodes) {
    visit(node.id);
    if (cycle) break;
  }

  if (cycle) {
    issues.push({ path: "nodes", message: `cycle detected: ${cycle.join(" -> ")}` });
  }

  // saveAs dead-location warnings:字段被 deepPreserveUnknown 保留但该 executor 不会执行。
  // 只 warning 不 throw——配置结构合法,但运行时无效,需给出正确方向(改用 nodeOutput/移到生效位置)。
  // 精度:仅当 _ignored 存在时报;capable executor(human/async-callback/approval)的 saveAs 不进 _ignored,不报。
  for (const node of workflow.nodes) {
    const ignored = (node as WorkflowNode & { _ignored?: string[] })._ignored ?? [];
    if (ignored.includes("executor.saveAs")) {
      warnings.push({
        path: `nodes[${node.id}].executor.saveAs`,
        message: `${node.executor.type} 不执行 executor.saveAs(仅 human/async-callback/approval 生效,以及 onResult.rerun/wait.saveAs)。数据流转请用 {{nodeOutput.${node.id}.<field>}},或把 saveAs 移到生效位置。`,
        severity: "warning",
      });
    }
    if (ignored.includes("saveAs")) {
      warnings.push({
        path: `nodes[${node.id}].saveAs`,
        message: `节点级 saveAs 无运行时钩子执行(写在这里不生效)。saveAs 只在 executor(human/async-callback/approval)内或 onResult.rerun/wait 内有效。`,
        severity: "warning",
      });
    }
  }

  // 契约可满足性:必败契约(唯一生产者是 dead saveAs)→ 硬错误并入 issues(throw);
  // 外部输出/LLM 无静态保证 → 软警告并入 warnings。
  const { errors: ctrErrors, warnings: ctrWarnings } = analyzeContractSatisfiability(workflow);
  for (const w of ctrWarnings) warnings.push(w);
  for (const e of ctrErrors) issues.push({ path: e.path, message: e.message, severity: "error" });

  if (issues.length > 0) {
    throw new WorkflowValidationError(issues);
  }
  return { warnings };
}

function collectHookActions(workflow: WorkflowSpec): Array<{ path: string; action: string }> {
  const refs: Array<{ path: string; action: string }> = [];
  for (const [lifecycleName, hooks] of [
    ["preflight", workflow.workflow?.preflight],
    ["onStart", workflow.workflow?.onStart],
    ["onFinish", workflow.workflow?.onFinish],
  ] as const) {
    hooks?.forEach((hook, index) => {
      refs.push({ path: `workflow.${lifecycleName}[${index}]`, action: hook.action });
    });
  }
  for (const node of workflow.nodes) {
    collectNodeActionRefs(node, `nodes.${node.id}`, refs);
    node.onSuccess?.forEach((hook, index) => {
      refs.push({ path: `nodes[${node.id}].onSuccess[${index}]`, action: hook.action });
    });
    node.validation?.actions.forEach((hook, index) => {
      refs.push({ path: `nodes[${node.id}].validation.actions[${index}]`, action: hook.action });
    });
  }
  return refs;
}

function collectNodeActionRefs(
  node: WorkflowNode,
  path: string,
  refs: Array<{ path: string; action: string }>,
): void {
  if (node.executor.type === "action") {
    refs.push({ path: `${path}.executor.action`, action: node.executor.action });
  }
  if (node.executor.type === "loop-group") {
    for (const bodyNode of node.executor.body) {
      collectNodeActionRefs(bodyNode, `${path}.body.${bodyNode.id}`, refs);
    }
  }
}

export function validateWorkflowResources(
  workflow: WorkflowSpec,
  options: { actionRegistry: ActionRegistry },
): void {
  const issues: ValidationIssue[] = [];
  for (const ref of collectHookActions(workflow)) {
    if (!options.actionRegistry.has(ref.action)) {
      issues.push({ path: ref.path, message: `action "${ref.action}" is not registered` });
    }
  }
  if (issues.length > 0) {
    throw new WorkflowValidationError(issues);
  }
}

// ── Subworkflow Validation ──

export type SubworkflowResolver = (workflowId: string, packId?: string) => WorkflowSpec | undefined;

function collectSubworkflowNodes(nodes: WorkflowNode[]): Array<{ nodeId: string; executor: SubworkflowExecutor }> {
  const result: Array<{ nodeId: string; executor: SubworkflowExecutor }> = [];
  for (const node of nodes) {
    if (node.executor.type === "subworkflow") {
      result.push({ nodeId: node.id, executor: node.executor });
    }
    if (node.executor.type === "loop-group") {
      result.push(...collectSubworkflowNodes(node.executor.body));
    }
  }
  return result;
}

export function validateSubworkflowReferences(
  workflow: WorkflowSpec,
  resolveWorkflow: SubworkflowResolver,
  currentPackId: string,
): void {
  const issues: ValidationIssue[] = [];
  const subworkflowNodes = collectSubworkflowNodes(workflow.nodes);
  for (const { nodeId, executor } of subworkflowNodes) {
    const packId = executor.packId ?? currentPackId;
    const resolved = resolveWorkflow(executor.workflowId, packId);
    if (!resolved) {
      if (executor.packId) {
        issues.push({
          path: `nodes[${nodeId}].executor`,
          message: `subworkflow references workflow "${executor.workflowId}" in pack "${executor.packId}" which is not found`,
        });
      } else {
        issues.push({
          path: `nodes[${nodeId}].executor`,
          message: `subworkflow references workflow "${executor.workflowId}" which is not found`,
        });
      }
    }
  }
  if (issues.length > 0) {
    throw new WorkflowValidationError(issues);
  }
}

export function detectCircularReferences(
  entryWorkflowId: string,
  resolveWorkflow: SubworkflowResolver,
  currentPackId: string,
): string[] | null {
  // Build adjacency list: workflowKey -> set of referenced workflowKeys
  // workflowKey includes packId for cross-pack disambiguation
  const adj = new Map<string, Set<string>>();
  const workflowKey = (id: string, packId?: string) => packId ? `${id} (${packId})` : id;

  const visit = (workflowId: string, packId?: string): void => {
    const key = workflowKey(workflowId, packId);
    if (adj.has(key)) return; // already visited for graph building
    adj.set(key, new Set());
    const spec = resolveWorkflow(workflowId, packId);
    if (!spec) return;
    for (const node of spec.nodes) {
      if (node.executor.type === "subworkflow") {
        const targetPackId = node.executor.packId ?? packId;
        const targetKey = workflowKey(node.executor.workflowId, targetPackId);
        adj.get(key)!.add(targetKey);
        visit(node.executor.workflowId, targetPackId);
      }
      if (node.executor.type === "loop-group") {
        for (const bodyNode of node.executor.body) {
          if (bodyNode.executor.type === "subworkflow") {
            const targetPackId2 = bodyNode.executor.packId ?? packId;
            const targetKey2 = workflowKey(bodyNode.executor.workflowId, targetPackId2);
            adj.get(key)!.add(targetKey2);
            visit(bodyNode.executor.workflowId, targetPackId2);
          }
        }
      }
    }
  };

  visit(entryWorkflowId, currentPackId);

  // DFS cycle detection
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  let cycle: string[] | null = null;

  const dfs = (node: string): void => {
    if (cycle || visited.has(node)) return;
    if (visiting.has(node)) {
      const cycleStart = stack.indexOf(node);
      cycle = [...stack.slice(cycleStart), node];
      return;
    }
    visiting.add(node);
    stack.push(node);
    const neighbors = adj.get(node);
    if (neighbors) {
      for (const neighbor of neighbors) {
        dfs(neighbor);
        if (cycle) return;
      }
    }
    stack.pop();
    visiting.delete(node);
    visited.add(node);
  };

  for (const node of adj.keys()) {
    dfs(node);
    if (cycle) break;
  }

  return cycle;
}

export function validateSubworkflowDepth(
  entryWorkflowId: string,
  resolveWorkflow: SubworkflowResolver,
  currentPackId: string,
): void {
  const issues: ValidationIssue[] = [];

  const checking = new Set<string>();
  const checkDepth = (workflowId: string, packId: string | undefined, depth: number, chain: string[]): void => {
    if (depth > MAX_SUBWORKFLOW_DEPTH) {
      issues.push({
        path: "nodes",
        message: `subworkflow nesting depth exceeds maximum (${MAX_SUBWORKFLOW_DEPTH}): ${chain.join(" -> ")}`,
      });
      return;
    }
    const visitKey = packId ? `${workflowId} (${packId})` : workflowId;
    if (checking.has(visitKey)) return; // cycle already detected by detectCircularReferences
    checking.add(visitKey);
    const spec = resolveWorkflow(workflowId, packId);
    if (!spec) return;
    for (const node of spec.nodes) {
      if (node.executor.type === "subworkflow") {
        const targetPackId = node.executor.packId ?? packId;
        const nextChain = [...chain, node.executor.workflowId];
        checkDepth(node.executor.workflowId, targetPackId, depth + 1, nextChain);
      }
      if (node.executor.type === "loop-group") {
        for (const bodyNode of node.executor.body) {
          if (bodyNode.executor.type === "subworkflow") {
            const targetPackId2 = bodyNode.executor.packId ?? packId;
            const nextChain = [...chain, bodyNode.executor.workflowId];
            checkDepth(bodyNode.executor.workflowId, targetPackId2, depth + 1, nextChain);
          }
        }
      }
    }
  };

  checkDepth(entryWorkflowId, currentPackId, 0, [entryWorkflowId]);

  if (issues.length > 0) {
    throw new WorkflowValidationError(issues);
  }
}

// ── Mock & Test Framework Validation ──

export function normalizeMockConfig(raw: unknown, ctx: string): MockConfig | undefined {
  if (raw == null || typeof raw !== "object") return undefined;
  const obj = raw as Record<string, unknown>;
  const mock: MockConfig = {};

  if (obj.output !== undefined) {
    if (!isPlainRecord(obj.output)) {
      fail(ctx, "mock.output must be an object");
    }
    mock.output = obj.output as Record<string, unknown>;
  }

  if (obj.error !== undefined) {
    if (typeof obj.error !== "string") {
      fail(ctx, "mock.error must be a string");
    }
    mock.error = obj.error as string;
  }

  if (obj.timeout !== undefined) {
    if (typeof obj.timeout !== "boolean") {
      fail(ctx, "mock.timeout must be a boolean");
    }
    mock.timeout = obj.timeout as boolean;
  }

  if (obj.delay !== undefined) {
    if (typeof obj.delay !== "number" || obj.delay < 0 || !Number.isFinite(obj.delay)) {
      fail(ctx, "mock.delay must be a non-negative number");
    }
    mock.delay = obj.delay as number;
  }

  if (obj.autoConfirm !== undefined) {
    if (typeof obj.autoConfirm !== "boolean") {
      fail(ctx, "mock.autoConfirm must be a boolean");
    }
    mock.autoConfirm = obj.autoConfirm as boolean;
  }

  if (obj.maxIterations !== undefined) {
    if (typeof obj.maxIterations !== "number" || !Number.isInteger(obj.maxIterations) || obj.maxIterations < 1) {
      fail(ctx, "mock.maxIterations must be a positive integer");
    }
    mock.maxIterations = obj.maxIterations as number;
  }

  if (Object.keys(mock).length === 0) return undefined;
  return mock;
}

function normalizeAssertion(raw: unknown, ctx: string): Assertion {
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const obj = raw as Record<string, unknown>;

  const hasNodeId = typeof obj.nodeId === "string";
  const hasVariable = typeof obj.variable === "string";
  const hasStatus = obj.status !== undefined;
  const hasOutput = obj.output !== undefined;

  if (hasNodeId && hasStatus && !hasOutput && !hasVariable) {
    const validStatuses = new Set([
      "pending", "running", "postActionsRunning", "waiting",
      "succeeded", "failed", "blocked", "skipped",
    ]);
    if (!validStatuses.has(obj.status as string)) {
      fail(`${ctx}.status`, `invalid node status "${obj.status}"`);
    }
    return { nodeId: obj.nodeId as string, status: obj.status as "pending" | "running" | "postActionsRunning" | "waiting" | "succeeded" | "failed" | "blocked" | "skipped" } as Assertion;
  }

  if (hasNodeId && hasOutput && !hasVariable) {
    if (!isPlainRecord(obj.output)) {
      fail(`${ctx}.output`, "must be an object");
    }
    const output = obj.output as Record<string, unknown>;
    const validMatchers: Set<string> = new Set(["equals", "contains", "matches", "type", "exists"]);
    for (const key of Object.keys(output)) {
      if (!validMatchers.has(key)) {
        fail(`${ctx}.output.${key}`, `unknown assertion matcher "${key}"`);
      }
    }
    return { nodeId: obj.nodeId as string, output } as Assertion;
  }

  if (hasVariable && !hasNodeId) {
    const matcher: Record<string, unknown> = {};
    const validMatchers: Set<string> = new Set(["equals", "contains", "matches", "type", "exists"]);
    for (const key of Object.keys(obj)) {
      if (key === "variable" || key === "description") continue;
      if (!validMatchers.has(key)) {
        fail(`${ctx}.${key}`, `unknown assertion matcher "${key}"`);
      }
      matcher[key] = obj[key];
    }
    return { variable: obj.variable as string, ...matcher } as Assertion;
  }

  fail(ctx, "assertion must have (nodeId + output), (nodeId + status), or (variable + matcher)");
}

function normalizeTestCase(raw: unknown, ctx: string): TestCase {
  if (!isPlainRecord(raw)) {
    fail(ctx, "must be an object");
  }
  const obj = raw as Record<string, unknown>;

  const name = requireString(obj, "name", ctx);
  const description = optionalString(obj, "description");

  let params: Record<string, unknown> | undefined;
  if (obj.params !== undefined) {
    if (!isPlainRecord(obj.params)) {
      fail(`${ctx}.params`, "must be an object");
    }
    params = obj.params as Record<string, unknown>;
  }

  let mockOverrides: Record<string, MockConfig> | undefined;
  if (obj.mockOverrides !== undefined) {
    if (!isPlainRecord(obj.mockOverrides)) {
      fail(`${ctx}.mockOverrides`, "must be an object");
    }
    const overrides = obj.mockOverrides as Record<string, unknown>;
    mockOverrides = {};
    for (const [nodeId, mockRaw] of Object.entries(overrides)) {
      mockOverrides[nodeId] = normalizeMockConfig(mockRaw, `${ctx}.mockOverrides.${nodeId}`)!;
    }
  }

  if (!Array.isArray(obj.assertions) || obj.assertions.length === 0) {
    fail(`${ctx}.assertions`, "must be a non-empty array");
  }
  const assertions = (obj.assertions as unknown[]).map(
    (a, i) => normalizeAssertion(a, `${ctx}.assertions[${i}]`),
  );

  return { name, description, params, mockOverrides, assertions };
}

export function normalizeTestCases(raw: unknown, ctx: string): TestCase[] | undefined {
  if (raw == null) return undefined;
  if (!Array.isArray(raw)) {
    fail(ctx, '"tests" must be an array');
  }
  if (raw.length === 0) return [];
  return raw.map((item, i) => normalizeTestCase(item, `${ctx}[${i}]`));
}

export function validateMockFields(workflow: WorkflowSpec): ValidationWarning[] {
  const warnings: ValidationWarning[] = [];

  for (const node of workflow.nodes) {
    if (node.mock) {
      if ((node.executor as { type: string }).type === "approval") {
        throw new WorkflowValidationError([{
          path: `nodes[${node.id}].mock`,
          message: "approval executor does not support mock",
        }]);
      }
      if (node.mock.output !== undefined && !isPlainRecord(node.mock.output)) {
        throw new WorkflowValidationError([{
          path: `nodes[${node.id}].mock.output`,
          message: `must be an object, got ${Array.isArray(node.mock.output) ? "array" : typeof node.mock.output}`,
        }]);
      }
    }
  }

  return warnings;
}

export function validateTestCases(workflow: WorkflowSpec): ValidationWarning[] {
  const warnings: ValidationWarning[] = [];

  if (!workflow.tests) return warnings;

  if (workflow.tests.length === 0) {
    warnings.push({
      path: "tests",
      message: "no test cases defined",
    });
    return warnings;
  }

  const nodeIds = new Set(workflow.nodes.map((n) => n.id));

  for (const tc of workflow.tests) {
    if (tc.mockOverrides) {
      for (const nodeId of Object.keys(tc.mockOverrides)) {
        if (!nodeIds.has(nodeId)) {
          warnings.push({
            path: `tests[${tc.name}].mockOverrides.${nodeId}`,
            message: `node "${nodeId}" not found in workflow`,
          });
        }
      }
    }

    for (const assertion of tc.assertions) {
      if ("nodeId" in assertion && !nodeIds.has(assertion.nodeId)) {
        warnings.push({
          path: `tests[${tc.name}].assertions`,
          message: `node "${assertion.nodeId}" not found in workflow`,
        });
      }
    }
  }

  return warnings;
}

export function validateSubworkflowComposition(
  workflow: WorkflowSpec,
  resolveWorkflow: SubworkflowResolver,
  currentPackId: string,
): void {
  const subworkflowNodes = collectSubworkflowNodes(workflow.nodes);
  if (subworkflowNodes.length === 0) return;

  validateSubworkflowReferences(workflow, resolveWorkflow, currentPackId);
  const cycle = detectCircularReferences(workflow.id, resolveWorkflow, currentPackId);
  if (cycle) {
    throw new WorkflowValidationError([{
      path: "nodes",
      message: `circular subworkflow reference detected: ${cycle.join(" -> ")}`,
    }]);
  }
  validateSubworkflowDepth(workflow.id, resolveWorkflow, currentPackId);
}
