import type {
  WorkflowNode,
  ApprovalExecutor,
  FlowState,
  WorkflowSpec,
  CardFieldDef,
  WorkflowApprover,
  CardSectionDef,
} from "./types.js";
import type { TemplateContext } from "./runner.js";
import { resolveTemplate } from "./runner.js";
import { getLegacyApprovalExecutor } from "./legacy-runtime.js";
import { loadConfig } from "./config/loader.js";

export function buildApprovalCardData(params: {
  node: WorkflowNode;
  executor: ApprovalExecutor;
  templateCtx: TemplateContext;
  flowState: FlowState;
  workflow: WorkflowSpec;
  flowId: string;
}): Record<string, unknown> {
  const { node, executor, templateCtx, flowState, workflow, flowId } = params;

  const fields = resolveCardFields(executor, templateCtx);
  const sections = resolveCardSections(executor, templateCtx);
  const approvers = resolveApprovers(executor);
  const actions = [
    { key: "approve", label: "同意", type: "primary", icon: "✅" },
    { key: "reject", label: "不同意", type: "danger", icon: "❌" },
  ];
  const clawwebBase = loadConfig().app.api.clawwebUrl;
  const workflowDetailUrl = executor.workflowUrl
    ? String(formatFieldValue(resolveTemplate(executor.workflowUrl, templateCtx)) ?? "")
    : `${clawwebBase}/runs/${flowId}`;

  return {
    title: formatFieldValue(resolveTemplate(executor.message, templateCtx)),
    status: "waiting",
    statusLabel: executor.statusLabel ? formatFieldValue(resolveTemplate(executor.statusLabel, templateCtx)) : "待审批",
    applicant: extractApplicant(flowState),
    department: (flowState.params?.department as string) ?? "",
    time: formatCurrentTime(),
    workflowTitle: workflow.title ?? node.title,
    flowId,
    nodeId: node.id,
    fields,
    sections,
    approvers,
    actions,
    workflowDetailUrl,
    approvalType: executor.approvalType,
    cardTitle: executor.cardTitle ? formatFieldValue(resolveTemplate(executor.cardTitle, templateCtx)) : undefined,
    actionLabel: executor.actionLabel ? formatFieldValue(resolveTemplate(executor.actionLabel, templateCtx)) : undefined,
  };
}

function resolveCardFields(
  executor: ApprovalExecutor,
  templateCtx: TemplateContext,
): Array<{ label: string; value: string }> {
  if (executor.cardFields && executor.cardFields.length > 0) {
    return executor.cardFields.map((f: CardFieldDef) => ({
      label: f.label,
      value: formatFieldValue(resolveTemplate(f.value, templateCtx)),
    }));
  }
  return autoExtractFields(templateCtx);
}

function resolveCardSections(
  executor: ApprovalExecutor,
  templateCtx: TemplateContext,
): Array<Record<string, unknown>> | undefined {
  if (!executor.cardSections || executor.cardSections.length === 0) {
    return undefined;
  }
  return executor.cardSections.map((section: CardSectionDef) => ({
    id: section.id,
    title: section.title,
    icon: section.icon,
    description: section.description
      ? formatFieldValue(resolveTemplate(section.description, templateCtx))
      : undefined,
    style: section.style ?? "default",
    fields: section.fields.map((field) => ({
      id: field.id,
      label: field.label,
      expected: field.expected ? formatFieldValue(resolveTemplate(field.expected, templateCtx)) : undefined,
      expectedLabel: field.expectedLabel ?? "AI推荐",
      actual: field.actual ? formatFieldValue(resolveTemplate(field.actual, templateCtx)) : undefined,
      actualLabel: field.actualLabel ?? "当前",
      actions: field.actions
        ? field.actions.map((action) => ({
            key: action.key,
            label: action.label,
            type: action.type,
            autoFill: action.autoFill
              ? formatFieldValue(resolveTemplate(action.autoFill, templateCtx))
              : undefined,
          }))
        : undefined,
      customizable: field.customizable ?? false,
      placeholder: field.placeholder,
    })),
  }));
}

function resolveApprovers(
  executor: ApprovalExecutor,
): Array<{ name: string; empId: string }> {
  if (executor.approvers && executor.approvers.length > 0) {
    return executor.approvers.map((a: WorkflowApprover) => ({
      name: a.name,
      empId: a.empId,
    }));
  }
  return [];
}

function extractApplicant(flowState: FlowState): string {
  return (
    (flowState.params?.applicant as string)
    ?? (flowState.params?.userName as string)
    ?? "系统"
  );
}

function formatCurrentTime(): string {
  return new Date().toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).replace(/\//g, "-");
}

// ── Smart field formatting: turn JSON arrays/objects into readable text ──

function formatFieldValue(raw: string): string {
  const trimmed = raw.trim();
  let formatted: string;
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      formatted = formatParsedValue(JSON.parse(trimmed));
    } catch {
      formatted = formatEmbeddedJsonBlocks(raw);
    }
  } else {
    formatted = formatEmbeddedJsonBlocks(raw);
  }
  // Unescape literal \n sequences so that newlines from LLM JSON outputs
  // (e.g. review_report) render correctly in card fields and messages.
  // This does NOT affect normal YAML block-scalar line breaks because they
  // are already actual newlines, not the two-character "\n" sequence.
  return formatted.replace(/\\n/g, "\n");
}

function formatEmbeddedJsonBlocks(text: string): string {
  // Scan for JSON array blocks starting with '['. For each '[' try extending
  // to the matching ']' position that yields a valid JSON.parse result.
  let result = "";
  let i = 0;
  while (i < text.length) {
    if (text[i] === "[") {
      let found = false;
      for (let j = i + 1; j <= text.length; j++) {
        if (text[j] === "]") {
          const candidate = text.slice(i, j + 1);
          try {
            const parsed = JSON.parse(candidate);
            if (Array.isArray(parsed)) {
              result += formatParsedValue(parsed);
              i = j + 1;
              found = true;
              break;
            }
          } catch {
            // continue searching
          }
        }
      }
      if (!found) {
        result += text[i];
        i++;
      }
    } else {
      result += text[i];
      i++;
    }
  }
  return result;
}

function formatParsedValue(value: unknown): string {
  if (Array.isArray(value)) {
    if (value.length === 0) return "无";
    return value.map((item, idx) => `${idx + 1}. ${formatItem(item)}`).join("\n");
  }
  if (value != null && typeof value === "object") {
    return formatItem(value);
  }
  return String(value ?? "");
}

function formatItem(item: unknown): string {
  if (item == null || typeof item !== "object") return String(item ?? "");
  const obj = item as Record<string, unknown>;
  const parts: string[] = [];
  if (obj.check_name) parts.push(`【${obj.check_name}】`);
  if (obj.suggestion) parts.push(String(obj.suggestion));
  if (parts.length > 0) return parts.join(" ");

  // Generic fallback: show readable key-value pairs, skip internal fields
  return Object.entries(obj)
    .filter(([k]) => !["check_id", "category", "severity", "current_value", "missing_type"].includes(k))
    .map(([k, v]) => `${k}: ${v}`)
    .join(" | ");
}

const FIELD_MAPPINGS: Record<string, string> = {
  budgetTitle: "方案名称",
  budgetAmount: "预算金额",
  budgetPeriod: "活动周期",
  projectName: "项目名称",
  amount: "金额",
  description: "描述",
  requestTitle: "申请标题",
  requestAmount: "申请金额",
  reason: "原因",
};

function autoExtractFields(templateCtx: TemplateContext): Array<{ label: string; value: string }> {
  const data = (templateCtx.workflowData ?? {}) as Record<string, unknown>;
  const fields: Array<{ label: string; value: string }> = [];
  for (const [key, label] of Object.entries(FIELD_MAPPINGS)) {
    if (data[key] !== undefined && data[key] !== null && data[key] !== "") {
      fields.push({ label, value: String(data[key]) });
    }
  }
  return fields.length > 0 ? fields : [{ label: "审批详情", value: "见上方描述" }];
}

export function renderAixUICard(cardId: string, data: Record<string, unknown>): string {
  return `<AixUI cardId="${cardId}">\n\n${JSON.stringify(data, null, 2)}\n\n</AixUI>`;
}

/**
 * Build the cardParamMap for the DingTalk interactive approval card template.
 *
 * Template ID: 733f3eb6-df4b-4b97-a8ff-621c0963bed6.schema
 * Variable names must match the template exactly.
 *
 * This produces the variable map used by `createAndDeliverApprovalCard()`
 * and `updateApprovalCardVariables()` in the card service.
 */
export function buildDingTalkCardVariables(data: Record<string, unknown>): Record<string, string> {
  const fields = Array.isArray(data.fields) ? data.fields as Array<{ label: string; value: string }> : [];
  const approvers = Array.isArray(data.approvers) ? data.approvers as Array<{ name: string }> : [];
  const applicant = String(data.applicant ?? "系统");
  const approverList = approvers.map((a) => a.name).join("、");

  // Build content with fields, applicant, and approver info.
  // The template's BaseText component only renders the `content` variable,
  // so we include all display information here (BaseText does NOT render Markdown).
  const contentLines: string[] = [];
  for (const f of fields) {
    contentLines.push(`${f.label}: ${f.value}`);
  }
  if (applicant) {
    contentLines.push(`申请人: ${applicant}`);
  }
  if (approverList) {
    contentLines.push(`审批人: ${approverList}`);
  }

  return {
    title: String(data.title ?? "审批通知"),
    status: String(data.status ?? "waiting"),
    statusLabel: String(data.statusLabel ?? "⏳ 待审批"),
    applicant,
    workflowTitle: String(data.workflowTitle ?? ""),
    content: contentLines.join("\n"),
    approverList,
    approveAction: "true",
    rejectAction: "true",
    resultText: "",
    workflowDetailUrl: String(data.workflowDetailUrl ?? ""),
  };
}