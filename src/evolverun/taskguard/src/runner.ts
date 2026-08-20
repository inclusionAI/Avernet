import type {
  WorkflowSpec,
  WorkflowNode,
  NodeState,
  NodeOnResult,
  NodeOnResultBranch,
  NodeOnResultRerun,
  HumanWaitSpec,
  ResultCondition,
  FlowState,
} from "./types.js";

// ── Template Resolution ──

export type TemplateContext = {
  skillRoot: string;
  nodeOutput: Record<string, Record<string, unknown>>;
  [key: string]: unknown;
};

export type TemplatePathReader = (
  path: string,
  context: unknown,
) => unknown;

function splitTemplatePath(path: string): string[] {
  return path.replace(/\[(\d+)\]/g, ".$1").split(".");
}

function getContextRawValue(path: string, context: unknown): unknown {
  const parts = splitTemplatePath(path);
  let current: unknown = context;
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

/** Read a display template path without invoking accessors at any depth. */
export function readTemplatePathByDescriptor(
  path: string,
  context: unknown,
): unknown {
  const parts = splitTemplatePath(path);
  let current: unknown = context;
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    const descriptor = Object.getOwnPropertyDescriptor(current, part);
    if (!descriptor) return undefined;
    if (!("value" in descriptor)) return "[Accessor]";
    current = descriptor.value;
  }
  return current;
}

function formatTemplateValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export type TemplateValueFormatter = (
  path: string,
  value: unknown,
) => string;

const TEMPLATE_PATTERN = /\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)(?:\s*\|\s*default:\s*([^}]+))?\}\}/g;

function resolveTemplateInternal(
  template: string,
  context: TemplateContext,
  formatter: TemplateValueFormatter,
  reader: TemplatePathReader,
): string {
  return template.replace(
    TEMPLATE_PATTERN,
    (_match, path: string, fallbackExpr?: string) => {
      const rawValue = reader(path, context);
      if (rawValue !== null && rawValue !== undefined) {
        return formatter(path, rawValue);
      }
      if (fallbackExpr == null) return "";

      const normalizedFallback = fallbackExpr.trim();
      if (
        (normalizedFallback.startsWith('"')
          && normalizedFallback.endsWith('"'))
        || (normalizedFallback.startsWith("'")
          && normalizedFallback.endsWith("'"))
      ) {
        return normalizedFallback.slice(1, -1);
      }
      if (normalizedFallback === "true") return "true";
      if (normalizedFallback === "false") return "false";

      const numericFallback = Number(normalizedFallback);
      if (!Number.isNaN(numericFallback)) return String(numericFallback);

      const fallbackValue = reader(
        normalizedFallback,
        context,
      );
      if (fallbackValue === null || fallbackValue === undefined) return "";
      return formatter(normalizedFallback, fallbackValue);
    },
  );
}

export function resolveTemplate(
  template: string,
  context: TemplateContext,
): string {
  return resolveTemplateInternal(
    template,
    context,
    (_path, value) => formatTemplateValue(value),
    getContextRawValue,
  );
}

export function resolveTemplateWithFormatter(
  template: string,
  context: TemplateContext,
  formatter: TemplateValueFormatter,
  reader: TemplatePathReader = getContextRawValue,
): string {
  return resolveTemplateInternal(template, context, formatter, reader);
}

// 锚定到整串:只有 token 外仅剩空白时才算"单模板",可安全透传原生类型。
const SINGLE_TEMPLATE_PATTERN = /^\s*\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)(?:\s*\|\s*default:\s*([^}]+))?\}\}\s*$/;

/**
 * 若 `template` 整体恰好是一个 `{{path}}` token(无拼接、无多余文本),解析成原生 JS 值并
 * **保留类型**(number/boolean/object/array),供 mcp-call 透传给对端 MCP 工具。
 *
 * 背景:`resolveTemplate` 把任何值经 `formatTemplateValue` 的 `String(value)` 拍成字符串——
 * 对拼接/展示场景是对的,但 mcp-call 的入参要对端 inputSchema 校验,`{{nodeOutput.x.docId}}`
 * (number)被拍成 "123" 后,要 number 的对端(如 skylark_doc_detail)直接拒。单模板 arg 透传
 * 原值即可根治,且不碰 `resolveTemplate` 的 30+ 调用点签名,对纯字符串/拼接场景零影响。
 *
 * 返回 `null` = 不是单模板(token 外有文本或多个 token)→ 调用方回退 `resolveTemplate` 字符串路径。
 * 返回 `{ value }` = 单模板;`value` 为解析到的原值(number 0 / boolean false / "" 等 falsy
 * 原值照常返回)。仅当原值缺失且无 default 时 `value === undefined` → 调用方回退字符串路径
 * (保持历史"缺省=空串"行为,不把字段丢成 undefined)。
 */
export function tryResolveSingleTemplate(
  template: string,
  context: TemplateContext,
): { value: unknown } | null {
  const m = SINGLE_TEMPLATE_PATTERN.exec(template);
  if (!m) return null;
  const path = m[1];
  const fallbackExpr = m[2];

  const raw = getContextRawValue(path, context);
  if (raw !== null && raw !== undefined) return { value: raw };
  if (fallbackExpr == null) return { value: undefined };

  // default 也尽量保留类型(对端要 number/boolean 时,default 同样不能拍成 string)。
  const fb = fallbackExpr.trim();
  if ((fb.startsWith('"') && fb.endsWith('"')) || (fb.startsWith("'") && fb.endsWith("'"))) {
    return { value: fb.slice(1, -1) };
  }
  if (fb === "true") return { value: true };
  if (fb === "false") return { value: false };
  const num = Number(fb);
  if (!Number.isNaN(num)) return { value: num };
  const fbRaw = getContextRawValue(fb, context);
  if (fbRaw !== null && fbRaw !== undefined) return { value: fbRaw };
  return { value: undefined };
}

// ── Ready Node Computation ──

function resolveTriggerRule(node: WorkflowNode) {
  if (node.triggerRule) return node.triggerRule;
  if (node.join === "any") return "one_success";
  return "all_success";
}

function isTerminalStatus(status: NodeState["status"] | undefined): boolean {
  return status === "succeeded" || status === "failed" || status === "rejected" || status === "skipped";
}

/**
 * Walk up the dependency chain to find whether a node's `branchId` is
 * active — i.e., some ancestor with `onResult.branches` produced a
 * `matchedBranchId` that equals this node's `branchId`.
 *
 * This handles the common branch chain pattern:
 *   A (onResult, matchedBranchId="kb-hit")
 *     → B (branchId="kb-hit", no onResult, no matchedBranchId)
 *       → C (branchId="kb-hit")
 * B does NOT have matchedBranchId set (it has no onResult), but C must
 * still see the branch as active by tracing through B back to A.
 *
 * Returns:
 *   true  — branch origin found and it matched this branchId
 *   false — branch origin found but matched a DIFFERENT branchId
 *   undefined — no branch origin found yet, or origin still running
 */
function isBranchMatched(
  branchId: string,
  node: WorkflowNode,
  nodeStates: Record<string, NodeState>,
  nodeById: Map<string, WorkflowNode>,
  visited: Set<string>,
): boolean | undefined {
  if (visited.has(node.id)) return undefined; // cycle guard
  visited.add(node.id);

  for (const depId of node.dependsOn) {
    const depNode = nodeById.get(depId);
    if (!depNode) continue;
    const depState = nodeStates[depId];

    // Direct match on this dependency
    if (depState?.matchedBranchId === branchId) {
      return true;
    }
    // This dependency matched a different branch → our branch is not active
    if (depState?.matchedBranchId != null && depState.matchedBranchId !== branchId) {
      // But only count it if this dep is a branch-producing node or in our chain
      if (depNode.onResult?.branches?.length || depNode.branchId) {
        return false;
      }
    }

    // Dependency has onResult.branches but is still running → can't decide
    if ((depNode.onResult?.branches?.length ?? 0) > 0 && depState && !isTerminalStatus(depState.status)) {
      return undefined;
    }

    // Dependency is skipped with the same branchId → branch was not matched
    // (propagate skip: a skipped same-branch node means this branch is inactive)
    if (depState?.status === "skipped" && depNode.branchId === branchId) {
      return false;
    }

    // Dependency is a non-branch-producing succeeded node → walk further up
    if (depState?.status === "succeeded" && !depNode.onResult?.branches?.length && depState.matchedBranchId == null) {
      const upstream = isBranchMatched(branchId, depNode, nodeStates, nodeById, new Set(visited));
      if (upstream !== undefined) return upstream;
    }
  }

  return undefined;
}

/**
 * For a node that declares `branchId`, determine whether its branch is
 * active by checking if any dependency (direct or transitive) originated
 * from a matching onResult branch evaluation.
 *
 * Returns:
 *   true  — the branch for this node's branchId is active
 *   false — the branch for this node's branchId is NOT active (another matched)
 *   undefined — can't decide yet (origin still running, or no origin found)
 */
function branchOriginMatched(
  node: WorkflowNode,
  nodeStates: Record<string, NodeState>,
  nodeById: Map<string, WorkflowNode>,
): boolean | undefined {
  // Start with an empty visited set — isBranchMatched will add node.id
  // on its first call as a cycle guard, THEN iterate the node's deps.
  return isBranchMatched(node.branchId!, node, nodeStates, nodeById, new Set());
}

function isTriggerSatisfied(
  node: WorkflowNode,
  nodeStates: Record<string, NodeState>,
): boolean {
  if (node.dependsOn.length === 0) return true;
  const depStates = node.dependsOn.map((depId) => nodeStates[depId]?.status);
  const rule = resolveTriggerRule(node);
  if (rule === "one_success") return depStates.some((status) => status === "succeeded");
  if (rule === "all_done") return depStates.every(isTerminalStatus);
  return depStates.every((status) => status === "succeeded");
}

function canTriggerStillSucceed(
  node: WorkflowNode,
  nodeStates: Record<string, NodeState>,
): boolean {
  if (node.dependsOn.length === 0) return true;
  const depStates = node.dependsOn.map((depId) => nodeStates[depId]?.status);
  const rule = resolveTriggerRule(node);
  if (rule === "one_success") {
    return depStates.some((status) => status === "succeeded" || !isTerminalStatus(status));
  }
  if (rule === "all_done") {
    // If all dependencies have reached a terminal state, the trigger
    // either is already satisfied or can never be satisfied — no more
    // state changes will occur. Return the actual satisfaction status
    // instead of the overly-optimistic `true`.
    const allDepsTerminal = depStates.every(isTerminalStatus);
    if (allDepsTerminal) return isTriggerSatisfied(node, nodeStates);
    return true;
  }
  return depStates.every((status) => status === "succeeded" || !isTerminalStatus(status));
}

export function findSkippableNodes(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
): WorkflowNode[] {
  const nodeById = new Map(workflow.nodes.map((n) => [n.id, n]));

  return workflow.nodes.filter((node) => {
    const state = nodeStates[node.id];
    if (state?.status !== "pending") return false;

    // Branch-aware skip: if this node declares a branchId, check whether
    // any of its dependencies (direct or transitive) matched that branch.
    // Skip only when all branch-producing dependencies are terminal AND
    // none matched.
    if (node.branchId) {
      const anyDepIncomplete = node.dependsOn.some((depId) => {
        const depState = nodeStates[depId];
        // No state entry = pending (not yet started), still can't decide
        if (!depState) return true;
        return !isTerminalStatus(depState.status);
      });
      if (anyDepIncomplete) return false; // can't decide yet — don't skip

      // Fast path: direct dependency matched
      const depMatchedBranch = node.dependsOn.some((depId) => {
        const depState = nodeStates[depId];
        return depState?.status === "succeeded"
          && depState.matchedBranchId === node.branchId;
      });
      if (depMatchedBranch) return false; // branch is active — don't skip

      // Transitive: walk up the dependency chain to find the branch origin
      const upstreamMatched = branchOriginMatched(node, nodeStates, nodeById);
      if (upstreamMatched === true) return false; // branch is active — don't skip
      if (upstreamMatched === undefined) return false; // can't decide yet — don't skip

      // All deps terminal, no branch match found — skip
      return true;
    }

    return !isTriggerSatisfied(node, nodeStates)
      && !canTriggerStillSucceed(node, nodeStates);
  });
}

export function findSkippableNodesFixedPoint(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
): WorkflowNode[] {
  const resolvedStates: Record<string, NodeState> = { ...nodeStates };
  const skippableNodes: WorkflowNode[] = [];

  while (true) {
    const nextSkippableNodes = findSkippableNodes(workflow, resolvedStates);
    if (nextSkippableNodes.length === 0) break;

    for (const node of nextSkippableNodes) {
      resolvedStates[node.id] = {
        ...resolvedStates[node.id],
        status: "skipped",
      };
      skippableNodes.push(node);
    }
  }

  return skippableNodes;
}

export function getReadyNodes(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
): WorkflowNode[] {
  const nodeById = new Map(workflow.nodes.map((n) => [n.id, n]));
  const ready: WorkflowNode[] = [];

  for (const node of workflow.nodes) {
    const state = nodeStates[node.id];
    if (state && state.status !== "pending") continue;

    if (!isTriggerSatisfied(node, nodeStates)) continue;

    // Branch guard: a node that declares a branchId should only be
    // scheduled if its branch is active (the onResult origin matched
    // this branchId). This prevents wrong-branch nodes from being
    // dispatched when their non-branch dependencies happen to succeed.
    if (node.branchId) {
      const upstreamMatched = branchOriginMatched(node, nodeStates, nodeById);
      if (upstreamMatched === false) continue; // branch not active — skip
      // upstreamMatched === true → branch active, allow scheduling
      // upstreamMatched === undefined → can't decide yet, allow scheduling
      //   (will be re-evaluated on next tick; skip logic will catch it
      //   if the branch-origin node finishes without matching this branch)
    }

    ready.push(node);
  }

  return ready;
}

// ── onResult Evaluation ──

function matchesCondition(
  result: Record<string, unknown>,
  condition: ResultCondition,
): boolean {
  return Object.entries(condition).every(
    ([key, expected]) => result[key] === expected,
  );
}

export type OnResultAction =
  | { action: "complete" }
  | { action: "wait"; prompt: string; waitKind?: string; inputSchema?: HumanWaitSpec["inputSchema"]; saveAs?: HumanWaitSpec["saveAs"] }
  | { action: "fail"; reason: string }
  | { action: "branch"; matchedBranchIndex: number }
  | { action: "rerun"; rerun: NodeOnResultRerun };

function waitAction(wait: HumanWaitSpec): OnResultAction {
  return {
    action: "wait",
    prompt: wait.prompt,
    waitKind: wait.waitKind,
    ...(wait.inputSchema ? { inputSchema: wait.inputSchema } : {}),
    ...(wait.saveAs ? { saveAs: wait.saveAs } : {}),
  };
}

export function evaluateOnResult(
  onResult: NodeOnResult | undefined,
  result: Record<string, unknown>,
): OnResultAction {
  if (!onResult) return { action: "complete" };

  // Multi-branch mode: first-match-wins + default
  if (onResult.branches && onResult.branches.length > 0) {
    for (let i = 0; i < onResult.branches.length; i++) {
      const branch = onResult.branches[i];
      if (matchesCondition(result, branch.match)) {
        if (branch.complete) return { action: "branch", matchedBranchIndex: i };
        return { action: "branch", matchedBranchIndex: i };
      }
    }
    // No branch matched — apply default (treat as unmatched branch)
    if (onResult.default?.complete) return { action: "complete" };
    return { action: "complete" };
  }

  // Legacy if/then/else mode
  if (onResult.if && matchesCondition(result, onResult.if)) {
    // rerun takes priority over wait/complete — automatic rerun, no human gate
    if (onResult.then?.rerun) {
      return { action: "rerun", rerun: onResult.then.rerun };
    }
    if (onResult.then?.wait) {
      return waitAction(onResult.then.wait);
    }
    if (onResult.then?.complete) {
      return { action: "complete" };
    }
    return { action: "complete" };
  }

  if (onResult.else?.rerun) {
    return { action: "rerun", rerun: onResult.else.rerun };
  }
  if (onResult.else?.wait) {
    return waitAction(onResult.else.wait);
  }
  if (onResult.else?.complete) {
    return { action: "complete" };
  }

  return { action: "complete" };
}

// ── Workflow Completion Check ──

export function isWorkflowComplete(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
): boolean {
  return workflow.nodes.every((node) => {
    const state = nodeStates[node.id];
    return state?.status === "succeeded" || state?.status === "skipped";
  });
}

// ── Build nodeOutput Context from NodeStates ──

export function buildNodeOutputContext(
  nodeStates: Record<string, NodeState>,
): Record<string, Record<string, unknown>> {
  const output: Record<string, Record<string, unknown>> = {};
  for (const [nodeId, state] of Object.entries(nodeStates)) {
    if (state.result) {
      output[nodeId] = state.result;
    }
    // Inject llmEvaluation as a sub-key so {{nodeId.llmEvaluation.reason}} resolves
    if (state.llmEvaluation) {
      if (!output[nodeId]) output[nodeId] = {};
      (output[nodeId] as Record<string, unknown>).llmEvaluation = state.llmEvaluation;
    }
  }
  return output;
}

export function buildScopedNodeOutputContext(
  flowState: FlowState,
  currentNodeId?: string,
): Record<string, Record<string, unknown>> {
  const output = buildNodeOutputContext(flowState.nodeStates);
  const currentMeta = currentNodeId ? flowState.runtimeNodeMeta?.[currentNodeId] : undefined;
  if (!currentMeta) return output;

  let scopedBodyKeys: string[] = [];
  for (const [nodeId, state] of Object.entries(flowState.nodeStates)) {
    if (!state.result) continue;

    const meta = flowState.runtimeNodeMeta?.[nodeId];
    if (
      meta
      && meta.loopId === currentMeta.loopId
      && meta.iteration === currentMeta.iteration
    ) {
      output[meta.bodyNodeId] = state.result;
      scopedBodyKeys.push(meta.bodyNodeId);
    }
  }

  // Diagnostic: log scoped mapping for loop-group context debugging (BUG-2)
  if (scopedBodyKeys.length > 0) {
    console.log(`[buildScopedNodeOutputContext] currentNodeId=${currentNodeId} loopId=${currentMeta.loopId} iteration=${currentMeta.iteration} scopedBodyKeys=[${scopedBodyKeys.join(",")}]`);
  }

  return output;
}

// ── Build Template Context ──

export type ActionTemplateExtras = {
  nodeOutput: Record<string, Record<string, unknown>>;
  loop?: { id: string; iteration: number; bodyNodeId: string };
  templateAliases?: Record<string, unknown>;
};

export function buildActionTemplateExtras(
  flowState: FlowState,
  currentNodeId?: string,
): ActionTemplateExtras {
  const context = buildTemplateContext(
    flowState,
    "",
    {},
    currentNodeId ? { currentNodeId } : {},
  );
  const currentMeta = currentNodeId ? flowState.runtimeNodeMeta?.[currentNodeId] : undefined;
  if (!currentMeta) return { nodeOutput: context.nodeOutput };

  return {
    nodeOutput: context.nodeOutput,
    loop: context.loop as ActionTemplateExtras["loop"],
    templateAliases: {
      [currentMeta.iterationVar]: currentMeta.iteration,
      iteration: currentMeta.iteration,
    },
  };
}

export function buildTemplateContext(
  flowState: FlowState,
  skillRoot: string,
  currentResult: Record<string, unknown> = {},
  options: { currentNodeId?: string; userIdentity?: Record<string, unknown> } = {},
): TemplateContext {
  const input = flowState.input ?? { params: flowState.params, files: [] };
  const packRoot = flowState.workflowSnapshot?.defaults?.packRoot;
  const pythonBin = process.env.WORKFLOW_PYTHON_BIN || "python3";
  const context: TemplateContext = {
    ...flowState.params,
    ...currentResult,
    params: flowState.params,
    input,
    result: currentResult,
    skillRoot,
    ...(packRoot ? { packRoot } : {}),
    pythonBin,
    workflowData: flowState.workflowData,
    actionOutputs: flowState.actionOutputs,
    flowHooks: flowState.flowHooks,
    nodeOutput: buildScopedNodeOutputContext(flowState, options.currentNodeId),
    __user__: options.userIdentity,
  };
  const currentMeta = options.currentNodeId
    ? flowState.runtimeNodeMeta?.[options.currentNodeId]
    : undefined;
  if (currentMeta) {
    context[currentMeta.iterationVar] = currentMeta.iteration;
    context.loop = {
      id: currentMeta.loopId,
      iteration: currentMeta.iteration,
      bodyNodeId: currentMeta.bodyNodeId,
    };
    // Dynamic-template: inject the iteration variable as the item value
    // stored at workflowData.__dt_${loopId}__${iteration}
    const dtItemKey = `__dt_${currentMeta.loopId}__${currentMeta.iteration}`;
    const dtItem = flowState.workflowData[dtItemKey];
    if (dtItem !== undefined) {
      context[currentMeta.iterationVar] = dtItem;
    }
  }
  return context;
}

// ── Update Business Status / Phase ──

export function computePhaseAndStatus(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
): { currentPhase: string; businessStatus: string; activeNodes: string[] } {
  const activeNodes: string[] = [];
  let latestPhase = "P1";
  let latestStatus = "INIT";

  for (const node of workflow.nodes) {
    const state = nodeStates[node.id];
    if (
      state?.status === "running" ||
      state?.status === "postActionsRunning" ||
      state?.status === "waiting" ||
      state?.status === "blocked"
    ) {
      activeNodes.push(node.id);
      latestPhase = node.phase;
      if (node.businessStatus) latestStatus = node.businessStatus;
    }
  }

  if (activeNodes.length === 0) {
    const lastSucceeded = [...workflow.nodes]
      .reverse()
      .find((n) => nodeStates[n.id]?.status === "succeeded");
    if (lastSucceeded) {
      latestPhase = lastSucceeded.phase;
      latestStatus = lastSucceeded.businessStatus ?? latestStatus;
    }
  }

  return { currentPhase: latestPhase, businessStatus: latestStatus, activeNodes };
}
