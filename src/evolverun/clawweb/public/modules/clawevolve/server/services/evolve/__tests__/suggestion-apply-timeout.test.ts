import { describe, expect, it, vi } from "vitest";
import type { EvolveRepository } from "../../../repositories/evolve-repository.js";
import { classifySuggestionApplyTimeout, runSuggestionApplyTimeoutSweep } from "../suggestion-apply-timeout.js";

describe("suggestion application timeout", () => {
  const now = Date.parse("2026-09-02T12:00:00.000Z");

  it("fails only after 15 minutes of execution and 3 minutes without heartbeat", () => {
    expect(classifySuggestionApplyTimeout({
      nowMs: now,
      startedAtMs: now - 16 * 60_000,
      updatedAtMs: now - 4 * 60_000,
      phase: "editing_workflow",
    })).toMatchObject({ timedOut: true, errorCode: "SUGGESTION_APPLY_TIMEOUT" });

    expect(classifySuggestionApplyTimeout({
      nowMs: now,
      startedAtMs: now - 16 * 60_000,
      updatedAtMs: now - 60_000,
      phase: "editing_workflow",
    })).toEqual({ timedOut: false });
  });

  it("uses an absolute 60 minute ceiling and reports an unknown deploy outcome", () => {
    expect(classifySuggestionApplyTimeout({
      nowMs: now,
      startedAtMs: now - 61 * 60_000,
      updatedAtMs: now - 10_000,
      phase: "deploying",
    })).toMatchObject({ timedOut: true, errorCode: "SUGGESTION_APPLY_RESULT_TIMEOUT", resultUnknown: true });
  });

  it("recovers stale running tasks after a process restart and makes them retryable", async () => {
    const repo = {
      listActiveSuggestionApplySteps: vi.fn().mockResolvedValue([{
        task_id: "EV-1", step_id: "EV-1-step-apply", status: "running",
        started_at: now - 20 * 60_000, gmt_create: now - 20 * 60_000, gmt_modified: now - 5 * 60_000,
        output_json: JSON.stringify({ applicationProgress: { phase: "editing_workflow", updatedAtMs: now - 5 * 60_000 } }),
        config_json: JSON.stringify({ workflowId: "wf-1", suggestionIds: ["1"] }),
      }]),
      tryFinalizeSuggestionApplication: vi.fn().mockResolvedValue({ settled: true, supersededSuggestionIds: [] }),
      findSuggestionById: vi.fn().mockResolvedValue({ id: 1, node_id: "n1", failure_signature: "sig-1" }),
      updateDiagnosesSuggestionStatus: vi.fn().mockResolvedValue(undefined),
    } as unknown as EvolveRepository;

    expect(await runSuggestionApplyTimeoutSweep(repo, now)).toBe(1);
    expect(repo.tryFinalizeSuggestionApplication).toHaveBeenCalledWith(
      "EV-1-step-apply",
      expect.objectContaining({
        source: "timeout",
        status: "failed",
        errorCode: "SUGGESTION_APPLY_TIMEOUT",
        suggestionIds: ["1"],
        failureVerdict: "application_timeout",
      }),
    );
  });
});
