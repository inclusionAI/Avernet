import { describe, expect, it, vi } from "vitest";
import type { EvolveRepository } from "../../../repositories/evolve-repository.js";
import { reconcileSessionAis } from "../session-ais-monitor.js";

describe("session analysis AIS monitor", () => {
  it("marks a terminated Base job failed through the current Session step", async () => {
    const task = { id: 7, task_id: "SA-1", config_json: JSON.stringify({
      stepId: "SA-1-AIS",
      aisBase: { snapshotId: 17, packageId: "clawevolve-ais-diagnose", deadlineAt: 10_000 },
    }) };
    const apply = vi.fn().mockResolvedValue(true);
    const list = vi.fn().mockResolvedValueOnce([task]).mockResolvedValueOnce([]);
    const repo = {
      listActiveSessionAisTasks: list,
      findStep: vi.fn().mockResolvedValue({ step_id: "SA-1-AIS", status: "running", bot_run_id: "job-1" }),
      applySessionAisStatus: apply,
    } as unknown as EvolveRepository;
    const ais = {
      getJobStatusDetail: vi.fn().mockResolvedValue({
        status: "failed" as const, rawStatus: "failed", errorMessage: null,
      }),
      stopExecution: vi.fn(),
    };
    await reconcileSessionAis(repo, ais, 100);
    expect(list).toHaveBeenCalledWith(0, 100);
    expect(apply).toHaveBeenCalledWith("SA-1", "SA-1-AIS", expect.objectContaining({
      status: "failed", errorCode: "AIS_JOB_FAILED",
    }));
  });

  it("does not fail a task when the platform status query is temporarily unavailable", async () => {
    const task = { id: 8, task_id: "SA-2", config_json: JSON.stringify({
      stepId: "SA-2-AIS",
      aisBase: { snapshotId: 17, packageId: "clawevolve-ais-diagnose", deadlineAt: 10_000 },
    }) };
    const apply = vi.fn();
    const repo = {
      listActiveSessionAisTasks: vi.fn().mockResolvedValueOnce([task]).mockResolvedValueOnce([]),
      findStep: vi.fn().mockResolvedValue({ step_id: "SA-2-AIS", status: "running", bot_run_id: "job-2" }),
      applySessionAisStatus: apply,
    } as unknown as EvolveRepository;
    await reconcileSessionAis(repo, {
      getJobStatusDetail: vi.fn().mockRejectedValue(new Error("temporary")),
      stopExecution: vi.fn(),
    }, 100);
    expect(apply).not.toHaveBeenCalled();
  });
});
