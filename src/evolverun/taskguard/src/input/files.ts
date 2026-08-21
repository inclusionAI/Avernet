import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { copyFile, mkdir, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, extname, join, resolve } from "node:path";
import type { FlowInputFile } from "../types.js";

function expandHome(filePath: string): string {
  if (filePath === "~") return homedir();
  if (filePath.startsWith("~/")) return join(homedir(), filePath.slice(2));
  return filePath;
}

function normalizeExtension(extension: string): string {
  const trimmed = extension.trim().toLowerCase();
  if (!trimmed) return trimmed;
  return trimmed.startsWith(".") ? trimmed : `.${trimmed}`;
}

async function sha256File(filePath: string): Promise<string> {
  const hash = createHash("sha256");
  await new Promise<void>((resolvePromise, reject) => {
    const stream = createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolvePromise);
  });
  return hash.digest("hex");
}

export async function materializeInputFiles(options: {
  files: string[];
  artifactDir: string;
  allowedExtensions?: string[];
  maxCount?: number;
  maxSizeMb?: number;
}): Promise<FlowInputFile[]> {
  const files = options.files ?? [];
  if (options.maxCount != null && files.length > options.maxCount) {
    throw new Error(`Too many input files: received ${files.length}, max ${options.maxCount}`);
  }
  if (files.length === 0) {
    return [];
  }

  const allowedExtensions = options.allowedExtensions?.map(normalizeExtension);
  const inputDir = join(options.artifactDir, "input");
  await mkdir(inputDir, { recursive: true });

  const results: FlowInputFile[] = [];
  for (const rawPath of files) {
    const originalPath = resolve(expandHome(rawPath));
    let fileStat;
    try {
      fileStat = await stat(originalPath);
    } catch (error) {
      throw new Error(`Input file does not exist: ${rawPath}`, { cause: error });
    }

    if (fileStat.isDirectory()) {
      throw new Error(`Input file must not be a directory: ${rawPath}`);
    }
    if (!fileStat.isFile()) {
      throw new Error(`Input path must be a regular file: ${rawPath}`);
    }
    if (options.maxSizeMb != null) {
      const maxBytes = options.maxSizeMb * 1024 * 1024;
      if (fileStat.size > maxBytes) {
        throw new Error(`Input file exceeds maxSizeMb ${options.maxSizeMb}: ${rawPath}`);
      }
    }

    const fileExtension = extname(originalPath).toLowerCase();
    if (allowedExtensions?.length && !allowedExtensions.includes(fileExtension)) {
      throw new Error(`Input file extension is not allowed: ${fileExtension || "(none)"}`);
    }

    const digest = await sha256File(originalPath);
    const name = basename(originalPath);
    const artifactPath = join(inputDir, `${digest}-${name}`);
    await copyFile(originalPath, artifactPath);

    results.push({
      name,
      originalPath,
      artifactPath,
      size: fileStat.size,
      digest,
    });
  }

  return results;
}
