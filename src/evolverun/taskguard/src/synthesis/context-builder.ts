/**
 * Synthesis context builder — assembles the LLM prompt for YAML generation.
 *
 * Constructs a structured prompt that includes:
 * 1. System prompt describing the YAML generation task
 * 2. Executor documentation (from normalizeExecutor field requirements)
 * 3. Available node templates from loaded packs
 * 4. Budget constraints
 * 5. Security constraints (sandbox whitelist, node cap)
 * 6. User goal
 * 7. Correction context (if retrying after validation failure)
 * 8. Custom prompt template override (if file exists)
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { ValidationError, SynthesisConfig, WorkflowSpec } from "../types.js";

// ── Executor documentation (static, derived from normalizeWorkflowSpec) ──

/** Executor doc entry used to build the LLM prompt. */
export type ExecutorDoc = {
  type: string;
  required: string[];
  optional: string[];
  example: string;
  constraints?: string[];
};

/**
 * Documentation for each allowed executor type in synthesized workflows.
 *
 * Derived from normalizeExecutor() in src/validation/workflow.ts.
 * Kept in sync manually since normalizer is procedural, not declarative.
 * When a new executor is added to the whitelist, its doc must be added here too.
 */
const EXECUTOR_DOCS: Record<string, ExecutorDoc> = {
  "embedded-agent": {
    type: "embedded-agent",
    required: ["prompt"],
    optional: ["model", "temperature", "history", "contextPolicy", "maxTurns", "tools"],
    example: `executor:
  type: embedded-agent
  prompt: "Analyze the code quality of this project"`,
    constraints: ["prompt must be a non-empty string"],
  },
  done: {
    type: "done",
    required: [],
    optional: ["message"],
    example: `executor:
  type: done
  message: "Analysis complete"`,
    constraints: ["Terminal node — no downstream work"],
  },
  subagent: {
    type: "subagent",
    required: ["prompt"],
    optional: ["model", "temperature", "taskDescription"],
    example: `executor:
  type: subagent
  prompt: "Review security vulnerabilities"
  taskDescription: "Security audit"`,
    constraints: ["Launches a separate agent session"],
  },
  "dynamic-template": {
    type: "dynamic-template",
    required: ["template", "forEach"],
    optional: ["iterationVar", "maxItems", "nodeTemplates"],
    example: `executor:
  type: dynamic-template
  forEach: "{{scan-structure.output.modules}}"
  iterationVar: "module"
  template: review-module
nodeTemplates:
  review-module:
    - id: review
      executor:
        type: embedded-agent
        prompt: "Review module {{module}}"`,
    constraints: [
      "forEach must resolve to an array at runtime",
      "template must reference a key in nodeTemplates",
      "Generated node IDs follow pattern: ${dtNodeId}__item${index}__${bodyNodeId}",
    ],
  },
  "goal-evaluator": {
    type: "goal-evaluator",
    required: ["goal", "evaluator.prompt"],
    optional: ["maxAttempts", "evaluator.model", "evaluator.temperature", "onNotMet"],
    example: `executor:
  type: goal-evaluator
  goal: "All modules have been reviewed"
  evaluator:
    prompt: "Check if all modules from the scan have been reviewed"
  maxAttempts: 2
  onNotMet:
    action: fail
    message: "Not all modules reviewed"`,
    constraints: ["Loops until goal is met or maxAttempts reached", "evaluator.prompt is required inside evaluator object"],
  },
  "llm-orchestrator": {
    type: "llm-orchestrator",
    required: ["goal", "availableActions"],
    optional: ["maxIterations", "budget", "verification"],
    example: `executor:
  type: llm-orchestrator
  goal: "Thoroughly review the codebase"
  maxIterations: 5
  availableActions:
    - name: scan
      executor:
        type: embedded-agent
        prompt: "Scan for issues"
    - name: fix
      executor:
        type: embedded-agent
        prompt: "Fix the identified issue"`,
    constraints: [
      "LLM selects from availableActions at each iteration",
      "Generated node IDs: ${orchestratorId}__step${N}__${actionName}",
      "budget is optional — uses WorkflowSpec budget if not set",
    ],
  },
  "loop-group": {
    type: "loop-group",
    required: ["iterations"],
    optional: ["iterationVar"],
    example: `executor:
  type: loop-group
  iterations: 3
  iterationVar: "round"
nodes:
  - id: check
    executor:
      type: embedded-agent
      prompt: "Check round {{round}}"`,
    constraints: [
      "iterations can be a number or a template resolving to an array",
      "Body nodes are expanded at compile-time",
      "Rewritten node IDs: ${loopId}__iter${N}__${bodyNodeId}",
    ],
  },
  action: {
    type: "action",
    required: ["actionName"],
    optional: ["params"],
    example: `executor:
  type: action
  actionName: "run-script"
  params:
    script: "echo hello"`,
    constraints: ["Runs a registered action from the action registry"],
  },
};

// ── Prompt template ──

const BUILTIN_SYSTEM_PROMPT = `You are a ClawMind workflow YAML generator. Given a user goal, generate a complete WorkflowSpec YAML.

## Output Format
Output the complete WorkflowSpec YAML wrapped in a \`\`\`yaml ... \`\`\` code block.
The YAML MUST include these top-level fields:
- id (kebab-case, descriptive)
- version: 1
- title (human-readable)
- nodes (array of node objects)

Each node MUST have:
- id (kebab-case, unique within the workflow)
- title (human-readable)
- phase (e.g., "main")
- executor (object with "type" and type-specific fields)
- dependsOn (array of node IDs this node depends on, empty array for root nodes)

## Available Executor Types
{EXECUTOR_DOCS}

## Available Node Templates
{NODE_TEMPLATES}

## Budget Constraints
{BUDGET_CONSTRAINTS}

## Security Constraints
- Only the following executor types are allowed: {ALLOWED_EXECUTORS}
- Node IDs must be kebab-case (lowercase, hyphens, no spaces)
- dependsOn must reference IDs of nodes defined in the same workflow
- Maximum node count: {MAX_NODE_COUNT}
- Do NOT use yaml-synthesizer as an executor type

## Template Resolution
- Use {{nodeId.output}} to reference another node's output
- Use {{nodeId.output.field}} for nested access
- Use {{path | default: fallback}} for default values
- Only reference nodes that appear in dependsOn (guaranteed to have completed)`;

const CORRECTION_CONTEXT_TEMPLATE = `

## Previous Attempt Errors ({ERROR_COUNT} errors)
{STRUCTURED_ERRORS}
Please fix the above errors and regenerate the complete YAML.`;

// ── Public types ──

export type SynthesisContextOptions = {
  goal: string;
  config: SynthesisConfig;
  /** Node templates from loaded packs (key=templateName, value=body summary). */
  packTemplates?: Record<string, string>;
  /** Budget config from dynamicWorkflow.budget section. */
  budgetConfig?: { maxTokens?: number; maxIterations?: number; strategy?: string };
  /** Validation errors from a previous attempt (for correction context). */
  correctionErrors?: ValidationError[];
  /** Override the config file path for custom prompt template. */
  customTemplatePath?: string;
};

// ── Public API ──

/**
 * Build the complete LLM prompt for YAML synthesis.
 *
 * Returns an object with `system` and `user` strings ready for the LLM call.
 */
export function buildSynthesisContext(opts: SynthesisContextOptions): {
  system: string;
  user: string;
} {
  const { goal, config, packTemplates, budgetConfig, correctionErrors, customTemplatePath } = opts;

  // 1. Build executor documentation section
  const executorDocsSection = buildExecutorDocsSection(config.allowedExecutors);

  // 2. Build node templates section
  const nodeTemplatesSection = buildNodeTemplatesSection(packTemplates);

  // 3. Build budget constraints section
  const budgetSection = buildBudgetSection(budgetConfig);

  // 4. Build system prompt
  let systemPrompt = loadCustomTemplate(customTemplatePath) ?? BUILTIN_SYSTEM_PROMPT;

  systemPrompt = systemPrompt
    .replace("{EXECUTOR_DOCS}", executorDocsSection)
    .replace("{NODE_TEMPLATES}", nodeTemplatesSection)
    .replace("{BUDGET_CONSTRAINTS}", budgetSection)
    .replace("{ALLOWED_EXECUTORS}", config.allowedExecutors.join(", "))
    .replace("{MAX_NODE_COUNT}", String(config.maxNodeCount));

  // 5. Build user prompt (goal + correction context)
  let userPrompt = `Goal: ${goal}`;
  if (correctionErrors && correctionErrors.length > 0) {
    userPrompt += buildCorrectionContext(correctionErrors);
  }

  return { system: systemPrompt, user: userPrompt };
}

// ── Executor documentation ──

/** Get the executor documentation for a specific type. */
export function getExecutorDoc(type: string): ExecutorDoc | undefined {
  return EXECUTOR_DOCS[type];
}

/** Get all executor docs for the allowed types. */
export function getAllowedExecutorDocs(allowedExecutors: string[]): ExecutorDoc[] {
  return allowedExecutors
    .map((t) => EXECUTOR_DOCS[t])
    .filter((d): d is ExecutorDoc => d !== undefined);
}

function buildExecutorDocsSection(allowedExecutors: string[]): string {
  const docs = getAllowedExecutorDocs(allowedExecutors);
  if (docs.length === 0) {
    return "(No executor types available — generate all nodes with type: done)";
  }

  return docs
    .map((doc) => {
      const reqStr = doc.required.length > 0 ? doc.required.join(", ") : "(none)";
      const optStr = doc.optional.length > 0 ? doc.optional.join(", ") : "(none)";
      const constraintsStr =
        doc.constraints && doc.constraints.length > 0
          ? `\n   Constraints: ${doc.constraints.join("; ")}`
          : "";
      return `### ${doc.type}
- Required: ${reqStr}
- Optional: ${optStr}
- Example:
  ${doc.example}${constraintsStr}`;
    })
    .join("\n\n");
}

// ── Node templates aggregation ──

function buildNodeTemplatesSection(packTemplates?: Record<string, string>): string {
  if (!packTemplates || Object.keys(packTemplates).length === 0) {
    return "No templates available — generate all node bodies inline.";
  }

  return Object.entries(packTemplates)
    .map(([name, summary]) => `- ${name}: ${summary}`)
    .join("\n");
}

// ── Budget constraints ──

function buildBudgetSection(
  budgetConfig?: SynthesisContextOptions["budgetConfig"],
): string {
  if (!budgetConfig) {
    return "No budget constraints specified.";
  }

  const parts: string[] = [];
  if (budgetConfig.maxTokens) parts.push(`maxTokens=${budgetConfig.maxTokens}`);
  if (budgetConfig.maxIterations) parts.push(`maxIterations=${budgetConfig.maxIterations}`);
  if (budgetConfig.strategy) parts.push(`strategy=${budgetConfig.strategy}`);

  if (parts.length === 0) return "No budget constraints specified.";

  return `${parts.join(", ")}. Include a 'budget' field in the generated YAML if the workflow may consume significant tokens.`;
}

// ── Correction context ──

/** Format validation errors into structured text for the correction prompt. */
export function formatCorrectionContext(errors: ValidationError[]): string {
  return errors
    .map((e, i) => {
      const sevStr = e.severity === "warning" ? " (warning)" : "";
      const suggStr = e.suggestion ? `\n   Suggestion: ${e.suggestion}` : "";
      return `${i + 1}. [${e.stage}] ${e.path}: ${e.message}${sevStr}${suggStr}`;
    })
    .join("\n");
}

function buildCorrectionContext(errors: ValidationError[]): string {
  const structured = formatCorrectionContext(errors);
  return CORRECTION_CONTEXT_TEMPLATE.replace("{ERROR_COUNT}", String(errors.length)).replace(
    "{STRUCTURED_ERRORS}",
    structured,
  );
}

// ── Custom prompt template ──

function loadCustomTemplate(customPath?: string): string | null {
  const path = customPath ?? join(process.cwd(), "configs", "synthesis-prompt-template.md");
  if (!existsSync(path)) return null;
  try {
    return readFileSync(path, "utf-8");
  } catch {
    return null;
  }
}