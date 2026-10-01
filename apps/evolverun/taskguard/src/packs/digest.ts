import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

function sha256(): ReturnType<typeof createHash> {
  return createHash("sha256");
}

function formatDigest(hex: string): string {
  return `sha256:${hex}`;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stableValue);
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .filter((key) => record[key] !== undefined)
        .sort()
        .map((key) => [key, stableValue(record[key])]),
    );
  }
  return value;
}

export function stableStringify(value: unknown): string {
  return `${JSON.stringify(stableValue(value))}\n`;
}

export function digestString(content: string): string {
  return formatDigest(sha256().update(content, "utf-8").digest("hex"));
}

export function digestBuffer(content: Buffer): string {
  return formatDigest(sha256().update(content).digest("hex"));
}

export function digestManifest(manifest: unknown): string {
  return digestString(stableStringify(manifest));
}

export function digestWorkflowSpec(workflowSpec: unknown): string {
  return digestString(stableStringify(workflowSpec));
}

export function digestFile(filepath: string): string {
  return digestBuffer(readFileSync(filepath));
}

function listFiles(root: string, current = root): string[] {
  const entries = readdirSync(current, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name));
  const files: string[] = [];

  for (const entry of entries) {
    if (entry.name === ".git") continue; // 跳过 .git:防不可读文件抛错致 pack 被丢弃 + digest 稳定
    const absolutePath = join(current, entry.name);
    if (entry.isDirectory()) {
      files.push(...listFiles(root, absolutePath));
    } else if (entry.isFile()) {
      files.push(absolutePath);
    }
  }

  return files;
}

export function digestPackDirectory(packRoot: string): string {
  const hash = sha256();
  for (const filepath of listFiles(packRoot)) {
    const relativePath = relative(packRoot, filepath).split(sep).join("/");
    hash.update(relativePath, "utf-8");
    hash.update("\0");
    hash.update(readFileSync(filepath));
    hash.update("\0");
  }
  return formatDigest(hash.digest("hex"));
}
