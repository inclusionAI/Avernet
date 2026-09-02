import type { ComponentType } from "react";

export declare const Evolve: ComponentType;
export declare const ClawevolveApp: ComponentType;

export type EvolveTaskType =
  | "diagnose"
  | "optimize"
  | "apply"
  | "full"
  | "bench"
  | "bench_optimize"
  | "pack"
  | "pack_restore"
  | "runtime_cleanup"
  | "repair";

export declare const evolveTaskTypes: readonly EvolveTaskType[];
export declare const evolveTaskRegistry: Record<EvolveTaskType, {
  type: EvolveTaskType;
  label: string;
}>;
export declare const evolveBranches: readonly {
  key: string;
  label: string;
  taskType: EvolveTaskType;
  status: string;
}[];
export declare function isEvolveTaskType(value: unknown): value is EvolveTaskType;
