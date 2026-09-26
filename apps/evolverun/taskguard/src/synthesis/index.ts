/**
 * Synthesis module — Layer D: LLM YAML Synthesis.
 *
 * Enables generating complete WorkflowSpec YAML from natural language goals,
 * validated through a three-stage pipeline, then executed by the engine.
 */

export {
  buildSynthesisContext,
  formatCorrectionContext,
  getExecutorDoc,
  getAllowedExecutorDocs,
  type SynthesisContextOptions,
  type ExecutorDoc,
} from "./context-builder.js";

export { loadSynthesisConfig, SYNTHESIS_DEFAULTS_READONLY } from "./config.js";

export { validateSynthesizedYaml, type ValidationResult } from "./validator.js";

export {
  isExecutorAllowed,
  checkWorkflowSecurity,
  detectSecrets,
  detectTemplateInjection,
} from "./sandbox-policy.js";

export { parseLlmYamlOutput, extractFencedCodeBlock, type ParsedYamlResult } from "./yaml-parser.js";

export { synthesize, type SynthesizerDeps } from "./synthesizer.js";

export { checkHumanApprovalNeeded, type HumanGateDecision } from "./human-gate.js";