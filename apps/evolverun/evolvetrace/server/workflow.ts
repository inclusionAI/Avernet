/**
 * Standalone workflow spec normalization and validation.
 * No dependency on ClawFlow — re-implements the minimal subset needed for ClawWeb.
 */
export interface WorkflowNode {
  id: string;
  title: string;
  phase?: string;
  businessStatus?: string;
  executor: {
    type: string;
    [key: string]: unknown;
  };
  dependsOn?: string[];
  branchId?: string;
  join?: 'all' | 'any';
  triggerRule?: 'all_success' | 'one_success' | 'all_done';
  retry?: unknown;
  outputContract?: unknown;
  outputSchema?: unknown;
  mock?: unknown;
  knowledge?: unknown;
  knowledgeBaseId?: string;
  knowledgeQuery?: string;
  onSuccess?: unknown;
  onFailure?: unknown;
  onFeedback?: unknown;
  onResult?: Array<{ value: string; target: string }>;
  alerting?: unknown;
  progressMessage?: string;
  validationTemplateId?: string;
  validationMinScore?: number;
  [key: string]: unknown;
}

export interface WorkflowSpec {
  id: string;
  version: string;
  title: string;
  nodes: WorkflowNode[];
  config?: Record<string, unknown>;
  params?: Record<string, unknown>;
  tests?: unknown[];
  requiredParams?: string[];
  input?: unknown;
  identity?: unknown;
  outputs?: unknown;
  debug?: unknown;
  defaults?: unknown;
  collaboration?: unknown;
  workflow?: unknown;
  messages?: unknown;
  allowedBots?: string[];
  [key: string]: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function normalizeWorkflowSpec(raw: unknown): WorkflowSpec {
  if (!isRecord(raw)) {
    throw new Error("Validation error: workflow spec must be an object");
  }

  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  if (!id) {
    throw new Error("Validation error: workflow spec must have a non-empty 'id'");
  }

  // Preserve the YAML version value as-is (string or number) instead of forcing "1"
  // when it isn't a string — an unquoted `version: 2` parses as a number and was being
  // reset to "1" on every save. The DB deploy version (workflow_specs.version column)
  // is a separate field managed by deploy_history, not this spec version.
  const version = typeof raw.version === "number"
    ? String(raw.version)
    : typeof raw.version === "string"
      ? raw.version.trim()
      : "1";
  const title = typeof raw.title === "string" ? raw.title.trim() : id;

  if (!Array.isArray(raw.nodes)) {
    throw new Error("Validation error: workflow spec must have a 'nodes' array");
  }

  const nodes: WorkflowNode[] = raw.nodes.map((node: unknown, index: number) => {
    if (!isRecord(node)) {
      throw new Error(`Validation error: nodes[${index}] must be an object`);
    }

    const nodeId = typeof node.id === "string" ? node.id.trim() : "";
    if (!nodeId) {
      throw new Error(`Validation error: nodes[${index}].id must be a non-empty string`);
    }

    const nodeTitle = typeof node.title === "string" ? node.title.trim() : nodeId;

    if (!isRecord(node.executor)) {
      throw new Error(`Validation error: nodes[${index}].executor must be an object with a 'type' field`);
    }
    const executorType = typeof node.executor.type === "string" ? node.executor.type.trim() : "";
    if (!executorType) {
      throw new Error(`Validation error: nodes[${index}].executor.type must be a non-empty string`);
    }

    const normalized: WorkflowNode = {
      id: nodeId,
      title: nodeTitle,
      executor: { ...node.executor } as WorkflowNode["executor"],
    };

    if (typeof node.phase === "string") normalized.phase = node.phase;
    if (typeof node.businessStatus === "string") normalized.businessStatus = node.businessStatus;
    if (Array.isArray(node.dependsOn)) normalized.dependsOn = node.dependsOn;
    if (typeof node.branchId === "string") normalized.branchId = node.branchId;
    if (node.join !== undefined) normalized.join = node.join as WorkflowNode["join"];
    if (node.triggerRule !== undefined) normalized.triggerRule = node.triggerRule as WorkflowNode["triggerRule"];
    if (node.retry !== undefined) normalized.retry = node.retry as WorkflowNode["retry"];
    if (node.outputContract !== undefined) normalized.outputContract = node.outputContract as WorkflowNode["outputContract"];
    if (node.outputSchema !== undefined) normalized.outputSchema = node.outputSchema as WorkflowNode["outputSchema"];
    if (node.mock !== undefined) normalized.mock = node.mock as WorkflowNode["mock"];
    if (node.knowledge !== undefined) normalized.knowledge = node.knowledge as WorkflowNode["knowledge"];
    if (typeof node.knowledgeBaseId === "string") normalized.knowledgeBaseId = node.knowledgeBaseId;
    if (typeof node.knowledgeQuery === "string") normalized.knowledgeQuery = node.knowledgeQuery;
    if (node.onSuccess !== undefined) normalized.onSuccess = node.onSuccess as WorkflowNode["onSuccess"];
    if (node.onFailure !== undefined) normalized.onFailure = node.onFailure as WorkflowNode["onFailure"];
    if (node.onFeedback !== undefined) normalized.onFeedback = node.onFeedback as WorkflowNode["onFeedback"];
    if (node.onResult !== undefined) normalized.onResult = node.onResult as WorkflowNode["onResult"];
    if (node.alerting !== undefined) normalized.alerting = node.alerting as WorkflowNode["alerting"];
    if (typeof node.progressMessage === "string") normalized.progressMessage = node.progressMessage;
    if (typeof node.validationTemplateId === "string") normalized.validationTemplateId = node.validationTemplateId;
    if (typeof node.validationMinScore === "number") normalized.validationMinScore = node.validationMinScore;

    // Preserve unknown node-level fields
    const knownNodeKeys = new Set([
      'id', 'title', 'phase', 'businessStatus', 'executor', 'dependsOn', 'branchId',
      'join', 'triggerRule', 'retry', 'outputContract', 'outputSchema',
      'mock', 'knowledge', 'knowledgeBaseId', 'knowledgeQuery',
      'onSuccess', 'onFailure', 'onFeedback', 'onResult',
      'alerting', 'progressMessage', 'validationTemplateId', 'validationMinScore',
    ]);
    for (const key of Object.keys(node)) {
      if (!knownNodeKeys.has(key) && !(key in normalized)) {
        (normalized as Record<string, unknown>)[key] = node[key];
      }
    }

    return normalized;
  });

  const spec: WorkflowSpec = { id, version, title, nodes };
  if (isRecord(raw.config)) spec.config = raw.config as Record<string, unknown>;
  if (isRecord(raw.params)) spec.params = raw.params as Record<string, unknown>;
  if (Array.isArray(raw.tests)) spec.tests = raw.tests;
  if (raw.requiredParams !== undefined) spec.requiredParams = raw.requiredParams as string[];
  if (raw.input !== undefined) spec.input = raw.input as WorkflowSpec["input"];
  if (raw.identity !== undefined) spec.identity = raw.identity as WorkflowSpec["identity"];
  if (raw.outputs !== undefined) spec.outputs = raw.outputs as WorkflowSpec["outputs"];
  if (raw.debug !== undefined) spec.debug = raw.debug as WorkflowSpec["debug"];
  if (raw.defaults !== undefined) spec.defaults = raw.defaults as WorkflowSpec["defaults"];
  if (raw.collaboration !== undefined) spec.collaboration = raw.collaboration as WorkflowSpec["collaboration"];
  if (raw.workflow !== undefined) spec.workflow = raw.workflow as WorkflowSpec["workflow"];
  if (raw.messages !== undefined) spec.messages = raw.messages as WorkflowSpec["messages"];
  if (Array.isArray(raw.allowedBots)) spec.allowedBots = raw.allowedBots as string[];

  // Preserve unknown top-level fields
  const knownTopKeys = new Set([
    'id', 'version', 'title', 'nodes', 'config', 'params', 'tests', 'requiredParams',
    'input', 'identity', 'outputs', 'debug', 'defaults',
    'collaboration', 'workflow', 'messages', 'allowedBots',
  ]);
  for (const key of Object.keys(raw)) {
    if (!knownTopKeys.has(key) && !(key in spec)) {
      (spec as Record<string, unknown>)[key] = raw[key];
    }
  }

  return spec;
}

export function validateWorkflowSemantics(spec: WorkflowSpec): void {
  if (spec.nodes.length === 0) {
    throw new Error("Validation error: workflow must have at least one node");
  }

  const nodeIds = new Set(spec.nodes.map((n) => n.id));
  for (const node of spec.nodes) {
    if (node.dependsOn) {
      for (const dep of node.dependsOn) {
        if (!nodeIds.has(dep)) {
          throw new Error(`Validation error: node "${node.id}" dependsOn "${dep}" which does not exist`);
        }
      }
    }
  }
}