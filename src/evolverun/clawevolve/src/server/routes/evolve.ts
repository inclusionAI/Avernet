import type { NodeCommandKey } from "../services/evolve/command.js";
import {
  EVOLVE_NODE_REGISTRY,
  EVOLVE_TASK_REGISTRY,
  type EvolveNodeDefinition,
  type EvolveTaskDefinition,
  type EvolveTaskType,
} from "../services/evolve/task-registry.js";

export type EvolveTaskDefinitionsOptions = {
  taskRegistry?: Record<EvolveTaskType, EvolveTaskDefinition>;
  nodeRegistry?: Record<NodeCommandKey, EvolveNodeDefinition>;
  variants?: Record<string, readonly NodeCommandKey[]>;
};

export type EvolveTaskDefinitionsResponse = {
  tasks: Array<{
    type: EvolveTaskType;
    label: string;
    nodes: EvolveNodeDefinition[];
  }>;
  variants: Record<string, EvolveNodeDefinition[]>;
};

export function createTaskDefinitionsResponse(
  options: EvolveTaskDefinitionsOptions = {},
): EvolveTaskDefinitionsResponse {
  const taskRegistry = options.taskRegistry ?? EVOLVE_TASK_REGISTRY;
  const nodeRegistry = options.nodeRegistry ?? EVOLVE_NODE_REGISTRY;
  return {
    tasks: Object.values(taskRegistry).map((definition) => ({
      type: definition.type,
      label: definition.label,
      nodes: definition.nodes.map((key) => ({ ...nodeRegistry[key] })),
    })),
    variants: Object.fromEntries(
      Object.entries(options.variants ?? {}).map(([name, keys]) => [
        name,
        keys.map((key) => ({ ...nodeRegistry[key] })),
      ]),
    ),
  };
}
