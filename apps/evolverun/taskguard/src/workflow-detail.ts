import type { FlowState, TriggerRule, WorkflowNode, WorkflowSpec } from "./types.js";
import type { ResolvedWorkflow } from "./packs/types.js";

export function toMermaidNodeId(id: string): string {
  const safeId = id.replace(/[^a-zA-Z0-9_]/gu, "_");
  if (/^[a-zA-Z_]/u.test(safeId)) return safeId;
  return `node_${safeId}`;
}

function escapeMermaidLabel(value: string): string {
  return value.replace(/\\/gu, "\\\\").replace(/\n/gu, "\\n").replace(/"/gu, '\\"');
}

function resolveTriggerRule(node: WorkflowNode): TriggerRule {
  if (node.triggerRule) return node.triggerRule;
  if (node.join === "any") return "one_success";
  return "all_success";
}

function listOrDash(values: string[]): string {
  return values.length > 0 ? values.join(", ") : "-";
}

function renderIdentity(workflow: WorkflowSpec): string[] {
  if (!workflow.identity) return [];

  const identity = workflow.identity;
  return [
    "**Identity:**",
    `key=${identity.key} | label=${identity.label ?? "-"} | duplicatePolicy=${identity.duplicatePolicy ?? "allow"}`,
    "",
  ];
}

function renderOutputs(workflow: WorkflowSpec): string[] {
  if (!workflow.outputs || Object.keys(workflow.outputs).length === 0) return [];

  return [
    "**Outputs:**",
    ...Object.entries(workflow.outputs).map(([name, spec]) => (
      `- ${name} | from=${spec.from} | public=${spec.public === true ? "true" : "false"} | description=${spec.description ?? "-"}`
    )),
    "",
  ];
}

function renderPreflight(workflow: WorkflowSpec): string[] {
  const preflight = workflow.workflow?.preflight ?? [];
  if (preflight.length === 0) return [];

  return [
    "**Preflight:**",
    ...preflight.map((action, index) => {
      const id = action.id ?? `preflight-${index + 1}`;
      const abortIf = action.abortIf?.empty === true ? " | abortIf.empty=true" : "";
      return `- ${id} | action=${action.action} | required=${action.required === false ? "false" : "true"}${abortIf}`;
    }),
    "",
  ];
}

export function renderWorkflowMermaid(workflow: WorkflowSpec): string {
  const lines = ["```mermaid", "flowchart TD"];

  for (const node of workflow.nodes) {
    const nodeId = toMermaidNodeId(node.id);
    const label = escapeMermaidLabel(`${node.phase} ${node.title}\n${node.executor.type}`);
    lines.push(`  ${nodeId}["${label}"]`);
  }

  for (const node of workflow.nodes) {
    const targetId = toMermaidNodeId(node.id);
    for (const depId of node.dependsOn) {
      lines.push(`  ${toMermaidNodeId(depId)} --> ${targetId}`);
    }
  }

  lines.push("```");
  return lines.join("\n");
}

function formatSubworkflowNodeDetail(node: WorkflowNode): string {
  if (node.executor.type !== "subworkflow") return "";
  const executor = node.executor as import("./types.js").SubworkflowExecutor;
  const parts: string[] = [`workflowId=${executor.workflowId}`];
  if (executor.packId) parts.push(`packId=${executor.packId}`);
  if (executor.onFailure && executor.onFailure !== "fail") parts.push(`onFailure=${executor.onFailure}`);
  return ` | ${parts.join(" | ")}`;
}

export function renderWorkflowDetail(workflow: WorkflowSpec, resolved?: ResolvedWorkflow): string {
  const phases = Array.from(new Set(workflow.nodes.map((node) => node.phase)));
  const lines = [
    `**${workflow.id}** - ${workflow.title}`,
    `节点数: ${workflow.nodes.length} | 阶段: ${listOrDash(phases)}`,
    ...(resolved ? [
      `来源: ${resolved.source.kind} | pack=${resolved.pack.id}@${resolved.pack.version} | digest=${resolved.digest}`,
      `路径: ${resolved.absolutePath}`,
    ] : []),
    "",
    ...renderIdentity(workflow),
    ...renderPreflight(workflow),
    ...renderOutputs(workflow),
    "**Workflow 定义图:**",
    renderWorkflowMermaid(workflow),
    "",
    "**节点清单:**",
  ];

  for (const node of workflow.nodes) {
    const dependsOn = listOrDash(node.dependsOn);
    const onSuccess = listOrDash((node.onSuccess ?? []).map((action) => action.id));
    lines.push(
      `- ${node.id} | ${node.phase} | ${node.title} | executor=${node.executor.type}${formatSubworkflowNodeDetail(node)} | dependsOn=${dependsOn} | triggerRule=${resolveTriggerRule(node)} | onSuccess=${onSuccess}`,
    );
  }

  return lines.join("\n");
}
