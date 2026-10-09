import { getArtifactBucket } from "../object-storage/oss-object-store.js";

export const EVOLVE_ARTIFACT_URL_TTL_SECONDS = 86_400;

export type UploadArtifactKind =
  | "baseline-pack" | "baseline-manifest"
  | "snapshot-pack" | "snapshot-manifest"
  | "round-diff" | "round-changed-files" | "round-tune-report"
  | "round-spec" | "round-acceptance" | "round-pack" | "round-manifest";

export type ArtifactLocation = {
  objectKey: string;
  ref: string;
  artifactKind: string;
  contentType: string;
};

function safeTaskId(taskId: string): string {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(taskId)) throw new Error("Task ID 不合法");
  return taskId;
}

function safeRound(round: unknown): number {
  const value = Number(round);
  if (!Number.isSafeInteger(value) || value < 1 || value > 100) throw new Error("round 必须是 1 到 100 的整数");
  return value;
}

export function uploadArtifactLocation(taskId: string, kind: unknown, roundInput?: unknown): ArtifactLocation {
  const artifactBucket = getArtifactBucket();
  const task = safeTaskId(taskId);
  const name = String(kind ?? "") as UploadArtifactKind;
  const prefix = `evolution/${task}`;
  let suffix: string;
  let artifactKind: string;
  let contentType: string;
  if (name === "baseline-pack") { suffix = "baseline/artifact_v0.zip"; artifactKind = "baseline_pack"; contentType = "application/zip"; }
  else if (name === "baseline-manifest") { suffix = "baseline/baseline-manifest.json"; artifactKind = "manifest"; contentType = "application/json"; }
  else if (name === "snapshot-pack") { suffix = "snapshots/artifact.zip"; artifactKind = "pack"; contentType = "application/zip"; }
  else if (name === "snapshot-manifest") { suffix = "snapshots/snapshot-manifest.json"; artifactKind = "manifest"; contentType = "application/json"; }
  else {
    const round = safeRound(roundInput);
    const roundPrefix = `rounds/round-${String(round).padStart(3, "0")}`;
    const mapping: Record<string, [string, string, string]> = {
      "round-diff": ["diff.patch", "diff", "text/x-diff; charset=utf-8"],
      "round-changed-files": ["changed-files.json", "changedFiles", "application/json"],
      "round-tune-report": ["tune-report.md", "tuneReport", "text/markdown; charset=utf-8"],
      "round-spec": [`spec-v${round}.md`, "spec", "text/markdown; charset=utf-8"],
      "round-acceptance": ["acceptance-report.json", "acceptance", "application/json"],
      "round-pack": [`artifacts/artifact_v${round}.zip`, "pack", "application/zip"],
      "round-manifest": ["round-manifest.json", "manifest", "application/json"],
    };
    const selected = mapping[name];
    if (!selected) throw new Error("不支持的 Artifact kind");
    [suffix, artifactKind, contentType] = [`${roundPrefix}/${selected[0]}`, selected[1], selected[2]];
  }
  const objectKey = `${prefix}/${suffix}`;
  return { objectKey, ref: `oss://${artifactBucket}/${objectKey}`, artifactKind, contentType };
}

export function restoreManifestLocation(sourceTaskId: string, sourceKind: unknown, sourceRound: unknown): ArtifactLocation {
  const artifactBucket = getArtifactBucket();
  const task = safeTaskId(sourceTaskId);
  const kind = String(sourceKind ?? "");
  let suffix: string;
  if (kind === "baseline") suffix = "baseline/baseline-manifest.json";
  else if (kind === "snapshot") suffix = "snapshots/snapshot-manifest.json";
  else if (kind === "round") suffix = `rounds/round-${String(safeRound(sourceRound)).padStart(3, "0")}/round-manifest.json`;
  else throw new Error("恢复来源类型不合法");
  const objectKey = `evolution/${task}/${suffix}`;
  return { objectKey, ref: `oss://${artifactBucket}/${objectKey}`, artifactKind: "manifest", contentType: "application/json" };
}

export function objectKeyFromFrozenPack(ref: unknown, sourceTaskId: string): string {
  const artifactBucket = getArtifactBucket();
  const uri = String(ref ?? "");
  const prefix = `oss://${artifactBucket}/evolution/${safeTaskId(sourceTaskId)}/`;
  if (!uri.startsWith(prefix) || /\s|[\u0000-\u001f\u007f?#]/.test(uri)) throw new Error("冻结 Pack 引用不合法");
  return uri.slice(`oss://${artifactBucket}/`.length);
}

export function taskLogArchiveLocation(taskId: string, archiveId: string): ArtifactLocation {
  const artifactBucket = getArtifactBucket();
  const task = safeTaskId(taskId);
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(archiveId)) throw new Error("Archive ID 不合法");
  const objectKey = `evolution/${task}/support/log-archives/${archiveId}.tar.gz`;
  return {
    objectKey,
    ref: `oss://${artifactBucket}/${objectKey}`,
    artifactKind: "task_log_archive",
    contentType: "application/gzip",
  };
}
