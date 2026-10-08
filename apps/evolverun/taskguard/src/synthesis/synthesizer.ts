/**
 * Synthesizer core engine — implements the LLM YAML synthesis pipeline.
 *
 * Flow:
 *   1. Goal length validation
 *   2. Build LLM prompt (context-builder)
 *   3. Call LLM → parse output (yaml-parser)
 *   4. Three-stage validation (validator)
 *   5. If validation fails, build correction context → re-call LLM (max N rounds)
 *   6. Return SynthesisResult
 *
 * If budget is exhausted mid-loop, terminates early.
 * If LLM is unavailable, returns an error result immediately.
 */
import type { SynthesisConfig, SynthesisResult, TokenUsage, ValidationError } from "../types.js";
import { loadSynthesisConfig } from "./config.js";
import { buildSynthesisContext, formatCorrectionContext } from "./context-builder.js";
import { parseLlmYamlOutput } from "./yaml-parser.js";
import { validateSynthesizedYaml, type ValidationResult } from "./validator.js";
import { callLlm, checkLlmAvailability } from "../llm/client.js";
import type { DynamicWorkflowEventEmitter } from "../observability/emitter.js";

// ── Public types ──

/** Dependencies needed by the synthesizer (injected for testability). */
export type SynthesizerDeps = {
  /** Resolve pack nodeTemplates for prompt context. Optional. */
  getPackTemplates?: () => Record<string, string>;
  /** Budget config for prompt context. Optional. */
  budgetConfig?: { maxTokens?: number; maxIterations?: number; strategy?: string };
  /** Start timestamp for budget tracking. */
  startedAtMs: number;
  /** Track token consumption. Called after each LLM call. */
  recordTokens?: (count: number) => void;
  /** Observability emitter for synthesis events. Optional. */
  emitter?: DynamicWorkflowEventEmitter;
  /** Flow ID for observability events (uses synthetic ID when not in a flow). Optional. */
  flowId?: string;
  /** Workflow ID for observability events. Optional. */
  workflowId?: string;
};

// ── Public API ──

/**
 * Synthesize a WorkflowSpec from a natural language goal.
 *
 * This is the main entry point for the L4 YAML synthesis pipeline.
 * It loops up to `maxCorrections` times: generate → validate → correct.
 */
export async function synthesize(
  goal: string,
  config: SynthesisConfig,
  deps: SynthesizerDeps,
): Promise<SynthesisResult> {
  // ── 0. Goal length check ──
  if (goal.length > config.maxGoalLength) {
    return {
      success: false,
      correctionRounds: 0,
      llmUsage: { input: 0, output: 0, totalTokens: 0 },
      llmModel: "",
      validationErrors: [
        {
          stage: "schema",
          path: "goal",
          message: `Goal length (${goal.length}) exceeds maximum (${config.maxGoalLength})`,
          suggestion: "Shorten the goal description or increase maxGoalLength in configuration",
        },
      ],
    };
  }

  // ── 1. LLM availability check ──
  const availability = checkLlmAvailability();
  if (!availability.available) {
    return {
      success: false,
      correctionRounds: 0,
      llmUsage: { input: 0, output: 0, totalTokens: 0 },
      llmModel: "",
      validationErrors: [
        {
          stage: "schema",
          path: "llm",
          message: `LLM not available: ${availability.reason}`,
          suggestion: "Set LLM_BASE_URL and LLM_API_KEY environment variables",
        },
      ],
    };
  }

  // ── 2. Synthesis loop ──
  const maxCorrections = config.defaultMaxCorrections;
  const model = config.defaultModel || undefined; // let callLlm use LLM_MODEL env

  let totalUsage: TokenUsage = { input: 0, output: 0, totalTokens: 0 };
  let correctionRounds = 0;
  let lastRawYaml: string | undefined;
  let lastValidation: ValidationResult | undefined;
  let usedModel = "";

  while (correctionRounds <= maxCorrections) {
    // 2a. Build prompt
    let correctionErrors: ValidationError[] | undefined;
    if (correctionRounds > 0 && lastValidation) {
      correctionErrors = [...lastValidation.errors, ...lastValidation.warnings];
    }

    const packTemplates = deps.getPackTemplates?.() ?? {};
    const context = buildSynthesisContext({
      goal,
      config,
      packTemplates,
      budgetConfig: deps.budgetConfig,
      correctionErrors,
    });

    // 2b. Call LLM
    let llmResult: Awaited<ReturnType<typeof callLlm>>;
    try {
      llmResult = await callLlm({
        systemPrompt: context.system,
        userPrompt: context.user,
        model,
        temperature: config.defaultTemperature,
        maxTokens: config.defaultMaxTokens,
        jsonMode: false,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        success: false,
        rawYaml: lastRawYaml,
        correctionRounds,
        llmUsage: totalUsage,
        llmModel: usedModel,
        validationErrors: lastValidation?.errors ?? [
          {
            stage: "schema",
            path: "llm",
            message: `LLM call failed: ${message}`,
            suggestion: "Check LLM_BASE_URL, LLM_API_KEY, and network connectivity",
          },
        ],
      };
    }

    // Track usage
    usedModel = llmResult.model;
    const usage = llmResult.usage ?? { promptTokens: 0, completionTokens: 0, totalTokens: 0 };
    totalUsage = {
      input: (totalUsage.input ?? 0) + usage.promptTokens,
      output: (totalUsage.output ?? 0) + usage.completionTokens,
      totalTokens: (totalUsage.totalTokens ?? 0) + usage.totalTokens,
    };
    deps.recordTokens?.(usage.totalTokens);

    // Emit yaml_synthesized event
    const obsFlowId = deps.flowId ?? "synthesis-standalone";
    const obsWorkflowId = deps.workflowId ?? "yaml-synthesizer";
    deps.emitter?.emitYamlSynthesized(obsFlowId, obsWorkflowId, "yaml-synthesizer", {
      round: correctionRounds,
      model: usedModel,
      tokenUsage: usage.totalTokens,
      goalLength: goal.length,
    }).catch(() => { /* best-effort */ });

    lastRawYaml = llmResult.content;

    // 2c. Parse LLM output
    const parseResult = parseLlmYamlOutput(llmResult.content);
    if (parseResult.parseError) {
      // Parse failure counts as a validation error
      lastValidation = {
        valid: false,
        errors: [
          {
            stage: "schema",
            path: "rawYaml",
            message: parseResult.parseError,
            suggestion: "The LLM output did not contain valid YAML or JSON",
          },
        ],
        warnings: [],
      };
      // Emit synthesis_rejected event for parse failure
      deps.emitter?.emitSynthesisRejected(obsFlowId, obsWorkflowId, "yaml-synthesizer", {
        round: correctionRounds,
        errorCount: 1,
        errorStages: ["schema"],
      }).catch(() => { /* best-effort */ });

      correctionRounds++;
      if (correctionRounds > maxCorrections) break;
      continue;
    }

    // 2d. Validate
    lastValidation = validateSynthesizedYaml(parseResult.parsed, config);

    if (lastValidation.valid) {
      // Emit synthesis_validated event
      deps.emitter?.emitSynthesisValidated(obsFlowId, obsWorkflowId, "yaml-synthesizer", {
        round: correctionRounds,
        nodeCount: (parseResult.parsed as import("../types.js").WorkflowSpec).nodes?.length ?? 0,
        warningCount: lastValidation.warnings.length,
      }).catch(() => { /* best-effort */ });

      return {
        success: true,
        workflowSpec: parseResult.parsed as import("../types.js").WorkflowSpec,
        rawYaml: lastRawYaml,
        validationErrors: [],
        correctionRounds,
        llmUsage: totalUsage,
        llmModel: usedModel,
      };
    }

    // Validation failed — increment round and loop for correction
    // Emit synthesis_rejected event
    const errorStages = [...new Set(lastValidation.errors.map(e => e.stage))];
    deps.emitter?.emitSynthesisRejected(obsFlowId, obsWorkflowId, "yaml-synthesizer", {
      round: correctionRounds,
      errorCount: lastValidation.errors.length,
      errorStages,
    }).catch(() => { /* best-effort */ });

    correctionRounds++;
  }

  // ── 3. Exhausted correction rounds ──
  return {
    success: false,
    workflowSpec: undefined,
    rawYaml: lastRawYaml,
    validationErrors: lastValidation?.errors ?? [],
    correctionRounds,
    llmUsage: totalUsage,
    llmModel: usedModel,
  };
}