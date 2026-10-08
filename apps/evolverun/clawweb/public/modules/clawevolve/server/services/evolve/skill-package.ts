import { readBoundedBody as readResponseBody } from "@avernet/clawweb-shared/server/services/http-response";
import { createHash } from "node:crypto";
import { crc32 } from "node:zlib";
import { createRequire } from "node:module";

// The same classic-zlib implementation already used by JSZip. Node builds
// using zlib-ng can encode identical contents differently from Python zipfile.
const { deflateRaw } = createRequire(import.meta.url)("pako") as {
  deflateRaw(bytes: Buffer, options: { level: number }): Uint8Array;
};

export const MAX_SKILL_DIRECTORY_BYTES = 100 * 1024 * 1024;
const MAX_FILE = MAX_SKILL_DIRECTORY_BYTES;
const MAX_EXPANDED = MAX_SKILL_DIRECTORY_BYTES;

// Preserve the package reader's existing default while sharing bounded HTTP reads.
export function readBoundedBody(response: Response, limit = MAX_FILE): Promise<Buffer> {
  return readResponseBody(response, limit);
}

export function packagePath(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 256 || value.includes("\\")
      || value.includes("\0") || value.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error("Skill 包包含不安全路径");
  }
  return value;
}

function ignored(path: string): boolean {
  const parts = path.split("/");
  return parts.at(-1) === ".DS_Store" || parts[0] === "__MACOSX" || parts.includes(".git")
    || parts.includes("__pycache__") || /\.py[co]$/.test(path);
}

/** Matches the existing canonical package ZIP format, including Python zipfile flags.
 * Kept deterministic so old frozen package digests remain comparable.
 */
export function encodeSkillPackage(files: Array<{ path: string; bytes: Buffer }>) {
  const sorted = files.map((file) => ({ ...file, path: packagePath(file.path) }))
    .filter((file) => !ignored(file.path))
    .sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
  if (!sorted.length || sorted.length > 500 || new Set(sorted.map((file) => file.path)).size !== sorted.length) {
    throw new Error("Skill 包文件数量或重复路径不合法");
  }
  const local: Buffer[] = [], central: Buffer[] = [];
  let offset = 0, expanded = 0;
  for (const file of sorted) {
    expanded += file.bytes.length;
    if (file.bytes.length > MAX_FILE || expanded > MAX_EXPANDED) throw new Error("Skill 包文件过大");
    const name = Buffer.from(file.path), compressed = Buffer.from(deflateRaw(file.bytes, { level: 9 }));
    const checksum = crc32(file.bytes), flags = /[^\x00-\x7f]/.test(file.path) ? 0x800 : 0;
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0); header.writeUInt16LE(20, 4);
    header.writeUInt16LE(flags, 6); header.writeUInt16LE(8, 8); header.writeUInt16LE(33, 12);
    header.writeUInt32LE(checksum, 14); header.writeUInt32LE(compressed.length, 18);
    header.writeUInt32LE(file.bytes.length, 22); header.writeUInt16LE(name.length, 26);
    const directory = Buffer.alloc(46);
    directory.writeUInt32LE(0x02014b50, 0); directory.writeUInt16LE(0x314, 4);
    directory.writeUInt16LE(20, 6); directory.writeUInt16LE(flags, 8);
    directory.writeUInt16LE(8, 10); directory.writeUInt16LE(33, 14);
    directory.writeUInt32LE(checksum, 16); directory.writeUInt32LE(compressed.length, 20);
    directory.writeUInt32LE(file.bytes.length, 24); directory.writeUInt16LE(name.length, 28);
    directory.writeUInt32LE(0o100644 * 65536, 38); directory.writeUInt32LE(offset, 42);
    local.push(header, name, compressed); central.push(directory, name);
    offset += header.length + name.length + compressed.length;
  }
  const centralSize = central.reduce((size, bytes) => size + bytes.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(sorted.length, 8);
  end.writeUInt16LE(sorted.length, 10); end.writeUInt32LE(centralSize, 12); end.writeUInt32LE(offset, 16);
  const packageBytes = Buffer.concat([...local, ...central, end]);
  if (packageBytes.length > 110 * 1024 * 1024) throw new Error("Skill 压缩包过大");
  return { packageBytes, sha256: `sha256:${createHash("sha256").update(packageBytes).digest("hex")}` };
}

function sizeUnavailable(reason: string) {
  return Object.assign(new Error(`无法预检查 Skill 大小：${reason}`), {
    statusCode: 502, code: "HOST_LOCAL_SKILL_SIZE_UNAVAILABLE",
  });
}

export async function readSkillPackage(
  directory: string,
  post: (path: string, body: Record<string, unknown>) => Promise<Response>,
) {
  // Enumerate each directory explicitly: the existing recursive OpenClaw
  // implementation uses os.walk, whose default error handling can omit an
  // unreadable subtree. Shallow list errors remain visible to this caller.
  const directories = [""];
  const entries: Array<{ path: string; size: number }> = [];
  let declaredBytes = 0;
  const seen = new Set<string>();
  for (let cursor = 0; cursor < directories.length; cursor += 1) {
    const parent = directories[cursor];
    let listing: { data?: { files?: Array<{ is_dir?: boolean; relative_path?: unknown; size?: unknown }> } };
    try {
      listing = JSON.parse((await readBoundedBody(await post("/api/file/list", {
        dir_path: parent ? `${directory}/${parent}` : directory, recursive: false,
      }), 4 * 1024 * 1024)).toString("utf8"));
    } catch { throw sizeUnavailable("目录列表读取失败"); }
    if (!Array.isArray(listing.data?.files)) throw sizeUnavailable("目录列表响应不完整");
    for (const entry of listing.data.files) {
      if (typeof entry.is_dir !== "boolean") throw sizeUnavailable("目录类型缺失");
      const child = packagePath(entry.relative_path);
      if (child.includes("/")) throw sizeUnavailable("单层目录返回了不合法路径");
      const path = packagePath(parent ? `${parent}/${child}` : child);
      if (seen.has(path)) throw sizeUnavailable("目录返回了重复路径");
      seen.add(path);
      if (seen.size > 10_000) throw sizeUnavailable("目录条目过多");
      if (entry.is_dir) { directories.push(path); continue; }
      if (typeof entry.size !== "number" || !Number.isSafeInteger(entry.size) || entry.size < 0) {
        throw sizeUnavailable("文件大小缺失或不合法");
      }
      declaredBytes += entry.size;
      if (declaredBytes > MAX_SKILL_DIRECTORY_BYTES) {
        throw Object.assign(new Error("Skill 太大：目录文件总大小超过 100 MiB，无法注册"), {
          statusCode: 413, code: "HOST_LOCAL_SKILL_TOO_LARGE",
        });
      }
      entries.push({ path, size: entry.size });
    }
  }
  const paths = entries.map((entry) => entry.path).filter((path) => !ignored(path));
  if (!paths.length || paths.length > 500) throw new Error("Skill 目录文件数量不合法");
  const files: Array<{ path: string; bytes: Buffer }> = [];
  let total = 0;
  for (const path of paths) {
    const bytes = await readBoundedBody(await post("/api/file/read", { file_path: `${directory}/${path}` }), MAX_FILE);
    total += bytes.length;
    if (total > MAX_EXPANDED) throw new Error("Skill 包文件过大");
    files.push({ path, bytes });
  }
  return encodeSkillPackage(files);
}
