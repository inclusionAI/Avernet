import {
  objectKeyFromArtifactRef,
  type ArtifactStorageConfig,
} from "./artifact-storage.js";

export type EvolveArtifactRef = {
  kind?: string;
  ref?: string;
  size?: number;
  sha256?: string;
  contentType?: string;
};

function expectedPath(
  expected: { taskId: string; round: number; kind: "diff" | "pack" | "manifest" },
): string {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(expected.taskId)
    || expected.taskId === "." || expected.taskId === "..") {
    throw new Error("Task ID 不合法");
  }
  if (!Number.isSafeInteger(expected.round) || expected.round < 1 || expected.round > 100) {
    throw new Error("round 必须是 1 到 100 的整数");
  }
  const prefix = `evolution/${expected.taskId}/rounds/round-${String(expected.round).padStart(3, "0")}/`;
  const suffix = expected.kind === "diff" ? "diff.patch"
    : expected.kind === "manifest" ? "round-manifest.json"
      : `artifacts/artifact_v${expected.round}.zip`;
  return prefix + suffix;
}

export function parseEvolveArtifactRef(
  value: unknown,
  expected: { taskId: string; round: number; kind: "diff" | "pack" | "manifest" },
  storage: ArtifactStorageConfig,
): { objectKey: string; artifact: EvolveArtifactRef } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("产物引用必须是对象");
  }
  const artifact = value as EvolveArtifactRef;
  if (expected.kind !== "manifest" && (!Number.isSafeInteger(artifact.size) || Number(artifact.size) < 0)) {
    throw new Error("产物 size 必须是非负整数");
  }
  if (expected.kind !== "manifest" && !/^[0-9a-f]{64}$/.test(String(artifact.sha256 ?? ""))) {
    throw new Error("产物 sha256 必须是 64 位小写十六进制");
  }

  const objectKey = objectKeyFromArtifactRef(storage, artifact.ref);
  if (objectKey !== expectedPath(expected)) {
    throw new Error("产物路径与当前 Task/Round 不一致");
  }
  if (expected.kind === "diff" && artifact.contentType !== "text/x-diff; charset=utf-8") {
    throw new Error("Diff Content-Type 不合法");
  }
  if (expected.kind === "pack" && artifact.contentType !== "application/zip") {
    throw new Error("Pack Content-Type 不合法");
  }
  if (expected.kind === "manifest" && artifact.contentType != null && artifact.contentType !== "application/json") {
    throw new Error("Manifest Content-Type 不合法");
  }
  if (expected.kind !== "manifest" && artifact.kind !== expected.kind) {
    throw new Error("产物 kind 不合法");
  }
  return { objectKey, artifact };
}

export function validatePackArtifact(
  value: unknown,
  storage: ArtifactStorageConfig,
): EvolveArtifactRef {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Pack 产物必须是对象");
  }
  const artifact = value as EvolveArtifactRef;
  const objectKey = objectKeyFromArtifactRef(storage, artifact.ref);
  if (!/^evolution\/[A-Za-z0-9._:-]+\/(?:baseline\/artifact_v0\.zip|snapshots\/artifact\.zip|rounds\/round-\d{3}\/artifacts\/artifact_v\d+\.zip)$/.test(objectKey)) {
    throw new Error("Pack 路径不合法");
  }
  if (artifact.kind !== "pack" && artifact.kind !== "baseline_pack") {
    throw new Error("Pack kind 不合法");
  }
  if (artifact.contentType !== "application/zip") {
    throw new Error("Pack Content-Type 不合法");
  }
  if (!Number.isSafeInteger(artifact.size) || Number(artifact.size) < 0) {
    throw new Error("Pack size 不合法");
  }
  if (!/^[0-9a-f]{64}$/.test(String(artifact.sha256 ?? ""))) {
    throw new Error("Pack sha256 不合法");
  }
  return artifact;
}
