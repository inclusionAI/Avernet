import { describe, expect, it } from "vitest";
import {
  EVOLVE_ARTIFACT_URL_TTL_SECONDS,
  objectKeyFromFrozenPack,
  restoreManifestLocation,
  uploadArtifactLocation,
} from "../artifact-url.js";

describe("Evolve Artifact signed URL locations", () => {
  it("uses a one-day validity and fixed task-scoped upload paths", () => {
    expect(EVOLVE_ARTIFACT_URL_TTL_SECONDS).toBe(86_400);
    expect(uploadArtifactLocation("EV-1", "snapshot-pack")).toMatchObject({
      objectKey: "evolution/EV-1/snapshots/artifact.zip",
      artifactKind: "pack",
      contentType: "application/zip",
    });
    expect(uploadArtifactLocation("EV-1", "round-diff", 2).objectKey)
      .toBe("evolution/EV-1/rounds/round-002/diff.patch");
  });

  it("resolves only constrained restore locations", () => {
    expect(restoreManifestLocation("EV-1", "round", 2).objectKey)
      .toBe("evolution/EV-1/rounds/round-002/round-manifest.json");
    expect(objectKeyFromFrozenPack(
      "oss://clawevolve-artifacts/evolution/EV-1/snapshots/artifact.zip", "EV-1",
    )).toBe("evolution/EV-1/snapshots/artifact.zip");
    expect(() => objectKeyFromFrozenPack(
      "oss://clawevolve-artifacts/evolution/EV-2/snapshots/artifact.zip", "EV-1",
    )).toThrow("冻结 Pack 引用不合法");
  });

  it("rejects unsupported kinds and invalid rounds", () => {
    expect(() => uploadArtifactLocation("EV-1", "arbitrary", 1)).toThrow("不支持的 Artifact kind");
    expect(() => uploadArtifactLocation("EV-1", "round-pack", 101)).toThrow("round 必须是 1 到 100 的整数");
  });
});
