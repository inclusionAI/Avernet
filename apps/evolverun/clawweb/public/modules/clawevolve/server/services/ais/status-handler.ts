import type { EvolveStepRow, EvolveTaskRow } from "../../repositories/evolve-repository.js";

/** Validated Base execution report, independent of business task lifecycle. */
export type AisStatusReport = {
  status: "running" | "succeeded" | "failed";
  summary?: string;
  output?: Record<string, unknown>;
  errorCode?: string;
  errorMessage?: string;
  retryable?: boolean;
};

/** Registered by the Host. The handler owns atomic execution updates and duplicate handling. */
export type AisStatusHandler = (input: {
  task: EvolveTaskRow;
  step: EvolveStepRow;
  report: AisStatusReport;
}) => Promise<{ applied: boolean; duplicate?: boolean }>;
