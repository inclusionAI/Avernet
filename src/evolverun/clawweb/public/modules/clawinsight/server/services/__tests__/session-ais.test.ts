import { describe, expect, it } from "vitest";
import { validateAisResult } from "../../routes/session-analysis.js";

describe("session attachments", () => {
  const config = { taskId: "SA-1", mode: "EXPORT_SINGLE" as const, stage: "all" as const,
    engineType: "openclaw" as const, attempt: 1,
    aisBase: { snapshotId: 1, packageId: "diagnose", deadlineAt: 100 },
    artifacts: Object.fromEntries(["raw", "manifest", "result", "trajectory", "trajectoryPath"].map(name => [name, { objectKey: name }])),
  };
  const meta = (name: string, size = 10) => ({ objectKey: name, size, sha256: "a".repeat(64),
    contentType: ["raw", "trajectory"].includes(name) ? "application/x-ndjson" : "application/json" });
  it("does not require missing optional files", () => {
    expect(() => validateAisResult({ taskId: "SA-1", analysisId: "SA-1", success: true,
      artifacts: { raw: meta("raw"), manifest: meta("manifest"), result: meta("result") } }, config)).not.toThrow();
  });
  it("accepts empty optional evidence on failure but rejects a foreign key", () => {
    const payload = { taskId: "SA-1", analysisId: "SA-1", success: false,
      artifacts: { trajectory: meta("trajectory", 0) } };
    expect(() => validateAisResult(payload, config, true)).not.toThrow();
    payload.artifacts.trajectory.objectKey = "other-task";
    expect(() => validateAisResult(payload, config, true)).toThrow();
  });
});
