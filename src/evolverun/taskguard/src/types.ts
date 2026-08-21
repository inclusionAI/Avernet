// ── YAML Workflow Spec Types ──

export type WorkflowActor = {
  role?: string;
  id: string;
  name: string;
};

export type WorkflowRuntimeUser = {
  id?: string;
  name?: string;
  source: "delivery-context" | "env" | "workflow-default";
};

/** Communication channel detected from message context. */
export type UserChannel = "dingtalk" | "web" | "bcs" | "unknown";

/** Chat type within a channel. */
export type UserChatType = "one_on_one" | "group" | "owner" | "others";

/** Source of user identity resolution. */
export type UserIdentitySource =
  | "conversation-meta"
  | "sender-meta"
  | "session-key"
  | "bcs-session"
  | "delivery-context"
  | "env"
  | "workflow-default";

/** Channel detection intermediate result. */
export type ChannelInfo = {
  senderId: string;
  senderName?: string;
  channel: UserChannel;
  chatType: UserChatType;
  groupName?: string;
  groupChannel?: string;
  bcsGroupId?: string;
  source: UserIdentitySource;
};

/** Full user identity returned by the get_current_user tool. */
export type UserIdentity = {
  /** Sender ID — staff ID from DingTalk, userId from sessionKey, or ownerId */
  senderId: string;
  /** Sender name — from metadata or fallback sources */
  senderName?: string;
  /** Communication channel */
  channel: UserChannel;
  /** Chat type within channel */
  chatType: UserChatType;
  /** Group name (DingTalk group / BCS group) */
  groupName?: string;
  /** DingTalk group channel identifier */
  groupChannel?: string;
  /** BCS group ID */
  bcsGroupId?: string;
  /** Bot owner ID from credentials */
  ownerId?: string;
  /** Whether the sender is the bot owner */
  isOwner: boolean;
  /** Identity resolution source */
  source: UserIdentitySource;
};

export type WorkflowDefaultUser = {
  id?: string;
  name?: string;
  source?: "fixed";
};

export type WorkflowCommandSurface =
  | { type: "workflow" }
  | { type: "facade"; command: string };

export type BcsRouteSelector =
  | { type: "bot"; value: string }
  | { type: "name"; value: string }
  | { type: "role"; value: string }
  | { type: "capability"; value: string }
  | { type: "participants"; value: "all" | "others" }
  | { type: "originator"; value?: never }
  | { type: "driver"; value?: never };

export type BcsRouteSpec = {
  provider?: "bcs";
  mode?: "auto" | "tool" | "cli";
  to?: BcsRouteSelector[];
  reason?: string;
};

export type WorkflowCollaboration = {
  provider: "bcs";
  subject: {
    type: string;
    id: string;
    title?: string;
  };
  group?: {
    mode?: "existing" | "create";
    id?: string;
  };
  routing?: {
    defaultMode?: "auto" | "tool" | "cli";
    defaultReason?: string;
  };
};

export type WorkflowParticipant = {
  role?: string;
  id?: string;
  name?: string;
};

export type CollaborationDeliveryPrimary = "subagent" | "embedded-agent" | "bcs-route" | "bcs-cli";

export type CollaborationDeliveryMode = {
  primary: CollaborationDeliveryPrimary;
  action?: string;
};

export type CollaborationDeliverySpec = {
  private?: CollaborationDeliveryMode;
  /** DingTalk group chat delivery. */
  dingtalkGroup?: CollaborationDeliveryMode;
  /** BCS/collaboration platform group chat delivery. */
  collaboration?: CollaborationDeliveryMode;
};

export type CollaborationOnFeedback = {
  target: string;
  feedbackPath?: string;
  historyPath?: string;
  reset?: "target-and-descendants";
};

export type CollaborationExecutor = {
  type: "collaboration";
  taskKind?: string;
  skillName?: string;
  routeDisplayName?: string;
  route?: BcsRouteSpec;
  participant?: WorkflowParticipant;
  message: string;
  timeoutSeconds?: number;
  onFeedback?: CollaborationOnFeedback;
  delivery?: CollaborationDeliverySpec;
  contextPolicy?: WorkflowContextPolicy;
};

export type WorkflowApprover = {
  empId: string;
  name: string;
  role?: string;
};

export type CardFieldDef = {
  label: string;
  value: string;
};

// ── Card Section: interactive approval field groups ─────────────────────

/** A group of interactive fields for section-based approval (e.g., per-field accept/keep/customize). */
export type CardSectionDef = {
  id: string;
  title: string;
  icon?: string;
  description?: string;
  style?: "default" | "warning" | "info" | "danger";
  fields: SectionFieldDef[];
};

/** A single interactive field inside a card section. */
export type SectionFieldDef = {
  id: string;
  label: string;
  expected?: string;
  expectedLabel?: string;
  actual?: string;
  actualLabel?: string;
  actions?: SectionFieldActionDef[];
  customizable?: boolean;
  placeholder?: string;
};

/** An action button attached to a section field. */
export type SectionFieldActionDef = {
  key: string;
  label: string;
  type?: "primary" | "default" | "danger";
  autoFill?: string;
};

export type ApprovalPolicy = "any" | "all" | "majority";

export type ApprovalDeliveryPrimary = "subagent" | "embedded-agent" | "bcs-route" | "bcs-cli" | "card-dingtalk" | "card-secoc" | "card-web";

export type ApprovalDeliveryMode = {
  primary: ApprovalDeliveryPrimary;
  action?: string;
};

export type ApprovalDeliverySpec = {
  private?: ApprovalDeliveryMode;
  /** DingTalk group chat delivery (chatInject into the group session). */
  dingtalkGroup?: ApprovalDeliveryMode;
  /** BCS/collaboration platform group chat delivery. */
  collaboration?: ApprovalDeliveryMode;
};

export type ApprovalExecutor = {
  type: "approval";
  skillName: string;
  approvalType?: string;
  routeDisplayName?: string;
  route?: BcsRouteSpec;
  reviewerRef?: string;
  message: string;
  timeoutSeconds?: number;
  onRevise?: ApprovalOnRevise;
  delivery?: ApprovalDeliverySpec;
  contextPolicy?: WorkflowContextPolicy;
  cardId?: string;
  approvers?: WorkflowApprover[];
  cardFields?: CardFieldDef[];
  /** Interactive section groups for per-field confirmation (card-web only). */
  cardSections?: CardSectionDef[];
  approvalPolicy?: ApprovalPolicy;
  /**
   * Skip condition: when the resolved values match, the node auto-succeeds
   * without sending cards or waiting. Keys are dot-paths into the template
   * context (e.g. "nodeOutput.check_completeness.is_complete"), values are
   * the expected literal values. All conditions must match (AND semantics).
   *
   * Particularly useful inside loop-group bodies where an approval node
   * should be skipped when a preceding body node's output already satisfies
   * the exit condition (e.g. completeness check passed → skip supplement).
   */
  skipWhen?: Record<string, unknown>;
  /**
   * Persist callback result fields into workflowData, similar to
   * HumanGateConfirmAction.saveAs. Applied when a BCS callback resolves
   * this approval node.
   */
  saveAs?: Record<string, string>;
  /**
   * Display templates. When set, these override the default labels in
   * card-web / dingtalk approval cards, so workflows can customise the
   * card title, status label, and action-button text.
   * Supports {{var}} template syntax just like `message`.
   */
  cardTitle?: string;
  statusLabel?: string;
  actionLabel?: string;
  /**
   * URL template pointing to the original work-order / ticket page.
   * When set, the approval card uses this instead of the ClawMind run page.
   * Supports {{var}} template syntax.
   */
  workflowUrl?: string;
};

export type ApprovalOnRevise = {
  target: string;
  feedbackPath?: string;
  historyPath?: string;
  reset?: "target-and-descendants";
};

export type EmbeddedAgentExecutor = {
  type: "embedded-agent";
  skillName?: string;
  outputMode?: "text" | "json";
  prompt: string;
  timeoutSeconds?: number;
  contextPolicy?: WorkflowContextPolicy;
};

export type SubagentExecutor = {
  type: "subagent";
  skillName: string;
  prompt: string;
  timeoutSeconds?: number;
  contextPolicy?: WorkflowContextPolicy;
};

export type BcsRouteExecutor = {
  type: "bcs-route";
  target: string;
  message: string;
  timeout?: number;
};

export type McpCallExecutor = {
  type: "mcp-call";
  server: string;
  tool: string;
  args: Record<string, string>;
  outputMode?: "text" | "json";
  timeoutMs?: number;
  timeoutSeconds?: number;
};

export type CliScriptExecutor = {
  type: "cli-script";
  command: string;
  args?: string[] | Record<string, string>;
  env?: Record<string, string>;
  outputMode?: "text" | "json";
  timeoutMs?: number;
  timeoutSeconds?: number;
};

export type BaasCallExecutor = {
  type: "baas-call";
  mode?: "run" | "message";
  botId?: string;
  message: string;
  apiKeyRef?: string;
  baseUrl?: string;
  iamToken?: string;
  timeoutMs?: number;
  timeoutSeconds?: number;
  pollIntervalMs?: number;
  outputMode?: "text" | "json";
};

export type ActionExecutor = {
  type: "action";
  action: string;
  args?: Record<string, unknown>;
};

export type HumanInputFieldSpec = {
  type?: "string" | "number" | "boolean";
  parse?: { regex?: string };
  regex?: string;
  parser?: "regex" | { regex?: string; pattern?: string };
  pattern?: string;
  enum?: Array<string | number | boolean>;
  /** Keyword aliases for each enum value, used by L1 intent detection.
   *  Key is the enum value, value is an array of natural language keywords. */
  keywordAliases?: Record<string, string[]>;
};

export type HumanInputSchema = {
  type?: "object";
  required?: string[];
  properties?: Record<string, HumanInputFieldSpec>;
  fields?: Record<string, HumanInputFieldSpec>;
};

export type HumanGateConfirmAction = {
  inputSchema?: HumanInputSchema;
  saveAs?: Record<string, string>;
  next?: "succeed-current";
};

export type HumanGateReviseAction = {
  inputSchema?: HumanInputSchema;
  feedbackPath: string;
  feedbackTemplate?: string;
  feedbackMode?: "replace" | "append-line";
  historyPath?: string;
  target: string;
  reset?: "target-and-descendants";
  next: "rerun-target";
  /** Persist key-value pairs into workflowData upon revise, similar to confirm.saveAs */
  saveAs?: Record<string, string>;
};

export type HumanGateRejectAction = {
  inputSchema?: HumanInputSchema;
  saveAs?: Record<string, string>;
  next?: "fail-flow" | "block-flow";
};

export type HumanGateActions = {
  confirm?: HumanGateConfirmAction;
  revise?: HumanGateReviseAction;
  reject?: HumanGateRejectAction;
};

export type HumanCommandHint = {
  label: string;
  args?: string[];
};

export type HumanCommandHints = {
  confirm?: HumanCommandHint;
  revise?: HumanCommandHint;
  reject?: HumanCommandHint;
};

export type HumanWaitExecutor = {
  type: "human";
  prompt: string;
  waitKind?: string;
  inputSchema?: HumanInputSchema;
  saveAs?: Record<string, string>;
  actions?: HumanGateActions;
  commandHints?: HumanCommandHints;
};

export type AsyncCallbackAuthConfig = {
  /** Authentication mode: "hmac" uses HMAC-SHA256 shared-secret signing;
   *  "x-one-id" uses Ant Group IAM token via x-one-id header. */
  mode: "hmac" | "x-one-id";
  /** HMAC shared secret (required when mode is "hmac"). */
  secret?: string;
  /** Allowed user IDs for x-one-id mode (optional allowlist). */
  allowedUsers?: string[];
};

export type AsyncCallbackExecutor = {
  type: "async-callback";
  /** Maximum wait time before the node times out (e.g. "30m", "2h", "24h"). */
  timeout?: string;
  /** Base URL for callback endpoint (overrides global config). */
  callbackBaseUrl?: string;
  /** Authentication configuration for the callback HTTP request. */
  auth?: AsyncCallbackAuthConfig;
  /** Map callback result fields into workflow variables. */
  saveAs?: Record<string, string>;
};

export type DoneExecutor = {
  type: "done";
};

export type LoopGroupUntilSpec = {
  node: string;
  path: string;
  equals?: string | number | boolean | null;
  /**
   * Additional exit conditions checked against workflowData.
   * Each entry is a dot-path into workflowData (without the "workflowData." prefix)
   * and the expected value. If ANY condition matches, the loop exits immediately
   * — even if the primary `node.path === equals` hasn't matched yet.
   *
   * This is useful when a loop should exit not only when a body node signals
   * completion, but also when external state (e.g., a user rejection written
   * by saveAs) changes. Checked after each body node completes in an iteration.
   */
  orWorkflowData?: Record<string, string | number | boolean | null>;
};

export type LoopGroupOnMaxIterationsSpec = {
  action: "continue" | "fail";
  saveLastIteration?: boolean;
};

export type LoopGroupExecutor = {
  type: "loop-group";
  maxIterations: number;
  iterationVar: string;
  until?: LoopGroupUntilSpec;
  body: WorkflowNode[];
  onMaxIterations?: LoopGroupOnMaxIterationsSpec;
};

export type SubworkflowExecutor = {
  type: "subworkflow";
  workflowId: string;
  packId?: string;
  params?: Record<string, string>;
  onFailure?: "fail" | "retry" | "skip";
};

// ── Dynamic Workflow: Node Templates ──

/** A reusable node template that can be materialized at runtime by a
 *  `dynamic-template` executor. Template node IDs are relative — they
 *  get rewritten to runtime IDs upon materialization. */
export type NodeTemplate = {
  /** Template body: list of WorkflowNode definitions to clone per item. */
  body: WorkflowNode[];
  /** Optional JSON Schema describing the parameters the template accepts. */
  params?: Record<string, unknown>;
};

/** Dynamic-template executor: materializes a template's body once per item
 *  in the resolved `forEach` array, producing runtime nodes that merge
 *  into the effective workflow via `buildEffectiveWorkflow()`. */
export type DynamicTemplateExecutor = {
  type: "dynamic-template";
  /** Name of the template in WorkflowSpec.nodeTemplates to materialize. */
  template: string;
  /** Template expression resolving to an array (e.g. "{{discover.items}}"). */
  forEach: string;
  /** Variable name injected into template context for each iteration item. */
  iterationVar: string;
  /** Max number of items to materialize (default: 100). Excess items are truncated with a warning. */
  maxItems?: number;
};

/** Goal-evaluator executor: uses an LLM to assess whether a goal has been
 *  met. If not, triggers a rerun loop with feedback until maxAttempts. */
export type GoalEvaluatorExecutor = {
  type: "goal-evaluator";
  /** The goal statement to evaluate. */
  goal: string;
  /** Evaluator configuration for the LLM call. */
  evaluator: {
    /** Prompt template for the LLM, may include {{nodeId.output}} references. */
    prompt: string;
    /** Model identifier (resolved via OpenClaw SDK LLM interface). */
    model?: string;
    /** Sampling temperature (default: 0.2 for deterministic evaluation). */
    temperature?: number;
    /** Timeout in milliseconds for the LLM call. */
    timeoutMs?: number;
  };
  /** Maximum evaluation attempts before giving up (default: 3). */
  maxAttempts?: number;
  /** Action when the goal is not met after maxAttempts. */
  onNotMet?: {
    /** "fail" — mark the node as failed; "complete" — mark as succeeded anyway. */
    action: "fail" | "complete";
    /** Optional message template for the failure/success. */
    message?: string;
  };
};

/** LLM-orchestrator executor: iteratively chooses and injects actions
 *  (from an availableActions menu) into the running DAG until the goal
 *  is met or budget/iterations exhausted. */
export type LlmOrchestratorExecutor = {
  type: "llm-orchestrator";
  /** The high-level goal the orchestrator tries to achieve. */
  goal: string;
  /** Actions the orchestrator can choose from at each iteration. */
  availableActions: AvailableAction[];
  /** Max orchestrator iterations (default: 10). */
  maxIterations?: number;
  /** Per-orchestrator budget constraints. */
  budget?: FlowBudget;
  /** Adversarial verification: an independent LLM challenges the result. */
  verification?: {
    prompt: string;
    model?: string;
    /** Min number of verifier votes to accept (default: 1 of 3). */
    minVotes?: number;
    totalVoters?: number;
  };
};

/** YAML synthesizer executor — placeholder type registered in NodeExecutor
 *  for type completeness. The synthesizer is invoked via the `synthesize`
 *  ControllerAction (not as a node executor) because it must operate
 *  before a flow exists. This type MUST NOT appear in workflow YAML. */
export type YamlSynthesizerExecutor = {
  type: "yaml-synthesizer";
  /** Placeholder flag — always true; prevents accidental use in YAML. */
  _placeholder?: true;
};

// ── L5: Goal-Loop Executor Types ──

/** Convergence status returned by the convergence detector. */
export type ConvergenceStatus =
  | { status: "continue" }
  | { status: "stop"; reason: "no-progress" | "repeated-failure" | "budget-exhausted" | "max-iterations" | "max-replans" };

/** A YAML fragment generated by the repair strategy for injection into the running DAG. */
export type YamlFragment = {
  /** New node definitions to inject. */
  nodes: WorkflowNode[];
  /** Additional dependsOn declarations (nodeId → upstream nodeIds). */
  dependsOnDeclarations?: Record<string, string[]>;
};

/** A single iteration record in the goal-loop history. */
export type GoalLoopIterationRecord = {
  /** 1-based iteration number. */
  iteration: number;
  /** Phase that produced this record. */
  phase: "plan" | "execute" | "evaluate" | "repair" | "complete";
  /** Brief summary of the result. */
  resultSummary: string;
  /** Failure reason if the iteration failed (undefined on success). */
  failureReason?: string;
  /** The nodeId that failed, if applicable. */
  failedNodeId?: string;
  /** Token usage for this iteration. */
  tokenUsage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** IDs of nodes injected during this iteration (repair phase). */
  injectedNodes?: string[];
  /** Whether this iteration triggered a replan. */
  triggeredReplan?: boolean;
};

/** Runtime state for a goal-loop node, persisted in FlowState. */
export type GoalLoopRuntimeState = {
  /** The nodeId of the goal-loop node. */
  goalLoopNodeId: string;
  /** The goal statement. */
  goal: string;
  /** Current iteration (0 before first, 1 after first plan). */
  currentIteration: number;
  /** Current phase in the four-phase cycle. */
  currentPhase: "plan" | "execute" | "evaluate" | "repair" | "complete";
  /** Iteration history. */
  iterations: GoalLoopIterationRecord[];
  /** Number of global replans performed. */
  replans: number;
  /** Number of local repair attempts in the current iteration. */
  repairAttempts: number;
  /** Convergence status (updated after each iteration). */
  convergenceStatus: ConvergenceStatus;
  /** The last WorkflowSpec generated/used (for replan context). */
  lastWorkflowSpec?: WorkflowSpec;
  /** Budget consumed so far. */
  budgetUsed: { tokens: number; iterations: number; nodes: number };
  /** Final evaluation result when complete. */
  finalEvaluation?: { met: boolean; reason: string };
};

/** Goal-loop executor: adaptive loop fusing L2 (evaluate) + L3 (orchestrate) + L4 (synthesize). */
export type GoalLoopExecutor = {
  type: "goal-loop";
  /** The high-level goal the loop tries to achieve. */
  goal: string;
  /** Initial plan generation strategy. */
  initialPlan?: {
    type: "synthesize" | "spec";
    /** Explicit WorkflowSpec when type is "spec". */
    spec?: WorkflowSpec;
    /** Optional hints for the synthesizer. */
    hints?: string[];
  };
  /** Evaluation configuration for checking goal achievement. */
  evaluation: {
    /** LLM model for evaluation (e.g., "haiku"). */
    model?: string;
    /** Evaluation criteria — must be a non-empty array. */
    criteria: string[];
    /** Sampling temperature for the evaluator (default: 0.2). */
    temperature?: number;
  };
  /** Repair strategy configuration. */
  repair?: {
    mode: "adaptive" | "local-only" | "replan-only";
    /** Max global replans before stopping (default: 3). */
    maxReplans?: number;
    /** Max local repair attempts per iteration before escalating to replan (default: 3). */
    maxLocalRepairs?: number;
    /** Predefined repair actions the LLM can use. */
    repairActions?: AvailableAction[];
    /** Whether LLM can generate new actions not in repairActions (default: false). */
    allowDynamicActions?: boolean;
  };
  /** Budget constraints. */
  budget?: FlowBudget;
  /** Adversarial verification after goal is reported as met. */
  verification?: {
    prompt: string;
    model?: string;
    minVotes?: number;
    totalVoters?: number;
  };
  /** Convergence detection configuration. */
  convergence?: {
    /** Stop after N consecutive iterations with no progress (default: 3). */
    noProgressIterations?: number;
    /** Stop after N repeated failures of the same node (default: 5). */
    repeatedFailures?: number;
  };
  /** Max total iterations (default: 20). */
  maxIterations?: number;
  /** Campaign: when set, goal-loop iterations are appended to the campaign evidence chain. */
  campaignId?: string;
};

/** Goal-loop event types for observability. */
export type GoalLoopEventType =
  | "goal_loop_iteration_started"
  | "goal_loop_iteration_completed"
  | "goal_loop_convergence_stopped"
  | "goal_loop_repair_started"
  | "goal_loop_replan_started"
  | "goal_loop_repair_succeeded"
  | "goal_loop_budget_warning"
  | "goal_loop_budget_exhausted";

/** An action that the llm-orchestrator can select and materialize at runtime. */
export type AvailableAction = {
  /** Action name used in node ID generation: ${orchestratorId}__step${N}__${actionName} */
  name: string;
  /** Executor type to use for the materialized node. */
  type: NodeExecutor["type"];
  /** Executor spec overrides merged into the materialized node's executor. */
  params?: Record<string, unknown>;
  /** Human-readable description shown to the LLM. */
  description?: string;
};

// ── Dynamic Workflow: Budget Types ──

/** Budget constraints for a flow or an individual node. */
export type FlowBudget = {
  /** Maximum total token consumption. */
  maxTokens?: number;
  /** Maximum number of orchestrator iterations. */
  maxIterations?: number;
  /** Maximum number of dynamically injected nodes. */
  maxNodes?: number;
  /** Maximum elapsed wall-clock time in ms. */
  timeoutMs?: number;
  /** Enforcement strategy when budget is exhausted. */
  strategy?: "hard-stop" | "graceful-degrade" | "replan";
  /** Configuration for graceful-degrade strategy. */
  degradeConfig?: {
    /** Model to switch to when budget threshold is reached. */
    fallbackModel?: string;
    /** Threshold ratio (0–1) at which to switch model. */
    threshold?: number;
  };
};

// ── Dynamic Workflow: Injected Node Tracking ──

/** Record of a node dynamically injected into the running DAG. */
export type InjectedNodeRecord = {
  /** Runtime ID of the injected node. */
  nodeId: string;
  /** ID of the source node that triggered the injection. */
  sourceNodeId: string;
  /** Name of the action that produced this node (for orchestrator). */
  actionName?: string;
  /** Step number in the orchestrator iteration (0-based). */
  stepNum?: number;
  /** Timestamp when the node was materialized. */
  materializedAt: number;
};

/** LLM evaluation result attached to a node's onResult evaluation. */
export type LlmEvaluationResult = {
  /** Whether the LLM determined the condition was met. */
  met: boolean;
  /** The branchId the LLM selected (if branches mode). */
  matchedBranchId?: string;
  /** The LLM's reasoning text. */
  reason: string;
  /** Token usage from the LLM call. */
  usage?: TokenUsage;
  /** Model used for the evaluation. */
  model?: string;
};

/** Runtime state for an llm-orchestrator node. */
export type OrchestrationRuntimeState = {
  orchestratorId: string;
  status: "running" | "succeeded" | "failed" | "budget-exhausted";
  currentIteration: number;
  maxIterations: number;
  iterations: OrchestrationIterationState[];
  budgetUsed?: FlowBudget;
};

/** State for a single orchestrator iteration. */
export type OrchestrationIterationState = {
  iteration: number;
  selectedAction: string;
  injectedNodeId: string;
  result?: Record<string, unknown>;
  llmReasoning?: string;
  completedAt?: number;
};

/** Dynamic workflow event types emitted to observability system. */
export type DynamicWorkflowEventType =
  | "node_materialized"
  | "node_injected"
  | "llm_evaluation"
  | "orchestrator_iteration"
  | "budget_warning"
  | "budget_exhausted"
  | "yaml_synthesized"
  | "synthesis_validated"
  | "synthesis_rejected"
  | "human_approval_requested"
  | "human_approval_granted"
  | "human_approval_denied";

/** A dynamic workflow observability event. */
export type DynamicWorkflowEvent = {
  type: DynamicWorkflowEventType;
  flowId: string;
  workflowId: string;
  nodeId: string;
  timestamp: number;
  data: Record<string, unknown>;
};

export type NodeExecutor =
  | CollaborationExecutor
  | ApprovalExecutor
  | EmbeddedAgentExecutor
  | SubagentExecutor
  | BcsRouteExecutor
  | McpCallExecutor
  | CliScriptExecutor
  | BaasCallExecutor
  | ActionExecutor
  | HumanWaitExecutor
  | AsyncCallbackExecutor
  | DoneExecutor
  | LoopGroupExecutor
  | SubworkflowExecutor
  | DynamicTemplateExecutor
  | GoalEvaluatorExecutor
  | LlmOrchestratorExecutor
  | YamlSynthesizerExecutor
  | GoalLoopExecutor;

export type WorkflowContextHistoryMode = "structured" | "isolated" | "inherit" | "tail" | "compacted";

export type WorkflowContextPolicy = {
  history?: WorkflowContextHistoryMode;
  includeSessionHistory?: boolean;
  tailMessages?: number;
  excludeInjectMessages?: boolean;
  /** Context compression configuration. Overrides workflow-level defaults when set. */
  compression?: import("./context/types.js").ContextCompressionConfig;
};

export type TriggerRule = "all_success" | "one_success" | "all_done";

export type NodeRetryFailureReason =
  | "executor-failed"
  | "output-contract-failed"
  | "validation-failed";

export type NodeRetrySpec = {
  maxAttempts?: number;
  backoffMs?: number;
  on?: NodeRetryFailureReason[];
};

export type OutputContractSchemaType = "object" | "array" | "string" | "number" | "boolean";

export type OutputContractSchema = {
  type: OutputContractSchemaType;
  nullable?: boolean;
  required?: string[];
  properties?: Record<string, OutputContractSchema>;
  items?: OutputContractSchema;
  enum?: Array<string | number | boolean | null>;
};

export type OutputContractSpec = {
  required?: boolean;
  schema: OutputContractSchema;
};

export type WorkflowInputSourceSpec = {
  params?: boolean;
  message?: boolean;
  files?: {
    maxCount?: number;
    maxSizeMb?: number;
    allowedExtensions?: string[];
  };
};

export type WorkflowInputSpec = {
  mode?: "params" | "task" | "mixed";
  requiredParams?: string[];
  schema?: Record<string, unknown>;
  sources?: WorkflowInputSourceSpec;
};

export type WorkflowIdentitySpec = {
  key: string;
  label?: string;
  duplicatePolicy?: "reject-active" | "allow" | "reuse-active";
};

export type WorkflowOutputSpec = {
  from: string;
  public?: boolean;
  description?: string;
};

export type WorkflowOutputsSpec = Record<string, WorkflowOutputSpec>;

// ── onResult condition branching ──

export type ResultCondition = Record<string, string | number | boolean | null>;

export type HumanWaitSpec = {
  type: "human";
  prompt: string;
  waitKind?: string;
  inputSchema?: HumanInputSchema;
  saveAs?: Record<string, string>;
  actions?: HumanGateActions;
  commandHints?: HumanCommandHints;
};

export type LlmEvaluateSpec = {
  /** Condition description for the LLM to evaluate. */
  condition: string;
  /** Model override for this evaluation (default: LLM_MODEL env). */
  model?: string;
  /** Sampling temperature (default: 0.2 for deterministic evaluation). */
  temperature?: number;
  /** Timeout in ms for the LLM call (default: 15000). */
  timeoutMs?: number;
};

export type NodeOnResultBranch = {
  branchId?: string;
  match: ResultCondition;
  complete?: boolean;
  /** If set, use an LLM to evaluate `condition` instead of rule-based `match`.
   *  When LLM says the condition is met, this branch is selected.
   *  Falls back to `match` rules if the LLM call fails. */
  llmEvaluate?: LlmEvaluateSpec;
};

/**
 * Auto-rerun: when an onResult condition matches, automatically reset the
 * target node (and optionally its descendants) and re-execute from there.
 * Unlike HumanGateReviseAction (which requires human confirmation), this
 * is fully automatic — no waiting, no user interaction.
 */
export type NodeOnResultRerun = {
  /** Target node ID to reset and re-activate */
  target: string;
  /** Reset scope: "target" resets only the target, "target-and-descendants" resets all downstream nodes */
  reset?: "target" | "target-and-descendants";
  /** Persist key-value pairs into workflowData before rerun, same semantics as HumanGateConfirmAction.saveAs */
  saveAs?: Record<string, string>;
  /** Write feedback text into this workflowData key before rerun */
  feedbackPath?: string;
  /** Template for feedback text, resolved with template context */
  feedbackTemplate?: string;
};

export type NodeOnResult = {
  if?: ResultCondition;
  then?: { wait?: HumanWaitSpec; complete?: boolean; rerun?: NodeOnResultRerun };
  else?: { wait?: HumanWaitSpec; complete?: boolean; rerun?: NodeOnResultRerun };
  branches?: NodeOnResultBranch[];
  default?: { complete?: boolean };
};

export type HookRetrySpec = {
  maxAttempts?: number;
  backoffMs?: number;
};

export type HookActionSpec = {
  id: string;
  action: string;
  required?: boolean;
  args?: Record<string, unknown>;
  retry?: HookRetrySpec;
  saveAs?: Record<string, string>;
};

export type NodeValidationFailureAction = "fail-node" | "block-node" | "ignore";

export type ValidationActionSpec = Omit<HookActionSpec, "required">;

export type NodeValidationSpec = {
  actions: ValidationActionSpec[];
  onFailure?: NodeValidationFailureAction;
};

export type WorkflowPreflightActionSpec = Omit<HookActionSpec, "id"> & {
  id?: string;
  abortIf?: {
    empty?: boolean | string;
    message?: string;
    in?: {
      value: string;
      list: unknown[];
      message?: string;
    };
  };
};

export type WorkflowLifecycleSpec = {
  preflight?: WorkflowPreflightActionSpec[];
  onStart?: HookActionSpec[];
  onFinish?: HookActionSpec[];
};

export type ActionStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export type ActionState = {
  status: ActionStatus;
  action: string;
  required: boolean;
  attempts: number;
  startedAt?: number;
  completedAt?: number;
  result?: Record<string, unknown>;
  error?: string | null;
};

export type FlowHooksState = {
  onStart?: Record<string, ActionState>;
  onFinish?: Record<string, ActionState>;
};

// ── Workflow Node ──

export type WorkflowNode = {
  id: string;
  title: string;
  phase: string;
  businessStatus?: string;
  dependsOn: string[];
  branchId?: string;
  join?: "all" | "any";
  triggerRule?: TriggerRule;
  retry?: NodeRetrySpec;
  executor: NodeExecutor;
  /**
   * 节点级跳过条件:命中即自动成功、不执行 executor 主体。key 为点路径
   * (如 "nodeOutput.auto_check.skip_analysis"),value 为字面量,AND 语义。
   * approval 节点也可使用 executor.skipWhen(向后兼容),二者取 node.skipWhen 优先。
   */
  skipWhen?: Record<string, unknown>;
  /** 命中 skipWhen 时写入 nodeOutput 的结果(approval 节点忽略,沿用 approved 语义)。 */
  skipResult?: Record<string, unknown>;
  outputContract?: OutputContractSpec;
  outputSchema?: OutputContractSchema;
  onResult?: NodeOnResult;
  onSuccess?: HookActionSpec[];
  /** Node-level validation stage run after executor completion. */
  validation?: NodeValidationSpec;
  progressMessage?: string;
  /** Enable knowledge injection for this node. When true, KB results are injected as knowledgeContext. */
  knowledge?: boolean;
  /** Reference to a GRT knowledge base configuration (by kb_id). Takes precedence over knowledge boolean. */
  knowledgeBaseId?: string;
  /** Override the KB query for this node (defaults to extracting keywords from node input). */
  knowledgeQuery?: string;
  /** Per-node alerting overrides (overrides global alerting config for this node). */
  alerting?: NodeAlertingSpec;
  /** Mock configuration for dry-run testing. Not supported on approval executor type. */
  mock?: MockConfig;
  /** Reference to a validation template (by template_id) for LLM-based output validation. */
  validationTemplateId?: string;
  /** Minimum validation score (0-100) to consider the node output acceptable. Default 60. */
  validationMinScore?: number;
  /**
   * 由 normalizeNode 写入,记录"字段被 deepPreserveUnknown 保留但该 executor 不会执行"
   * 的字段名(如 mcp-call 上的 `executor.saveAs`、任意节点上的节点级 `saveAs`)。
   * 仅供 validate/对账读取与告警;运行时忽略。
   * 不写 `_ignored` 不代表字段会执行——以 isSaveAsCapableExecutor 为准。
   */
  _ignored?: string[];
  /** Campaign: when true, node output is automatically appended to the campaign evidence chain. */
  campaignEvidence?: boolean;
};

/** Per-node alerting configuration that overrides the global alerting settings. */
export type NodeAlertingSpec = {
  /** Enable dingtalk notifications for this node's failures. Overrides global onNodeFailure. */
  dingtalk?: boolean;
  /** Custom severity for alerts from this node. Defaults to "warning". */
  severity?: "critical" | "warning" | "info";
  /** Additional keywords to include in DingTalk notifications for this node. */
  keywords?: string[];
};

// ── DingTalk Notification Types ──

/** DingTalk notification target for a specific user. */
export type DingTalkUserTarget = {
  /** DingTalk user ID for oToMessages/batchSend.
   *  This is the userId field from the DingTalk user info API,
   *  NOT the employee ID or phone number. */
  userId: string;
  /** Human-readable name (for reference/logging only, not used in API calls). */
  name?: string;
};

/** DingTalk notification target for a group chat. */
export type DingTalkGroupTarget = {
  /** Open conversation ID for groupMessages/send.
   *  Starts with "cid" prefix, case-sensitive (base64-encoded).
   *  Obtain from DingTalk group settings or the conversation API. */
  openConversationId: string;
  /** Human-readable name (for reference/logging only, not used in API calls). */
  name?: string;
};

/** Message customization for DingTalk notifications. */
export type DingTalkMessageConfig = {
  /** Custom title for the notification.
   *  Default: "⚠️ ClawFlow 工作流失败通知" */
  title?: string;
  /** Whether to include a clickable run link in the notification.
   *  The link uses dingtalk:// protocol to open in the PC sidebar.
   *  Default: true */
  includeRunLink?: boolean;
};

/** DingTalk notification configuration within a workflow YAML.
 *  Each workflow can use a different robot — the credentials live here,
 *  NOT in the global application.yaml. */
export type DingTalkNotificationConfig = {
  /** Enterprise robot code (appKey).
   *  Used as the sender identity for both single-chat and group messages. */
  robotCode: string;
  /** Enterprise robot appSecret for OAuth2 token acquisition. */
  appSecret: string;
  /** Notification strategy for workflow/node failure. */
  onFailure: {
    /** Users to notify via single chat (oToMessages/batchSend).
     *  Each user receives an independent private message. */
    users?: DingTalkUserTarget[];
    /** Groups to notify (groupMessages/send).
     *  The message appears in the group as the robot. */
    groups?: DingTalkGroupTarget[];
    /** Message customization. */
    message?: DingTalkMessageConfig;
  };
};

/** Events that can trigger an HTTP callback notification. */
export type NotifyEvent =
  | "workflow_started"
  | "node_started"
  | "node_succeeded"
  | "node_failed"
  | "node_rejected"
  | "node_skipped"
  | "workflow_succeeded"
  | "workflow_failed"
  | "workflow_cancelled";

/** HTTP callback notification configuration as declared in YAML workflow specs. */
export type HttpCallbackNotification = {
  /** Human-readable name for this callback config. */
  name: string;
  /** Target URL to receive the HTTP POST callback. Must use HTTPS. */
  url: string;
  /** HMAC-SHA256 signing secret. Optional — if provided, payloads are signed; if omitted, no signature headers are sent. */
  secret?: string;
  /** Which events should trigger this callback. */
  notifyOn: NotifyEvent[];
  /** Whether this callback is active. Default: true. */
  enabled?: boolean;
  /** Request timeout in milliseconds. Default: 5000. */
  timeoutMs?: number;
  /** Maximum retry attempts for 5xx / network errors. Default: 2. */
  maxRetries?: number;
  /** Delay between retry attempts in milliseconds. Default: 1000. */
  retryDelayMs?: number;
  /** Whether ext_info should include node output_json (can be large). Default: false. */
  includeNodeOutput?: boolean;
};

/** Top-level notification configuration on a workflow. */
export type WorkflowNotifications = {
  /** DingTalk notification configuration. */
  dingtalk?: DingTalkNotificationConfig;
  /** HTTP callback notification configurations (one or more). */
  httpCallbacks?: HttpCallbackNotification[];
};

// ── Workflow Spec ──

export type WorkflowSpec = {
  id: string;
  version?: number;
  title: string;
  packId?: string;
  configPath?: string;
  config?: Record<string, unknown>;
  requiredParams?: string[];
  input?: WorkflowInputSpec;
  identity?: WorkflowIdentitySpec;
  outputs?: WorkflowOutputsSpec;
  debug?: {
    summaryKeys?: string[];
  };
  defaults?: {
    progress?: { enabled: boolean; sink: string };
    user?: WorkflowDefaultUser;
    packRoot?: string;
    contextPolicy?: {
      embeddedAgent?: WorkflowContextPolicy;
      subagent?: WorkflowContextPolicy;
    };
  };
  collaboration?: WorkflowCollaboration;
  workflow?: WorkflowLifecycleSpec;
  messages?: {
    onCreated?: string;
    onFinished?: string;
    onFinishedVariants?: string[];
    onFinishedRareVariants?: string[];
  };
  allowedBots?: string[];
  /** Per-workflow notification configuration.
   *  When defined, failure notifications are sent to the specified
   *  users and groups via the DingTalk Enterprise Robot API. */
  notifications?: WorkflowNotifications;
  /** Per-workflow chatInject level override. When set, overrides the global
   *  chatInject.level for runs of this workflow (subject to per-run trigger param). */
  chatInject?: { level?: import("./inject-level.js").InjectLevel };
  /** Reusable node templates for dynamic-template executors. */
  nodeTemplates?: Record<string, NodeTemplate>;
  /** Top-level budget constraints for the workflow. */
  budget?: FlowBudget;
  /** Per-workflow flow timeout in minutes. When defined, overrides the global
   *  `execution.flowTimeoutMinutes` from application.yaml for this workflow only.
   *  Set to 0 to disable the timeout watchdog for this workflow (let it run
   *  to completion with no reap).
   *
   *  Legacy spelling `timeoutMinutes` is still accepted on read (normalized
   *  into `flowTimeoutMinutes`) but not emitted, so saved/deployed specs
   *  canonicalize on the new name. */
  flowTimeoutMinutes?: number;
  nodes: WorkflowNode[];
  tests?: TestCase[];
};

// ── Runtime Node State ──

export type NodeStatus =
  | "pending"
  | "running"
  | "postActionsRunning"
  | "waiting"
  | "succeeded"
  | "failed"
  | "rejected"
  | "blocked"
  | "skipped";

export type BcsApprovalState = {
  protocolVersion: string;
  batchId?: string;
  taskId: string;
  workflowId?: string;
  flowId?: string;
  nodeId?: string;
  taskKind?: string;
  participant?: WorkflowParticipant;
  route?: {
    to: BcsRouteSelector[];
  };
  approvalType?: string;
  skillId?: string;
  routeDisplayName?: string;
};

export type NodeState = {
  status: NodeStatus;
  phase: string;
  executor: string;
  matchedBranchId?: string | null;
  attempts?: number;
  manualRetries?: number;
  retry?: Required<NodeRetrySpec>;
  startedAt?: number;
  updatedAt?: number;
  completedAt?: number;
  result?: Record<string, unknown>;
  error?: string | null;
  /** Warnings collected during execution (e.g. tool errors the agent recovered from).
   *  Use `formatWarningsAsErrorText()` to produce the `[WARNINGS]`-prefixed string
   *  for storage in `node_executions.error_text`. */
  warnings?: ExecutionWarning[];
  waitKind?: string;
  /** @deprecated Executor-level flow control removed. Kept for backward compatibility with persisted state. */
  fcSlotPreAcquired?: boolean;
  waitPrompt?: string;
  waitInputSchema?: HumanInputSchema;
  waitSaveAs?: Record<string, string>;
  bcsRouteId?: string | null;
  bcsApproval?: BcsApprovalState;
  /** Callback token issued when an async-callback node enters waiting state.
   *  The token is a UUID v4 that identifies the pending callback and is
   *  single-use — consumed on first successful callback or expired by the
   *  timeout poller. */
  callbackToken?: string;
  childSessionKey?: string | null;
  progressMessageIds?: string[];
  postActions?: Record<string, ActionState>;
  usage?: TokenUsage;
  /** LLM evaluation result attached by onResult LLM branch evaluation. */
  llmEvaluation?: LlmEvaluationResult;
};

export type TokenUsage = {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
  totalTokens?: number;
  toolCalls?: number;
  estimated?: boolean;
  source?: "reported" | "estimated" | "mixed";
  method?: string;
  confidence?: "low" | "medium" | "high";
};

// ── YAML Synthesis (Layer D) ──

/** Validation error from the three-stage synthesis validation pipeline. */
export type ValidationError = {
  /** Which validation stage produced this error. */
  stage: "schema" | "semantic" | "security";
  /** Dot-notation path to the problematic field (e.g., "nodes[2].dependsOn"). */
  path: string;
  /** Human-readable error description. */
  message: string;
  /** Severity: "error" causes validation failure; "warning" does not. */
  severity?: "error" | "warning";
  /** Optional fix hint for LLM correction loop. */
  suggestion?: string;
};

/** Configuration for the synthesis subsystem (Layer D). */
export type SynthesisConfig = {
  /** LLM model override — empty string falls back to LLM_MODEL env var. */
  defaultModel: string;
  /** Low temperature for stable YAML structure (default 0.3). */
  defaultTemperature: number;
  /** Max tokens for LLM generation (default 8192). */
  defaultMaxTokens: number;
  /** Maximum goal text length in characters (default 2000). */
  maxGoalLength: number;
  /** Maximum correction rounds when validation fails (default 3). */
  defaultMaxCorrections: number;
  /** Sandbox executor whitelist — only these executor types are allowed. */
  allowedExecutors: string[];
  /** Admin-controlled extensions to the whitelist. */
  extraAllowedExecutors: string[];
  /** Maximum node count in a synthesized workflow (default 50). */
  maxNodeCount: number;
  /** Regex patterns for detecting hardcoded secrets. */
  secretPatterns: string[];
  /** Template expression paths that are forbidden (e.g., "process.env"). */
  forbiddenTemplatePaths: string[];
  /** Human approval gate configuration. */
  humanApproval: {
    strategy: "never" | "on-warning" | "always";
    warningTriggers: {
      nodeCountExceeds: number;
      budgetExceeds: number;
      usesDynamicExecutors: boolean;
      hasLoopGroup: boolean;
    };
  };
};

/** Result of a YAML synthesis attempt. */
export type SynthesisResult = {
  /** Whether a valid WorkflowSpec was produced. */
  success: boolean;
  /** The validated WorkflowSpec — present on success. */
  workflowSpec?: WorkflowSpec;
  /** Raw YAML text from the LLM. */
  rawYaml?: string;
  /** Structured validation errors — present on failure or partial success. */
  validationErrors?: ValidationError[];
  /** Number of correction rounds used (0 = first attempt passed). */
  correctionRounds: number;
  /** Token consumption across all LLM calls. */
  llmUsage: TokenUsage;
  /** LLM model used for synthesis. */
  llmModel: string;
};

export type WorkflowUsage = {
  total: TokenUsage;
  byNode: Record<string, TokenUsage>;
};

export type WorkflowPin = {
  workflowId: string;
  workflowVersion: number;
  workflowDigest: string;
  packId?: string;
  packVersion?: string;
  packDigest?: string;
  packRoot?: string;
  source: "workspace-pack" | "configured-pack" | "db";
  capturedAt: string;
};

export type FlowInputFile = {
  name: string;
  originalPath?: string;
  artifactPath: string;
  mimeType?: string;
  size: number;
  digest: string;
};

export type FlowInput = {
  params: Record<string, string>;
  message?: string;
  files: FlowInputFile[];
  digest: string;
  digestShort: string;
};

export type FlowIdentity = {
  key: string;
  label: string;
  duplicatePolicy: "reject-active" | "allow" | "reuse-active";
};

// ── Flow State (stateJson) ──

export type AuditLogEntry = {
  time: string;
  node: string;
  action: string;
  detail: string;
};

export type ExecutionMode = "private" | "dingtalk-group" | "bcs-group";

export type FlowEventType =
  | "workflow_started"
  | "workflow_preflight"
  | "workflow_reopened"
  | "workflow_blocked"
  | "workflow_finished"
  | "workflow_repaired"
  | "node_ready"
  | "node_started"
  | "node_waiting"
  | "node_succeeded"
  | "node_output_contract_failed"
  | "node_failed"
  | "node_manual_retry"
  | "node_skipped"
  | "loop_started"
  | "loop_iteration_started"
  | "loop_iteration_completed"
  | "loop_completed"
  | "loop_failed"
  | "flow_hidden"
  | "flow_imported"
  | "action_started"
  | "action_succeeded"
  | "action_failed"
  | "chat_inject_failed"
  | "embedded_agent_event"
  | "embedded_agent_final_output"
  | "collaboration_result_received"
  | "collaboration_result_rejected"
  | "subworkflow_started"
  | "subworkflow_finished"
  | "node_validation_result"
  | "validation_failed"
  | "flow_control_queued"
  | "flow_control_resumed"
  | "flow_control_expired"
  | "node_materialized"
  | "node_injected"
  | "llm_evaluation"
  | "orchestrator_iteration"
  | "budget_warning"
  | "budget_exhausted";

export type FlowEvent = {
  id: string;
  time: number;
  type: FlowEventType;
  flowId: string;
  workflowId: string;
  nodeId?: string | null;
  actionId?: string | null;
  attempt?: number;
  data?: Record<string, unknown>;
  error?: string | null;
};

export type LoopGroupRuntimeStatus = "pending" | "running" | "waiting" | "succeeded" | "failed" | "blocked";

export type LoopIterationRuntimeStatus = "running" | "waiting" | "succeeded" | "failed" | "skipped";

export type LoopIterationRuntimeState = {
  iteration: number;
  status: LoopIterationRuntimeStatus;
  nodeIds: Record<string, string>;
  startedAt: number;
  completedAt?: number;
};

export type LoopGroupRuntimeState = {
  loopId: string;
  status: LoopGroupRuntimeStatus;
  currentIteration: number;
  maxIterations: number;
  iterationVar: string;
  iterations: Record<string, LoopIterationRuntimeState>;
  exitReason?: "until-matched" | "until-workflow-data-matched" | "max-iterations-continue" | "max-iterations-fail";
  lastIteration?: number;
  error?: string;
};

export type LoopRuntimeNodeMeta = {
  loopId: string;
  iteration: number;
  bodyNodeId: string;
  iterationVar: string;
};

// ── Dynamic Template Runtime State ──

export type DynamicTemplateRuntimeStatus = "pending" | "running" | "waiting" | "succeeded" | "failed";

/** Runtime state for a single materialized item of a dynamic-template node. */
export type DynamicTemplateItemState = {
  index: number;
  nodeIds: Record<string, string>;
};

/** Runtime state for a dynamic-template node, tracking all materialized items. */
export type DynamicTemplateRuntimeState = {
  sourceNodeId: string;
  templateName: string;
  status: DynamicTemplateRuntimeStatus;
  iterationVar: string;
  items: Record<string, DynamicTemplateItemState>;
  materializedCount: number;
  truncated?: boolean;
  error?: string;
};

export type SubworkflowFlowMeta = {
  parentFlowId: string;
  parentNodeId: string;
  depth: number;
};

export const MAX_SUBWORKFLOW_DEPTH = 3;

export type FlowState = {
  workflowId: string;
  workflowVersion: number;
  params: Record<string, string>;
  commandSurface?: WorkflowCommandSurface;
  input?: FlowInput;
  identity?: FlowIdentity;
  executionMode: ExecutionMode;
  bcsGroupId?: string;
  businessStatus: string;
  currentPhase: string;
  activeNodes: string[];
  nodeStates: Record<string, NodeState>;
  workflowData: Record<string, unknown>;
  actors?: Record<string, WorkflowActor>;
  actionOutputs: Record<string, Record<string, unknown>>;
  flowHooks: FlowHooksState;
  /** Runtime state for node-level validation actions, keyed by nodeId -> actionId -> state. */
  nodeValidationStates?: Record<string, Record<string, ActionState>>;
  loopGroups?: Record<string, LoopGroupRuntimeState>;
  dynamicTemplates?: Record<string, DynamicTemplateRuntimeState>;
  runtimeNodeMeta?: Record<string, LoopRuntimeNodeMeta>;
  subworkflowMeta?: SubworkflowFlowMeta;
  auditLog: AuditLogEntry[];
  flowEvents?: FlowEvent[];
  usage?: WorkflowUsage;
  workflowPin?: WorkflowPin;
  workflowSnapshot?: WorkflowSpec;
  /** Provenance: set when a flow is continued via `retry --use-current-def` (ran against a definition newer than the launch snapshot). Does NOT overwrite workflowPin/workflowSnapshot. */
  continuedWithCurrentDef?: { atRevision: number; fromWorkflowDigest?: string; currentDigest?: string; source?: string; debug?: boolean; capturedAt: string };
  /** Human intervention: parsed .credentials content (real_bot_id, staff_no, etc.) stored as JSON string. */
  originCredentials?: string | null;
  /** Human intervention: sessionKey at workflow start time (e.g. "agent:main:dashboard:xxx-yyy"). */
  originSessionKey?: string | null;
  /** Human intervention: resolved session UUID from sessionKey. */
  originSessionId?: string | null;
  /** Human intervention: BaaS-format bot_id "real_bot_id:staff_no" (e.g. "default:151614"). */
  originBotId?: string | null;
  /** Dynamic workflow: records of nodes dynamically injected into the DAG. */
  injectedNodes?: InjectedNodeRecord[];
  /** Dynamic workflow: runtime state for llm-orchestrator nodes. */
  orchestrationState?: Record<string, OrchestrationRuntimeState>;
  /** Dynamic workflow: LLM evaluation results keyed by node ID. */
  llmEvaluations?: Record<string, LlmEvaluationResult>;
  /** L5 goal-loop: runtime state for goal-loop nodes. */
  goalLoopState?: GoalLoopRuntimeState;
};

// ── Campaign: Cross-execution aggregation layer ──

/** Campaign status lifecycle. */
export type CampaignStatus = "active" | "paused" | "completed" | "failed" | "abandoned";

/** Budget constraints for a campaign (aggregated across multiple flows). */
export type CampaignBudget = {
  /** Maximum total token consumption across all flows in this campaign. */
  maxTokens?: number;
  /** Maximum total number of flow runs in this campaign. */
  maxFlows?: number;
  /** Maximum total iterations (Goal-Loop iterations across all flows). */
  maxIterations?: number;
};

/** A single evidence entry in the campaign's evidence chain. */
export type CampaignEvidence = {
  id: string;
  campaignId: string;
  flowId: string;
  nodeId: string;
  /** Output summary (truncated to 500 chars). */
  summary: string;
  createdAt: number;
};

/** Gate status for campaign-level human approval. */
export type CampaignGateStatus = "pending" | "approved" | "rejected" | "expired";

/** A persistent human-approval gate within a campaign. */
export type CampaignGate = {
  id: string;
  campaignId: string;
  flowId: string;
  nodeId: string;
  prompt: string;
  options: string[];
  status: CampaignGateStatus;
  createdAt: number;
  resolvedAt?: number;
  resolvedBy?: string;
  reason?: string;
};

/** A campaign — upper-level aggregation of multiple flow runs sharing a goal. */
export type Campaign = {
  id: string;
  /** Natural-language goal description. */
  goal: string;
  status: CampaignStatus;
  budget: CampaignBudget;
  /** Tokens consumed so far (aggregated from all associated flows). */
  usedTokens: number;
  /** Iterations consumed so far. */
  usedIterations: number;
  /** Number of flow runs associated with this campaign. */
  flowCount: number;
  createdAt: number;
  updatedAt: number;
  completedAt?: number;
};

/** Association between a campaign and a flow run. */
export type CampaignFlow = {
  campaignId: string;
  flowId: string;
  workflowId: string;
  status: string;
  tokenUsage: number;
  startedAt: number;
  completedAt?: number;
};

// ── Wait State (waitJson) ──

export type WaitState = {
  kind: "platform-workflow";
  workflowId: string;
  params: Record<string, string>;
  activeNodes: string[];
  waitingFor: string;
  received?: string[];
  pending?: string[];
  hint: string;
  userAction: string;
};

// ── Executor Result ──

/** Warning emitted during executor execution. Indicates a recoverable issue
 *  that did not cause the node to fail, but may indicate degraded quality
 *  (e.g. tool errors that the agent recovered from, JSON repair, etc.). */
export type ExecutionWarning = {
  code: "tool_errors" | "partial_output" | "recovered_from_error" | "json_repair_needed" | "session_errors";
  message: string;
  detail?: Record<string, unknown>;
};

export type ExecutorResult = {
  status: "succeeded" | "waiting" | "failed";
  result?: Record<string, unknown>;
  waitConfig?: { prompt: string; hint?: string; waitKind?: string };
  error?: string;
  /** Raw Error object preserved for complete stack trace logging in JSONL. */
  rawError?: unknown;
  warnings?: ExecutionWarning[];
  usage?: TokenUsage;
  /** Session file path from embedded-agent execution — used by Controller to persist step traces. */
  sessionFile?: string;
  /** Skill name from embedded-agent execution — used by Controller to persist step traces. */
  skillName?: string | null;
  /** Template-resolved prompt text (after {{...}} substitution) for embedded-agent/subagent nodes. */
  resolvedPrompt?: string;
};

// ── Workflow Test Framework ──

export type MockConfig = {
  output?: Record<string, unknown>;
  error?: string;
  timeout?: boolean;
  delay?: number;
  autoConfirm?: boolean;
  maxIterations?: number;
};

export type AssertionMatcher =
  | "equals"
  | "contains"
  | "matches"
  | "type"
  | "exists"
  | "status";

export type OutputAssertion = {
  nodeId: string;
  output: {
    equals?: unknown;
    contains?: string;
    matches?: string;
    type?: string;
    exists?: boolean;
  };
};

export type StatusAssertion = {
  nodeId: string;
  status: NodeStatus;
};

export type VariableAssertion = {
  variable: string;
  equals?: unknown;
  contains?: string;
  matches?: string;
  type?: string;
  exists?: boolean;
};

export type Assertion = OutputAssertion | StatusAssertion | VariableAssertion;

export type TestCase = {
  name: string;
  description?: string;
  params?: Record<string, unknown>;
  mockOverrides?: Record<string, MockConfig>;
  assertions: Assertion[];
};

export type DryRunConfig = {
  dryRun: boolean;
  mockFile?: string;
  assertEnabled: boolean;
};

export type MockSource = "default" | "inline" | "external" | "override";

export type AssertionResult = {
  type: "output" | "status" | "variable";
  matcher?: AssertionMatcher;
  path?: string;
  nodeId?: string;
  expected?: unknown;
  actual?: unknown;
  passed: boolean;
  message?: string;
};

export type NodeExecutionReport = {
  nodeId: string;
  nodeStatus: NodeStatus;
  startedAt?: number;
  completedAt?: number;
  duration?: number;
  mockSource: MockSource;
  assertions: AssertionResult[];
};

export type TestCaseReport = {
  name: string;
  description?: string;
  params?: Record<string, unknown>;
  status: "passed" | "failed" | "error";
  duration: number;
  results: NodeExecutionReport[];
  summary: {
    total: number;
    passed: number;
    failed: number;
  };
};

export type TestReport = {
  workflowId: string;
  version: string;
  timestamp: string;
  testCases: TestCaseReport[];
  summary: {
    total: number;
    passed: number;
    failed: number;
  };
  status: "passed" | "failed" | "error";
};

// ── Controller Action ──

export type ControllerAction =
  | { action: "help"; workflowId?: string }
  | { action: "run"; workflowId: string; params: Record<string, string>; message?: string; files: string[]; debug?: boolean; chatInjectLevel?: import("./inject-level.js").InjectLevel }
  | { action: "state"; flowId?: string }
  | { action: "logs"; flowId?: string; limit?: number }
  | {
      action: "runs";
      limit?: number;
      includeHidden?: boolean;
      global?: boolean;
      identityKey?: string;
      workflowId?: string;
      status?: string;
    }
  | { action: "runsCleanup"; identityKey: string; workflowId?: string; status: "failed" }
  | { action: "repairLegacyIdentity"; workflowId: string; flowId?: string; dryRun?: boolean }
  | { action: "repairExternalPackPin"; workflowId: string; flowId?: string; dryRun?: boolean }
  | { action: "detail"; workflowId: string; source?: "pack" | "db" }
  | { action: "validate"; workflowId: string; file?: string }
  | { action: "packs" }
  | { action: "packInspect"; packId: string }
  | { action: "packValidate"; packId: string }
  | { action: "cutoverCheck"; workflowId: string }
  | { action: "confirm"; note?: string; flowId?: string }
  | { action: "retry"; nodeId?: string; flowId?: string; reason?: string; useCurrentDef?: boolean; debug?: boolean; inputOverrides?: Record<string, string> }
  | { action: "submit"; nodeId: string; flowId?: string; resultJson?: string; text?: string }
  | { action: "skip"; nodeId: string; reason: string; flowId?: string; resultJson?: string; runHooks: boolean }
  | { action: "revise"; note: string; nodeId?: string; flowId?: string }
  | { action: "reject"; note?: string; flowId?: string }
  | { action: "reopen"; workflowId: string; params: Record<string, string> }
  | { action: "resume"; flowId: string; revision: number }
  | { action: "bcs-callback"; flowId: string; nodeId: string; result: Record<string, unknown> }
  | { action: "async-callback"; flowId: string; nodeId: string; callbackToken: string; result: Record<string, unknown>; userId?: string }
  | { action: "inspect"; flowId?: string; analyze?: boolean; full?: boolean }
  | { action: "debug"; flowId?: string; full?: boolean }
  | { action: "export"; flowId: string }
  | { action: "import"; token: string }
  | { action: "list"; filter?: string }
  | { action: "schedule"; rawArgs: string }
  | { action: "webhook"; rawArgs: string }
  | { action: "test"; workflowId: string; dryRun: boolean; mockFile?: string; assertEnabled: boolean; json?: boolean }
  | { action: "injectNodes"; flowId: string; sourceNodeId: string; nodes: WorkflowNode[] }
  | { action: "synthesize"; goal: string; model?: string; validateOnly?: boolean; maxCorrections?: number }
  | { action: "deploy"; workflowId: string; file?: string; force?: boolean; note?: string }
  | { action: "install-pack"; packDir: string; only?: string; force?: boolean; move?: boolean }
  | { action: "pull"; workflowId?: string }
  | { action: "rollback"; workflowId: string; version?: number; deployNumber?: number; pack?: boolean; tag?: string; note?: string }
  | { action: "deploys"; workflowId: string; limit?: number; detailVersion?: number; detailDeployNumber?: number }
  | { action: "status"; workflowId?: string; diff?: boolean; gitDiff?: boolean }
  | { action: "share"; workflowId: string; to: string }
  | { action: "unshare"; workflowId: string; from: string }
  | {
      action: "debug-segment";
      workflowId: string;
      fromNode: string;
      toNode?: string;
      nodeOutput: Record<string, Record<string, unknown>>;
      workflowData?: Record<string, unknown>;
      input?: Record<string, unknown>;
    }
  | { action: "dev-workflow-callback"; params: DevWorkflowCallbackParams };

// ── Dev-workflow phase callback types ──

/** Parameters for the dev-workflow phase callback MCP tool. */
export type DevWorkflowCallbackParams = {
  /** Dev-workflow ID (from BOT prompt context). */
  workflowId: string;
  /** Phase ID within the dev-workflow. */
  phaseId: string;
  /** Execution status of the phase. */
  status: "success" | "failed" | "timeout";
  /** Human-readable summary of phase execution result. */
  resultSummary?: string;
  /** URL of the primary output document (e.g., PR link, design doc URL). */
  documentUrl?: string;
  /** Title of the primary output document. */
  documentTitle?: string;
  /** Error message (required when status is not "success"). */
  error?: string;
  /** BaaS run ID for traceability. */
  baasRunId?: string;
  /** Git operations performed during this phase. */
  gitOps?: DevWorkflowGitOp[];
  /** Artifacts produced during this phase. */
  artifacts?: DevWorkflowArtifact[];
};

/** Git operation record for a dev-workflow phase. */
export type DevWorkflowGitOp = {
  /** Type of git operation. */
  operation: "clone" | "pull" | "checkout" | "commit" | "push";
  /** Repository URL. */
  repoUrl: string;
  /** Branch name. */
  branch: string;
  /** Full commit SHA (for commit/push operations). */
  commitSha?: string;
  /** Commit message (for commit operations). */
  commitMessage?: string;
  /** Remote branch name (for push operations, when different from local). */
  remoteBranch?: string;
  /** Human-readable summary of the operation. */
  summary?: string;
  /** Operation result. */
  result: "success" | "failed" | "timeout";
  /** Error message if result is not "success". */
  errorMessage?: string;
  /** User or bot that executed the operation. */
  executedBy?: string;
};

/** Artifact produced during a dev-workflow phase. */
export type DevWorkflowArtifact = {
  /** Artifact type classification. */
  artifactType: string;
  /** Human-readable title. */
  title: string;
  /** Full content of the artifact (lightweight artifacts),
   *  or summary for large artifacts (use contentUrl for full content). */
  content?: string;
  /** URL pointing to the full artifact content (e.g., YuQue doc URL). */
  contentUrl?: string;
  /** Content format. */
  format?: "markdown" | "yaml" | "json" | "html";
  /** Who produced this artifact. */
  source?: "bot" | "human" | "imported";
  /** Author identifier. */
  authoredBy?: string;
};
