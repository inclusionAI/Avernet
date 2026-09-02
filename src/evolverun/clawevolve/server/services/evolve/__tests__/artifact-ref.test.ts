import { describe, expect, it } from "vitest";
import { parseEvolveArtifactRef, validatePackArtifact } from "../artifact-ref.js";

const digest = "a".repeat(64);

describe("evolve artifact refs", () => {
  it("accepts a constrained round diff", () => {
    expect(parseEvolveArtifactRef({ kind: "diff", ref: "oss://clawevolve-artifacts/evolution/EV-1/rounds/round-002/diff.patch", size: 3, sha256: digest, contentType: "text/x-diff; charset=utf-8" }, { taskId: "EV-1", round: 2, kind: "diff" }).objectKey).toContain("round-002/diff.patch");
  });
  it.each([" bad", "\n--workspace /tmp", "?x=1", "#fragment"])("rejects unsafe URI suffix %s", (suffix) => {
    expect(() => parseEvolveArtifactRef({ kind: "diff", ref: `oss://clawevolve-artifacts/evolution/EV-1/rounds/round-002/diff.patch${suffix}`, size: 3, sha256: digest, contentType: "text/x-diff; charset=utf-8" }, { taskId: "EV-1", round: 2, kind: "diff" })).toThrow();
  });
  it("rejects malformed digest and accepts registered pack shapes", () => {
    expect(() => validatePackArtifact({ kind: "pack", ref: "oss://clawevolve-artifacts/evolution/EV-1/snapshots/artifact.zip", size: 1, sha256: "bad", contentType: "application/zip" })).toThrow();
    expect(validatePackArtifact({ kind: "pack", ref: "oss://clawevolve-artifacts/evolution/EV-1/snapshots/artifact.zip", size: 1, sha256: digest, contentType: "application/zip" }).sha256).toBe(digest);
  });
});
