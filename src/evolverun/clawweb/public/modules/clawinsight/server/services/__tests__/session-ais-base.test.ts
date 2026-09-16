import { describe, expect, it } from "vitest";
import { validateAisResult } from "../../routes/session-analysis.js";

describe("session analysis AIS base attachments", () => {
  const config = { taskId: "SA-1", mode: "ANALYZE_SINGLE" as const, stage: "all" as const,
    engineType: "openclaw" as const, attempt: 1,
    aisBase: { snapshotId: 1, packageId: "diagnose", deadlineAt: 100 },
    artifacts: Object.fromEntries(["raw", "manifest", "report", "analysis", "result", "trajectory",
      "trajectoryPath", "runtimeBundle", "openclawSessions"].map(name => [name, { objectKey: name }])),
  };
  const contentType = (name: string) => ["raw", "trajectory"].includes(name) ? "application/x-ndjson"
    : name === "report" ? "text/markdown; charset=utf-8"
      : ["runtimeBundle", "openclawSessions"].includes(name) ? "application/gzip" : "application/json";
  const meta = (name: string, size = 10) => ({ objectKey: name, size, sha256: "a".repeat(64),
    contentType: contentType(name) });

  it("requires generated outputs and archives but permits missing optional trajectory files", () => {
    const required = ["raw", "manifest", "report", "analysis", "result", "runtimeBundle", "openclawSessions"];
    expect(() => validateAisResult({ taskId: "SA-1", analysisId: "SA-1", success: true,
      artifacts: Object.fromEntries(required.map(name => [name, meta(name)])) }, config)).not.toThrow();
  });

  it("accepts verified partial evidence on failure and rejects a foreign object key", () => {
    const payload = { taskId: "SA-1", analysisId: "SA-1", success: false,
      artifacts: { trajectory: meta("trajectory", 0) } };
    expect(() => validateAisResult(payload, config, true)).not.toThrow();
    payload.artifacts.trajectory.objectKey = "other-task";
    expect(() => validateAisResult(payload, config, true)).toThrow();
  });
});
