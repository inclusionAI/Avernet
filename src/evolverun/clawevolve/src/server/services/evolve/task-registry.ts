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
};

export function taskNodeKeys(type: EvolveTaskType): readonly NodeCommandKey[] {
  return EVOLVE_TASK_REGISTRY[type].nodes;
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
