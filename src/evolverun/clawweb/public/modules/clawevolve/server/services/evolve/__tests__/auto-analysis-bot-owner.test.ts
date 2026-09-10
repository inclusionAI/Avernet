import { afterEach, describe, expect, it, vi } from "vitest";
import { createFailedRunAutoAnalysisObserver } from "../run-analysis-starter.js";

afterEach(() => vi.restoreAllMocks());

describe("failed-run automatic analysis identity", () => {
  it.each([
    ["gateway-client", "20260508_2k59gf2s:331844", "331844"],
    ["another-caller", "bot-1:331844", "331844"],
    [null, "bot-1:331844", "331844"],
    ["331844", null, "331844"],
    ["331844", "bot-1", "331844"],
    ["331844", "bot-1: ", "331844"],
    ["331844", ":another-owner", "331844"],
    ["331844", "bot-1:another-owner:extra", "331844"],
    ["gateway-client", " bot-1:331844 ", "331844"],
  ])("resolves caller %s and origin %s to owner %s", async (userId, originBotId, expectedOwner) => {
    const run = { workflow_id: "wf-1", origin_bot_id: originBotId, user_id: userId };
    const start = vi.fn().mockResolvedValue({ ok: true });
    const observer = createFailedRunAutoAnalysisObserver({
      flowRunRepo: { findByFlowId: async () => run },
      starter: { start },
      isWorkflowEnabled: async () => true,
    });

    await observer({ flowId: "flow-1", status: "failed" });

    expect(start).toHaveBeenCalledExactlyOnceWith({ flowId: "flow-1", userId: expectedOwner });
    expect(run.user_id).toBe(userId);
  });

  it.each(["running", "waiting", "succeeded", "canceled"])("does not analyze %s runs", async (status) => {
    const start = vi.fn();
    const observer = createFailedRunAutoAnalysisObserver({
      flowRunRepo: { findByFlowId: async () => ({ workflow_id: "wf-1", origin_bot_id: "bot:331844", user_id: "gateway-client" }) },
      starter: { start },
      isWorkflowEnabled: async () => true,
    });
    await observer({ flowId: "flow-1", status });
    expect(start).not.toHaveBeenCalled();
  });

  it("does not bypass the workflow switch", async () => {
    const start = vi.fn();
    const observer = createFailedRunAutoAnalysisObserver({
      flowRunRepo: { findByFlowId: async () => ({ workflow_id: "wf-1", origin_bot_id: "bot:331844", user_id: "gateway-client" }) },
      starter: { start },
      isWorkflowEnabled: async () => false,
    });
    await observer({ flowId: "flow-1", status: "failed" });
    expect(start).not.toHaveBeenCalled();
  });

  it("skips runs without an owner or caller identity", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const start = vi.fn();
    const observer = createFailedRunAutoAnalysisObserver({
      flowRunRepo: { findByFlowId: async () => ({ workflow_id: "wf-1", origin_bot_id: "bot:", user_id: null }) },
      starter: { start },
      isWorkflowEnabled: async () => true,
    });
    await observer({ flowId: "flow-1", status: "failed" });
    expect(start).not.toHaveBeenCalled();
  });
});
