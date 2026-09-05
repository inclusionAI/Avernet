import type { ComponentType } from "react";

export declare const Evolve: ComponentType;
export declare const ClawevolveApp: ComponentType;
export declare const BenchAdmin: ComponentType;
export declare const BenchDomains: ComponentType;
export declare const BenchRunDetail: ComponentType;
export declare const BenchTemplateDetail: ComponentType;

export type BenchSessionEvent = {
  type?: string;
  timestamp?: string;
  message?: {
    role?: string;
    content?: Array<Record<string, unknown>>;
    usage?: Record<string, unknown>;
  };
  [key: string]: unknown;
};
export type BenchSessionContentBlock = {
  label: string;
  text: string;
  tone?: "default" | "thinking" | "tool";
};
export declare function eventLabel(event: BenchSessionEvent): string;
export declare function eventPreview(event: BenchSessionEvent): string;
export declare function eventRole(event: BenchSessionEvent): string;
export declare function eventUsageText(event: BenchSessionEvent): string;
export declare function eventContentBlocks(event: BenchSessionEvent): BenchSessionContentBlock[];

export interface ParsedBaselineMarkdown {
  id?: string;
  category?: string;
  grading_type?: string;
  timeout_seconds?: number;
  workspace_files?: string[];
  grading_weights?: { automated?: number; llm_judge?: number };
  sections: Record<string, string>;
}
export declare function parseBaselineMarkdown(content: string): ParsedBaselineMarkdown;
export declare function parsedMarkdownToFormFields(parsed: ParsedBaselineMarkdown): Record<string, string>;

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
