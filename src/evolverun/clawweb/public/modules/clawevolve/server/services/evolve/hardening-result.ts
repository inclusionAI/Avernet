import { posix } from "node:path";

/** Validate reported paths against the frozen target; business fields stay opaque. */
export function validateHardeningChangedFiles(targetPath: string, files: unknown): void {
  if (files === undefined || files === null) return;
  if (!Array.isArray(files)) throw new Error("Hardening changed_files 必须是数组");
  const target = posix.resolve(targetPath);
  for (const file of files) {
    if (typeof file !== "string" || !file.trim() || file.includes("\\") || file.includes("\0")) {
      throw new Error("Hardening changed_files 路径无效");
    }
    const resolved = posix.resolve(target, file);
    if (!resolved.startsWith(`${target}/`) || file.split("/").includes("..")) {
      throw new Error("Hardening changed_files 超出目标 Skill 文件边界");
    }
  }
}
