import { createHash, randomUUID } from "node:crypto";
import JSZip from "jszip";
import { getArtifactBucket, type ObjectStore } from "../object-storage/oss-object-store.js";

export const MATERIAL_LIMITS = {
  maxFileBytes: 20 * 1024 * 1024, maxFiles: 500, maxTotalBytes: 100 * 1024 * 1024,
  maxArchiveBytes: 110 * 1024 * 1024, maxPathBytes: 1024, maxPathComponentBytes: 255, maxDepth: 20,
  uploadLifetimeSeconds: 86400,
} as const;
export const MATERIAL_PROTOCOL = "clawevolve.reference-materials/v1";
export type MaterialFile = { path: string; size: number; sha256: string; content_type: string };
export type MaterialManifest = { protocolVersion: string; files: MaterialFile[] };
export type FrozenMaterials = {
  protocolVersion: string; materialId: string; taskId: string; bucket: string; objectKey: string;
  sha256: string; manifestSha256: string; size: number; fileCount: number; totalBytes: number;
};
type Upload = Omit<FrozenMaterials, "taskId"> & { owner: string; expiresAt: number };
export const digest = (bytes: Buffer | string) => createHash("sha256").update(bytes).digest("hex");
export function materialError(message: string, status = 422) {
  return Object.assign(new Error(message), { status });
}
export function materialPath(value: string): string {
  const parts = value.split("/");
  if (!value || Buffer.byteLength(value, "utf8") > MATERIAL_LIMITS.maxPathBytes
    || parts.length > MATERIAL_LIMITS.maxDepth + 1 || value !== value.normalize("NFC")
    || /[\\\u0000-\u001f\u007f]/.test(value) || /^[A-Za-z]:/.test(value)
    || parts.some(part => !part || part === "." || part === ".." || Buffer.byteLength(part, "utf8") > MATERIAL_LIMITS.maxPathComponentBytes)) {
    throw materialError(`材料路径不合法：${value}`);
  }
  return value;
}

/** The browser sends a STORE ZIP. Check the central directory before JSZip
 * can normalize names or collapse duplicate entries. Compressed input is not
 * needed for this transport and is rejected before any inflation. */
function inspectDirectory(raw: Buffer): string[] {
  if (raw.length < 22 || raw.length > MATERIAL_LIMITS.maxArchiveBytes) throw materialError("材料包大小不合法");
  const end = raw.length - 22;
  if (raw.readUInt32LE(end) !== 0x06054b50 || raw.readUInt16LE(end + 20) !== 0
    || raw.readUInt16LE(end + 4) !== 0 || raw.readUInt16LE(end + 6) !== 0) {
    throw materialError("材料包必须为平台生成的单卷 ZIP");
  }
  const count = raw.readUInt16LE(end + 10), start = raw.readUInt32LE(end + 16);
  if (count < 1 || count > MATERIAL_LIMITS.maxFiles || raw.readUInt16LE(end + 8) !== count
    || start + raw.readUInt32LE(end + 12) !== end) throw materialError("材料文件数量或目录不合法");
  const names: string[] = [];
  let cursor = start, total = 0;
  for (let i = 0; i < count; i++) {
    if (cursor + 46 > end || raw.readUInt32LE(cursor) !== 0x02014b50) throw materialError("材料目录损坏");
    const flags = raw.readUInt16LE(cursor + 8), method = raw.readUInt16LE(cursor + 10);
    const size = raw.readUInt32LE(cursor + 24), packed = raw.readUInt32LE(cursor + 20);
    const nameLength = raw.readUInt16LE(cursor + 28);
    const next = cursor + 46 + nameLength + raw.readUInt16LE(cursor + 30) + raw.readUInt16LE(cursor + 32);
    const mode = (raw.readUInt32LE(cursor + 38) >>> 16) & 0o170000;
    const local = raw.readUInt32LE(cursor + 42);
    if (next > end || flags & ~0x800 || method !== 0 || size !== packed
      || size > MATERIAL_LIMITS.maxFileBytes || (mode !== 0 && mode !== 0o100000)
      || raw.readUInt32LE(cursor + 38) & 0x10 || local + 30 > start
      || raw.readUInt32LE(local) !== 0x04034b50) throw materialError("材料包包含非法文件或不支持的 ZIP 格式");
    const nameBytes = raw.subarray(cursor + 46, cursor + 46 + nameLength);
    let name: string;
    try { name = new TextDecoder("utf-8", { fatal: true }).decode(nameBytes); }
    catch { throw materialError("材料文件名必须为有效 UTF-8"); }
    materialPath(name);
    const localNameSize = raw.readUInt16LE(local + 26);
    const dataStart = local + 30 + localNameSize + raw.readUInt16LE(local + 28);
    if (raw.readUInt16LE(local + 6) !== flags || raw.readUInt16LE(local + 8) !== method
      || raw.readUInt32LE(local + 18) !== packed || raw.readUInt32LE(local + 22) !== size
      || !raw.subarray(local + 30, local + 30 + localNameSize).equals(nameBytes)
      || dataStart + size > start || names.includes(name)
      || names.some(other => other.startsWith(`${name}/`) || name.startsWith(`${other}/`))) {
      throw materialError("材料路径重复、冲突或 ZIP 头不一致");
    }
    total += size;
    if (total > MATERIAL_LIMITS.maxTotalBytes) throw materialError("材料总大小不能超过 100 MiB");
    names.push(name); cursor = next;
  }
  if (cursor !== end) throw materialError("材料目录长度不一致");
  return names.sort();
}

export async function buildMaterials(raw: Buffer) {
  const names = inspectDirectory(raw);
  let input: JSZip;
  try { input = await JSZip.loadAsync(raw, { checkCRC32: true }); }
  catch { throw materialError("材料 ZIP 损坏或文件校验失败"); }
  if (Object.keys(input.files).length !== names.length) throw materialError("材料目录存在重复或非法条目");
  const zip = new JSZip(), files: MaterialFile[] = [];
  for (const path of names) {
    const entry = input.files[path];
    if (!entry || entry.dir || (entry as typeof entry & { unsafeOriginalName?: string }).unsafeOriginalName !== path) {
      throw materialError("材料路径不能被重写");
    }
    const bytes = await entry.async("nodebuffer");
    if (bytes.length > MATERIAL_LIMITS.maxFileBytes) throw materialError("材料单文件过大");
    files.push({ path, size: bytes.length, sha256: digest(bytes), content_type: "application/octet-stream" });
    zip.file(`files/${path}`, bytes, { createFolders: false, unixPermissions: 0o100444 });
  }
  const manifest: MaterialManifest = { protocolVersion: MATERIAL_PROTOCOL, files };
  const index = JSON.stringify(manifest);
  zip.file("index.json", index, { unixPermissions: 0o100444 });
  const bytes = await zip.generateAsync({ type: "nodebuffer", compression: "STORE", platform: "UNIX" });
  if (bytes.length > MATERIAL_LIMITS.maxArchiveBytes) throw materialError("材料包超过上传上限");
  return { bytes, manifest, manifestSha256: digest(index) };
}

export function frozenMaterials(value: unknown, taskId: string): FrozenMaterials {
  const item = value as FrozenMaterials | null;
  if (!item || item.protocolVersion !== MATERIAL_PROTOCOL || item.taskId !== taskId || item.bucket !== getArtifactBucket()
    || !/^[0-9a-f]{64}$/.test(item.sha256) || !/^[0-9a-f]{64}$/.test(item.manifestSha256) || !/^[0-9a-f-]{36}$/.test(item.materialId)
    || item.objectKey !== `evolution/${taskId}/reference-materials/${item.sha256}/package.zip`
    || !Number.isSafeInteger(item.size) || item.size < 1 || item.size > MATERIAL_LIMITS.maxArchiveBytes
    || !Number.isSafeInteger(item.fileCount) || item.fileCount < 1 || item.fileCount > MATERIAL_LIMITS.maxFiles
    || !Number.isSafeInteger(item.totalBytes) || item.totalBytes < 0 || item.totalBytes > MATERIAL_LIMITS.maxTotalBytes) {
    throw materialError("任务参考材料引用无效", 409);
  }
  return item;
}

export async function signedMaterials(value: unknown, taskId: string, signer: Pick<ObjectStore, "createSignedUrl">) {
  const item = frozenMaterials(value, taskId);
  const { objectKey, ...publicFields } = item;
  return { ...publicFields, download: { method: "GET", url: await signer.createSignedUrl(objectKey, "GET", 86400) } };
}

export function referenceMaterialsCommand(task: { config_json: string }, command: string): string {
  return JSON.parse(task.config_json).reference_materials
    ? `${command} --reference-materials-version 1` : command;
}

export class ReferenceMaterials {
  constructor(private readonly store: ObjectStore, private readonly signer: Pick<ObjectStore, "createSignedUrl">) {}
  get available() { return typeof this.store.putObject === "function"; }
  private uploadKey(actor: string, id: string) {
    if (!actor || !/^[0-9a-f-]{36}$/.test(id)) throw materialError("上传引用不合法", 400);
    return `evolution/reference-uploads/${digest(actor)}/${id}`;
  }
  async upload(actor: string, raw: Buffer) {
    if (!this.store.putObject) throw materialError("参考材料存储不可用", 503);
    const { bytes, manifest, manifestSha256 } = await buildMaterials(raw);
    const materialId = randomUUID(), prefix = this.uploadKey(actor, materialId);
    const upload: Upload = {
      protocolVersion: MATERIAL_PROTOCOL, materialId, owner: actor, bucket: getArtifactBucket(),
      objectKey: `${prefix}/package.zip`, size: bytes.length, sha256: digest(bytes), manifestSha256,
      fileCount: manifest.files.length, totalBytes: manifest.files.reduce((sum, file) => sum + file.size, 0),
      expiresAt: Date.now() + MATERIAL_LIMITS.uploadLifetimeSeconds * 1000,
    };
    await this.store.putObject(upload.objectKey, bytes, "application/zip");
    await this.store.putObject(`${prefix}/index.json`, JSON.stringify(manifest), "application/json");
    await this.store.putObject(`${prefix}/upload.json`, JSON.stringify(upload), "application/json");
    return { materialId, expiresAt: upload.expiresAt, files: manifest.files, totalBytes: upload.totalBytes };
  }
  async bind(actor: string, uploadId: string, taskId: string): Promise<FrozenMaterials> {
    if (!this.store.putObject) throw materialError("参考材料存储不可用", 503);
    const prefix = this.uploadKey(actor, uploadId);
    let upload: Upload;
    try { upload = JSON.parse((await this.store.getObject(`${prefix}/upload.json`)).content.toString("utf8")); }
    catch (error) {
      const code = (error as { code?: string }).code;
      if (code === "ENOENT" || code === "NoSuchKey") throw materialError("材料上传引用不存在或已失效，请重新上传", 410);
      throw materialError("读取已上传材料失败，请重试", 503);
    }
    if (upload.owner !== actor || upload.materialId !== uploadId || upload.expiresAt <= Date.now()
      || upload.bucket !== getArtifactBucket() || upload.objectKey !== `${prefix}/package.zip`) throw materialError("材料上传引用已失效", 410);
    const bytes = (await this.store.getObject(upload.objectKey)).content;
    if (bytes.length !== upload.size || digest(bytes) !== upload.sha256) throw materialError("上传材料校验失败", 409);
    const index = (await this.store.getObject(`${prefix}/index.json`)).content;
    if (digest(index) !== upload.manifestSha256) throw materialError("上传材料清单校验失败", 409);
    const { owner: _owner, expiresAt: _expires, ...metadata } = upload;
    const result = frozenMaterials({ ...metadata, taskId,
      objectKey: `evolution/${taskId}/reference-materials/${upload.sha256}/package.zip` }, taskId);
    await this.store.putObject(result.objectKey, bytes, "application/zip");
    await this.store.putObject(result.objectKey.replace(/package\.zip$/, "index.json"), index, "application/json");
    return result;
  }
  async stepInput(value: unknown, taskId: string) { return signedMaterials(value, taskId, this.signer); }
  async view(value: unknown, taskId: string) {
    const item = frozenMaterials(value, taskId);
    // Detail views and re-signing read only the hash-attested index, not
    // the up-to-110 MiB package. Runtime still verifies the complete package.
    const index = (await this.store.getObject(item.objectKey.replace(/package\.zip$/, "index.json"))).content;
    if (digest(index) !== item.manifestSha256) throw materialError("任务材料清单校验失败", 409);
    const manifest = JSON.parse(index.toString("utf8")) as MaterialManifest;
    return { ...await this.stepInput(value, taskId), files: manifest.files };
  }
}
