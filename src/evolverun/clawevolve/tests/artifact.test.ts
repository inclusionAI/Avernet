import assert from "node:assert/strict";
import test from "node:test";

import {
  parseEvolveArtifactRef,
  validatePackArtifact,
} from "../src/server/services/evolve/artifact-ref.js";
import {
  objectKeyFromFrozenPack,
  restoreManifestLocation,
  taskLogArchiveLocation,
  uploadArtifactLocation,
} from "../src/server/services/evolve/artifact-url.js";

const storage = { bucket: "public-artifacts" };
const digest = "a".repeat(64);

test("builds artifact locations from injected storage configuration", () => {
  assert.deepEqual(uploadArtifactLocation(storage, "EV-1", "snapshot-pack"), {
    objectKey: "evolution/EV-1/snapshots/artifact.zip",
    ref: "oss://public-artifacts/evolution/EV-1/snapshots/artifact.zip",
    artifactKind: "pack",
    contentType: "application/zip",
  });
  assert.equal(
    uploadArtifactLocation(storage, "EV-1", "round-diff", 2).objectKey,
    "evolution/EV-1/rounds/round-002/diff.patch",
  );
  assert.equal(
    restoreManifestLocation(storage, "EV-1", "round", 2).objectKey,
    "evolution/EV-1/rounds/round-002/round-manifest.json",
  );
  assert.equal(
    taskLogArchiveLocation(storage, "EV-1", "archive-1").contentType,
    "application/gzip",
  );
});

test("validates artifact references against the configured bucket and path", () => {
  const artifact = {
    kind: "diff",
    ref: "oss://public-artifacts/evolution/EV-1/rounds/round-002/diff.patch",
    size: 3,
    sha256: digest,
    contentType: "text/x-diff; charset=utf-8",
  };
  assert.equal(
    parseEvolveArtifactRef(artifact, { taskId: "EV-1", round: 2, kind: "diff" }, storage).objectKey,
    "evolution/EV-1/rounds/round-002/diff.patch",
  );
  assert.throws(
    () => parseEvolveArtifactRef(
      { ...artifact, ref: artifact.ref.replace("public-artifacts", "other-artifacts") },
      { taskId: "EV-1", round: 2, kind: "diff" },
      storage,
    ),
    /不属于配置的存储位置/,
  );
});

test("validates registered packs without a built-in bucket", () => {
  const pack = {
    kind: "pack",
    ref: "oss://public-artifacts/evolution/EV-1/snapshots/artifact.zip",
    size: 1,
    sha256: digest,
    contentType: "application/zip",
  };
  assert.equal(validatePackArtifact(pack, storage).sha256, digest);
  assert.equal(
    objectKeyFromFrozenPack(storage, pack.ref, "EV-1"),
    "evolution/EV-1/snapshots/artifact.zip",
  );
  assert.throws(() => validatePackArtifact({ ...pack, sha256: "bad" }, storage), /sha256/);
});

test("rejects unsupported artifact inputs", () => {
  assert.throws(
    () => uploadArtifactLocation(storage, "EV-1", "arbitrary", 1),
    /不支持的 Artifact kind/,
  );
  assert.throws(
    () => uploadArtifactLocation(storage, "EV-1", "round-pack", 101),
    /round 必须是 1 到 100 的整数/,
  );
  assert.throws(
    () => uploadArtifactLocation({ bucket: "" }, "EV-1", "snapshot-pack"),
    /bucket 不合法/,
  );
  assert.throws(
    () => uploadArtifactLocation(storage, "..", "snapshot-pack"),
    /Task ID 不合法/,
  );
  assert.throws(
    () => objectKeyFromFrozenPack(
      storage,
      "oss://public-artifacts/evolution/EV-1/../private-object",
      "EV-1",
    ),
    /冻结 Pack 路径不合法/,
  );
  assert.throws(
    () => uploadArtifactLocation(
      { bucket: "public-artifacts", scheme: "https" as "oss" },
      "EV-1",
      "snapshot-pack",
    ),
    /scheme 不合法/,
  );
  assert.throws(
    () => parseEvolveArtifactRef(
      {
        kind: "diff",
        ref: "oss://public-artifacts/evolution/../rounds/round-002/diff.patch",
        size: 3,
        sha256: digest,
        contentType: "text/x-diff; charset=utf-8",
      },
      { taskId: "..", round: 2, kind: "diff" },
      storage,
    ),
    /Task ID 不合法/,
  );
});
