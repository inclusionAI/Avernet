import type { NodeCommandKey } from "./command.js";

export const EVOLVE_TASK_TYPES = [
  "diagnose",
  "optimize",
  "apply",
  "full",
  "bench",
  "bench_optimize",
  "pack",
  "pack_restore",
  "runtime_cleanup",
  "repair",
  "suggestion_apply",
  "run_analysis",
] as const;

export type EvolveTaskType = typeof EVOLVE_TASK_TYPES[number];

export const EVOLVE_STEP_TYPES = [
  "skill_init",
  "diagnose",
  "plan",
  "optimize",
  "apply",
  "bench",
  "bench_plan",
  "pack",
  "restore",
  "runtime_cleanup",
  "repair_plan",
  "repair_apply",
  "suggestion_apply",
  "run_analysis",
] as const;

export type EvolveStepType = typeof EVOLVE_STEP_TYPES[number];

export type EvolveTaskDefinition = {
  type: EvolveTaskType;
  label: string;
  initialStepType: EvolveStepType;
  supportsRetry: boolean;
  supportsCancel: boolean;
  nodes: readonly NodeCommandKey[];
};

export type EvolveNodeDefinition = {
  key: NodeCommandKey;
  label: string;
  defaultCommand: string;
};

export type EvolveNodeCommandOverrides = Partial<Record<NodeCommandKey, string>>;

const PUBLIC_NODE_COMMANDS: Record<NodeCommandKey, string> = {
  diagnose: "/clawevolve-diagnose --api-key {{api_key}} --model {{model}} --intent {{diagnose_intent}}",
  plan: "/clawevolve-plan",
  bench: "/clawevolve-bench --model {{model}} --suite all",
  bench_plan: "/clawevolve-workflow --stage bench-plan --model {{model}} --suite all",
  optimize: "/clawevolve-workflow --stage optimize --model {{model}} --suite all",
};

const NODE_LABELS: Record<NodeCommandKey, string> = {
  diagnose: "Bot 诊断",
  plan: "进化规划",
  bench: "Bench 评测",
  bench_plan: "Baseline 与 Spec v0",
  optimize: "每轮优化",
};

export function createEvolveNodeRegistry(
  overrides: EvolveNodeCommandOverrides = {},
): Record<NodeCommandKey, EvolveNodeDefinition> {
  return Object.fromEntries(
    (Object.keys(PUBLIC_NODE_COMMANDS) as NodeCommandKey[]).map((key) => [key, {
      key,
      label: NODE_LABELS[key],
      defaultCommand: overrides[key] ?? PUBLIC_NODE_COMMANDS[key],
    }]),
  ) as Record<NodeCommandKey, EvolveNodeDefinition>;
}

export const EVOLVE_NODE_REGISTRY = createEvolveNodeRegistry();

export const EVOLVE_TASK_REGISTRY: Record<EvolveTaskType, EvolveTaskDefinition> = {
  diagnose: { type: "diagnose", label: "Bot诊断", initialStepType: "diagnose", supportsRetry: true, supportsCancel: true, nodes: ["diagnose", "plan"] },
  optimize: { type: "optimize", label: "诊断后优化", initialStepType: "optimize", supportsRetry: true, supportsCancel: true, nodes: ["optimize"] },
  apply: { type: "apply", label: "应用优化", initialStepType: "apply", supportsRetry: true, supportsCancel: true, nodes: [] },
  full: { type: "full", label: "Bot自进化", initialStepType: "diagnose", supportsRetry: true, supportsCancel: true, nodes: ["diagnose", "plan", "optimize"] },
  bench: { type: "bench", label: "Bench诊断", initialStepType: "bench", supportsRetry: true, supportsCancel: true, nodes: ["bench"] },
  bench_optimize: { type: "bench_optimize", label: "Bench优化", initialStepType: "bench_plan", supportsRetry: true, supportsCancel: true, nodes: ["bench_plan", "optimize"] },
  pack: { type: "pack", label: "创建Pack", initialStepType: "pack", supportsRetry: true, supportsCancel: true, nodes: [] },
  pack_restore: { type: "pack_restore", label: "应用Pack", initialStepType: "restore", supportsRetry: true, supportsCancel: true, nodes: [] },
  runtime_cleanup: { type: "runtime_cleanup", label: "任务清理", initialStepType: "runtime_cleanup", supportsRetry: true, supportsCancel: false, nodes: [] },
  repair: { type: "repair", label: "Bot修复", initialStepType: "repair_plan", supportsRetry: false, supportsCancel: false, nodes: [] },
  suggestion_apply: { type: "suggestion_apply", label: "应用进化建议", initialStepType: "suggestion_apply", supportsRetry: true, supportsCancel: false, nodes: [] },
  run_analysis: { type: "run_analysis", label: "运行日志分析", initialStepType: "run_analysis", supportsRetry: true, supportsCancel: false, nodes: [] },
};

export const INSIGHT_IMPROVEMENT_NODES = ["plan", "optimize"] as const satisfies readonly NodeCommandKey[];

export function taskNodeKeys(
  type: EvolveTaskType,
  source?: "insight_improvement",
): readonly NodeCommandKey[] {
  return source === "insight_improvement" ? INSIGHT_IMPROVEMENT_NODES : EVOLVE_TASK_REGISTRY[type].nodes;
}

export function defaultNodeCommand(
  key: NodeCommandKey,
  registry: Record<NodeCommandKey, EvolveNodeDefinition> = EVOLVE_NODE_REGISTRY,
): string {
  return registry[key].defaultCommand;
}

export function isEvolveTaskType(value: unknown): value is EvolveTaskType {
  return typeof value === "string" && value in EVOLVE_TASK_REGISTRY;
}

const EVOLVE_STEP_REGISTRY: Record<EvolveStepType, { baasStage: string; usesBaasRuntime: boolean }> = {
  skill_init: { baasStage: "skill-init", usesBaasRuntime: false },
  diagnose: { baasStage: "clawevolve-diagnose", usesBaasRuntime: true },
  plan: { baasStage: "clawevolve-plan", usesBaasRuntime: true },
  optimize: { baasStage: "optimize", usesBaasRuntime: true },
  apply: { baasStage: "clawevolve-apply", usesBaasRuntime: false },
  bench: { baasStage: "clawevolve-bench", usesBaasRuntime: true },
  bench_plan: { baasStage: "bench-plan", usesBaasRuntime: true },
  pack: { baasStage: "clawevolve-pack", usesBaasRuntime: true },
  restore: { baasStage: "clawevolve-pack", usesBaasRuntime: true },
  runtime_cleanup: { baasStage: "runtime-cleanup", usesBaasRuntime: true },
  repair_plan: { baasStage: "repair-plan", usesBaasRuntime: false },
  repair_apply: { baasStage: "repair-apply", usesBaasRuntime: false },
  suggestion_apply: { baasStage: "suggestion-apply", usesBaasRuntime: false },
  run_analysis: { baasStage: "run-analysis", usesBaasRuntime: false },
};

export function stepUsesBaasRuntime(stepType: string): boolean {
  return EVOLVE_STEP_REGISTRY[stepType as EvolveStepType]?.usesBaasRuntime === true;
}

export function evolveStepBaasStage(stepType: string): string | undefined {
  return EVOLVE_STEP_REGISTRY[stepType as EvolveStepType]?.baasStage;
}
