import { describe, expect, it } from "vitest";
import { runtimeArtifactDownloadFilename, validateAisResult } from "../session-analysis.js";

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

  it("requires generated outputs and archives and accepts the main Session as evidence", () => {
    const required = ["raw", "manifest", "report", "analysis", "result", "runtimeBundle", "openclawSessions"];
    expect(() => validateAisResult({ taskId: "SA-1", analysisId: "SA-1", success: true,
      artifacts: Object.fromEntries(required.map(name => [name, meta(name)])) }, config)).not.toThrow();
  });

  it("accepts trajectory as the only source evidence and rejects a result with neither source", () => {
    const generated = ["manifest", "report", "analysis", "result", "runtimeBundle", "openclawSessions"];
    const trajectoryOnly = [...generated, "trajectory"];
    expect(() => validateAisResult({ taskId: "SA-1", analysisId: "SA-1", success: true,
      artifacts: Object.fromEntries(trajectoryOnly.map(name => [name, meta(name)])) }, config)).not.toThrow();
    expect(() => validateAisResult({ taskId: "SA-1", analysisId: "SA-1", success: true,
      artifacts: Object.fromEntries(generated.map(name => [name, meta(name)])) }, config)).toThrow(
      "AIS 结果缺少 Session 或 trajectory 证据",
    );
  });

  it("accepts verified partial evidence on failure and rejects a foreign object key", () => {
    const payload = { taskId: "SA-1", analysisId: "SA-1", success: false,
      artifacts: { trajectory: meta("trajectory", 0) } };
    expect(() => validateAisResult(payload, config, true)).not.toThrow();
    payload.artifacts.trajectory.objectKey = "other-task";
    expect(() => validateAisResult(payload, config, true)).toThrow();
  });

  it("uses task-scoped archive download names without changing artifact identifiers", () => {
    expect(runtimeArtifactDownloadFilename("runtimeBundle", "SA-1-AIS-2"))
      .toBe("SA-1-AIS-2-clawevolve-results.tar.gz");
    expect(runtimeArtifactDownloadFilename("openclawSessions", "SA-1-AIS-2"))
      .toBe("SA-1-AIS-2-openclaw-sessions.tar.gz");
    expect(runtimeArtifactDownloadFilename("raw", "SA-1-AIS-2")).toBeNull();
  });
});
