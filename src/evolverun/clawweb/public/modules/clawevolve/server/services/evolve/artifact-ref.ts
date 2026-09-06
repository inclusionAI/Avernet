import { getArtifactBucket } from "../object-storage/oss-object-store.js";

export type EvolveArtifactRef = {
  kind?: string;
  ref?: string;
  size?: number;
  sha256?: string;
  contentType?: string;
};

export function parseEvolveArtifactRef(
  value: unknown,
  expected: { taskId: string; round: number; kind: "diff" | "pack" | "manifest" },
): { objectKey: string; artifact: EvolveArtifactRef } {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("产物引用必须是对象");
  const artifact = value as EvolveArtifactRef;
  const uri = String(artifact.ref ?? "");
  if (/\s|[\u0000-\u001f\u007f?#]/.test(uri)) throw new Error("产物 OSS URI 含非法字符");
  if (expected.kind !== "manifest" && (!Number.isSafeInteger(artifact.size) || Number(artifact.size) < 0)) throw new Error("产物 size 必须是非负整数");
  if (expected.kind !== "manifest" && !/^[0-9a-f]{64}$/.test(String(artifact.sha256 ?? ""))) throw new Error("产物 sha256 必须是 64 位小写十六进制");
  const match = /^oss:\/\/([^/]+)\/(.+)$/.exec(uri);
  if (!match || match[1] !== getArtifactBucket()) throw new Error("产物必须位于固定 OSS Bucket");
  const prefix = `evolution/${expected.taskId}/rounds/round-${String(expected.round).padStart(3, "0")}/`;
  const suffix = expected.kind === "diff" ? "diff.patch"
    : expected.kind === "manifest" ? "round-manifest.json"
      : `artifacts/artifact_v${expected.round}.zip`;
  if (match[2] !== prefix + suffix) throw new Error(`产物 OSS 路径与当前 Task/Round 不一致`);
  if (expected.kind === "diff" && artifact.contentType !== "text/x-diff; charset=utf-8") throw new Error("Diff Content-Type 不合法");
  if (expected.kind === "pack" && artifact.contentType !== "application/zip") throw new Error("Pack Content-Type 不合法");
  if (expected.kind === "manifest" && artifact.contentType != null && artifact.contentType !== "application/json") throw new Error("Manifest Content-Type 不合法");
  if (expected.kind !== "manifest" && artifact.kind !== expected.kind) throw new Error("产物 kind 不合法");
  return { objectKey: match[2], artifact };
}

export function validatePackArtifact(value: unknown): EvolveArtifactRef {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Pack 产物必须是对象");
  const artifact = value as EvolveArtifactRef;
  const uri = String(artifact.ref ?? "");
  if (/\s|[\u0000-\u001f\u007f?#]/.test(uri)) throw new Error("Pack OSS 路径不合法");
  const match = /^oss:\/\/([^/]+)\/(evolution\/[A-Za-z0-9._:-]+\/(?:baseline\/artifact_v0\.zip|snapshots\/artifact\.zip|rounds\/round-\d{3}\/artifacts\/artifact_v\d+\.zip))$/.exec(uri);
  if (!match || match[1] !== getArtifactBucket()) throw new Error("Pack OSS 路径不合法");
  if (artifact.kind !== "pack" && artifact.kind !== "baseline_pack") throw new Error("Pack kind 不合法");
  if (artifact.contentType !== "application/zip") throw new Error("Pack Content-Type 不合法");
  if (!Number.isSafeInteger(artifact.size) || Number(artifact.size) < 0) throw new Error("Pack size 不合法");
  if (!/^[0-9a-f]{64}$/.test(String(artifact.sha256 ?? ""))) throw new Error("Pack sha256 不合法");
  return artifact;
}
