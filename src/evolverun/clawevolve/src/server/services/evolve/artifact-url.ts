import {
  artifactRefForObjectKey,
  objectKeyFromArtifactRef,
  type ArtifactStorageConfig,
} from "./artifact-storage.js";

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
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(taskId) || taskId === "." || taskId === "..") {
    throw new Error("Task ID 不合法");
  }
  return taskId;
}

function safeRound(round: unknown): number {
  const value = Number(round);
  if (!Number.isSafeInteger(value) || value < 1 || value > 100) {
    throw new Error("round 必须是 1 到 100 的整数");
  }
  return value;
}

function location(
  storage: ArtifactStorageConfig,
  objectKey: string,
  artifactKind: string,
  contentType: string,
): ArtifactLocation {
  return {
    objectKey,
    ref: artifactRefForObjectKey(storage, objectKey),
    artifactKind,
    contentType,
  };
}

export function uploadArtifactLocation(
  storage: ArtifactStorageConfig,
  taskId: string,
  kind: unknown,
  roundInput?: unknown,
): ArtifactLocation {
  const task = safeTaskId(taskId);
  const name = String(kind ?? "") as UploadArtifactKind;
  const prefix = `evolution/${task}`;
  let suffix: string;
  let artifactKind: string;
  let contentType: string;
  if (name === "baseline-pack") {
    suffix = "baseline/artifact_v0.zip";
    artifactKind = "baseline_pack";
    contentType = "application/zip";
  } else if (name === "baseline-manifest") {
    suffix = "baseline/baseline-manifest.json";
    artifactKind = "manifest";
    contentType = "application/json";
  } else if (name === "snapshot-pack") {
    suffix = "snapshots/artifact.zip";
    artifactKind = "pack";
    contentType = "application/zip";
  } else if (name === "snapshot-manifest") {
    suffix = "snapshots/snapshot-manifest.json";
    artifactKind = "manifest";
    contentType = "application/json";
  } else {
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
    [suffix, artifactKind, contentType] = [
      `${roundPrefix}/${selected[0]}`,
      selected[1],
      selected[2],
    ];
  }
  return location(storage, `${prefix}/${suffix}`, artifactKind, contentType);
}

export function restoreManifestLocation(
  storage: ArtifactStorageConfig,
  sourceTaskId: string,
  sourceKind: unknown,
  sourceRound: unknown,
): ArtifactLocation {
  const task = safeTaskId(sourceTaskId);
  const kind = String(sourceKind ?? "");
  let suffix: string;
  if (kind === "baseline") suffix = "baseline/baseline-manifest.json";
  else if (kind === "snapshot") suffix = "snapshots/snapshot-manifest.json";
  else if (kind === "round") {
    suffix = `rounds/round-${String(safeRound(sourceRound)).padStart(3, "0")}/round-manifest.json`;
  } else throw new Error("恢复来源类型不合法");
  return location(storage, `evolution/${task}/${suffix}`, "manifest", "application/json");
}

export function objectKeyFromFrozenPack(
  storage: ArtifactStorageConfig,
  ref: unknown,
  sourceTaskId: string,
): string {
  const objectKey = objectKeyFromArtifactRef(storage, ref);
  const prefix = `evolution/${safeTaskId(sourceTaskId)}/`;
  if (!objectKey.startsWith(prefix)) throw new Error("冻结 Pack 引用不合法");
  const relativePath = objectKey.slice(prefix.length);
  if (!/^(?:baseline\/artifact_v0\.zip|snapshots\/artifact\.zip|rounds\/round-\d{3}\/artifacts\/artifact_v\d+\.zip)$/.test(relativePath)) {
    throw new Error("冻结 Pack 路径不合法");
  }
  return objectKey;
}

export function taskLogArchiveLocation(
  storage: ArtifactStorageConfig,
  taskId: string,
  archiveId: string,
): ArtifactLocation {
  const task = safeTaskId(taskId);
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(archiveId)) throw new Error("Archive ID 不合法");
  return location(
    storage,
    `evolution/${task}/support/log-archives/${archiveId}.tar.gz`,
    "task_log_archive",
    "application/gzip",
  );
}
