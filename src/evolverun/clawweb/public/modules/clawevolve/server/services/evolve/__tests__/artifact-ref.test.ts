import { afterEach, describe, expect, it } from "vitest";
import { parseEvolveArtifactRef, validatePackArtifact } from "../artifact-ref.js";
import { configureArtifactBucket } from "../../object-storage/oss-object-store.js";

const digest = "a".repeat(64);

describe("evolve artifact refs", () => {
  afterEach(() => configureArtifactBucket(undefined));

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

  it("uses the exact artifact bucket configured by the host", () => {
    configureArtifactBucket("internal-artifacts");
    const artifact = {
      kind: "pack",
      ref: "oss://internal-artifacts/evolution/EV-1/snapshots/artifact.zip",
      size: 1,
      sha256: digest,
      contentType: "application/zip",
    };
    expect(validatePackArtifact(artifact).ref).toBe(artifact.ref);
    expect(() => validatePackArtifact({
      ...artifact,
      ref: "oss://clawevolve-artifacts/evolution/EV-1/snapshots/artifact.zip",
    })).toThrow("Pack OSS 路径不合法");
  });
});
