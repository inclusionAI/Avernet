import { createHash } from "node:crypto";
import type { FlowInput, WorkflowInputSpec } from "../types.js";
import { materializeInputFiles } from "./files.js";

function sortRecord<T>(record: Record<string, T>): Record<string, T> {
  return Object.fromEntries(Object.entries(record).sort(([left], [right]) => left.localeCompare(right)));
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value != null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, nested]) => nested !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, nested]) => [key, canonicalize(nested)]),
    );
  }
  return value;
}

function sha256CanonicalJson(value: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(canonicalize(value)))
    .digest("hex");
}

export async function normalizeFlowInput(options: {
  workflowId: string;
  workflowDigest: string;
  packDigest?: string;
  inputSpec?: WorkflowInputSpec;
  params: Record<string, string>;
  message?: string;
  files?: string[];
  artifactDir: string;
}): Promise<FlowInput> {
  const files = await materializeInputFiles({
    files: options.files ?? [],
    artifactDir: options.artifactDir,
    allowedExtensions: options.inputSpec?.sources?.files?.allowedExtensions,
    maxCount: options.inputSpec?.sources?.files?.maxCount,
    maxSizeMb: options.inputSpec?.sources?.files?.maxSizeMb,
  });

  const params = sortRecord(options.params ?? {});
  const digest = sha256CanonicalJson({
    workflowId: options.workflowId,
    workflowDigest: options.workflowDigest,
    packDigest: options.packDigest,
    params,
    message: options.message,
    files: files.map((file) => file.digest).sort(),
  });

  return {
    params,
    message: options.message,
    files,
    digest,
    digestShort: digest.slice(0, 12),
  };
}
