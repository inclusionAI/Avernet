import { createHash } from "node:crypto";
import JSZip from "jszip";

const MAX_FILES = 1_000;
// A 100 MiB directory can produce a slightly larger ZIP for incompressible files.
const MAX_ARCHIVE_BYTES = 110 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 100 * 1024 * 1024;
const MAX_TEXT_FILE_BYTES = 512 * 1024;

export type SkillPackageFile = {
  path: string;
  size: number;
  text: boolean;
};

type ParsedSkillPackage = {
  files: SkillPackageFile[];
  textByPath: Map<string, string>;
  sha256ByPath: Map<string, string>;
};

function safePath(path: string): boolean {
  return Boolean(path) && !path.startsWith("/") && !path.includes("\\")
    && path.split("/").every((part) => part && part !== "." && part !== "..");
}

function isText(content: Buffer): boolean {
  if (content.byteLength > MAX_TEXT_FILE_BYTES || content.includes(0)) return false;
  const decoded = content.toString("utf8");
  return !decoded.includes("\uFFFD");
}

function declaredUncompressedSize(entry: JSZip.JSZipObject): number | null {
  const value = (entry as unknown as { _data?: { uncompressedSize?: unknown } })._data?.uncompressedSize;
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export async function parseSkillPackage(archive: Buffer): Promise<ParsedSkillPackage> {
  if (archive.byteLength > MAX_ARCHIVE_BYTES) throw new Error("Skill 包压缩文件过大");
  const zip = await JSZip.loadAsync(archive);
  const entries = Object.values(zip.files)
    .filter((entry) => !entry.dir && !entry.name.startsWith("__MACOSX/"));
  if (!entries.length || entries.length > MAX_FILES) throw new Error("Skill 包文件数量不合法");
  if (entries.some((entry) => !safePath(entry.name))) throw new Error("Skill 包包含不安全路径");
  const declaredExpandedBytes = entries.reduce((sum, entry) =>
    sum + (declaredUncompressedSize(entry) ?? 0), 0);
  if (declaredExpandedBytes > MAX_EXPANDED_BYTES) throw new Error("Skill 包解压后过大");
  const files: SkillPackageFile[] = [];
  const textByPath = new Map<string, string>();
  const sha256ByPath = new Map<string, string>();
  let expandedBytes = 0;
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const content = await entry.async("nodebuffer");
    expandedBytes += content.byteLength;
    if (expandedBytes > MAX_EXPANDED_BYTES) throw new Error("Skill 包解压后过大");
    const text = isText(content);
    files.push({ path: entry.name, size: content.byteLength, text });
    sha256ByPath.set(entry.name, createHash("sha256").update(content).digest("hex"));
    if (text) textByPath.set(entry.name, content.toString("utf8"));
  }
  return { files, textByPath, sha256ByPath };
}

export async function skillPackagesEquivalent(leftArchive: Buffer, rightArchive: Buffer): Promise<boolean> {
  const [left, right] = await Promise.all([
    parseSkillPackage(leftArchive),
    parseSkillPackage(rightArchive),
  ]);
  if (left.files.length !== right.files.length) return false;
  return left.files.every((file, index) => {
    const other = right.files[index];
    return other?.path === file.path && other.size === file.size
      && right.sha256ByPath.get(file.path) === left.sha256ByPath.get(file.path);
  });
}

export async function editSkillPackage(
  archive: Buffer,
  edits: Array<{ path: string; content: string }>,
): Promise<Buffer> {
  const parsed = await parseSkillPackage(archive);
  if (!edits.length) throw new Error("请至少修改一个文件");
  const seen = new Set<string>();
  const zip = await JSZip.loadAsync(archive);
  for (const edit of edits) {
    const path = edit.path.trim();
    if (!safePath(path) || seen.has(path)) throw new Error("编辑文件路径不合法或重复");
    if (!parsed.textByPath.has(path)) throw new Error(`文件不存在或不是可编辑的文本文件：${path}`);
    if (Buffer.byteLength(edit.content, "utf8") > MAX_TEXT_FILE_BYTES) throw new Error(`文本文件过大：${path}`);
    seen.add(path);
    zip.file(path, edit.content);
  }
  const result = await zip.generateAsync({ type: "nodebuffer" });
  await parseSkillPackage(result);
  return result;
}

export async function skillPackageView(archive: Buffer, requestedPath?: string): Promise<{
  files: SkillPackageFile[];
  selected: { path: string; content: string } | null;
}> {
  const parsed = await parseSkillPackage(archive);
  const selectedPath = requestedPath?.trim()
    || (parsed.textByPath.has("SKILL.md")
      ? "SKILL.md"
      : parsed.textByPath.has("implementation/SKILL.md")
        ? "implementation/SKILL.md"
        : parsed.textByPath.keys().next().value);
  if (!selectedPath) return { files: parsed.files, selected: null };
  if (!safePath(selectedPath)) throw new Error("文件路径不合法");
  const content = parsed.textByPath.get(selectedPath);
  if (content == null) throw new Error("文件不存在或不是可预览的文本文件");
  return { files: parsed.files, selected: { path: selectedPath, content } };
}

export async function skillPackageDiff(beforeArchive: Buffer, afterArchive: Buffer): Promise<{
  files: Array<{
    path: string;
    change: "added" | "modified" | "deleted";
    before: string | null;
    after: string | null;
  }>;
}> {
  const [before, after] = await Promise.all([
    parseSkillPackage(beforeArchive),
    parseSkillPackage(afterArchive),
  ]);
  const paths = [...new Set([...before.textByPath.keys(), ...after.textByPath.keys()])].sort();
  return {
    files: paths.flatMap((path) => {
      const oldText = before.textByPath.get(path);
      const newText = after.textByPath.get(path);
      if (oldText === newText) return [];
      return [{
        path,
        change: oldText == null ? "added" as const : newText == null ? "deleted" as const : "modified" as const,
        before: oldText ?? null,
        after: newText ?? null,
      }];
    }),
  };
}

/** Stable identity across ZIP metadata/repacking, using exactly the compared file set. */
export async function skillPackageContentDigest(content: Buffer): Promise<string> {
  const parsed = await parseSkillPackage(content);
  return createHash("sha256").update(JSON.stringify([...parsed.sha256ByPath].sort(([a], [b]) => a.localeCompare(b)))).digest("hex");
}
