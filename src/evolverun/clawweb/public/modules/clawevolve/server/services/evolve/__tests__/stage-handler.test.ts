import { describe, expect, it } from "vitest";
import { coreStageRequiresAgentMessage } from "../stage-handler.js";

function task(stage: "diagnose" | "plan" | "hardening") {
  return {
    config_json: JSON.stringify({
      stageExtensions: {
        [stage]: {
          replace: { enabled: true, implementationId: `IMPL-${stage}` },
        },
      },
    }),
  };
}

describe("coreStageRequiresAgentMessage", () => {
  it("keeps Diagnose replacement inside its native Handler Runner", () => {
    expect(coreStageRequiresAgentMessage(task("diagnose"), "diagnose")).toBe(false);
  });

  it("keeps Hardening replacement inside its native Handler Runner", () => {
    expect(coreStageRequiresAgentMessage(task("hardening"), "hardening")).toBe(false);
  });

  it("keeps Plan replacement inside its native Handler Runner", () => {
    expect(coreStageRequiresAgentMessage(task("plan"), "plan")).toBe(false);
  });
});
