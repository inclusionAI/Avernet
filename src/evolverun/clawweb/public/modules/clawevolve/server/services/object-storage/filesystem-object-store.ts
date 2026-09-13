import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, writeFile, rename, rm, stat, realpath } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
import { createReadStream, createWriteStream } from "node:fs";
import { Transform, type Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { ObjectStore, StoredObject } from "./oss-object-store.js";

const MAX_OBJECT_BYTES = 10 * 1024 * 1024;

function safeObjectKey(value: string): string {
  const key = value.replace(/^\/+/, "");
  if (!key || key.length > 1024 || key.split("/").some((part) => !part || part === "." || part === "..")
      || /[\\\u0000-\u001f\u007f?#]/.test(key)) {
    throw new Error("Invalid Singlebox artifact key");
  }
  return key;
}

export class FilesystemObjectStore implements ObjectStore {
  private readonly root: string;
  private readonly signingKey = randomBytes(32);

  constructor(root: string, private readonly publicBaseUrl = "", private readonly maxObjectBytes = MAX_OBJECT_BYTES) {
    if (!Number.isSafeInteger(maxObjectBytes) || maxObjectBytes < 1) throw new Error("Invalid artifact size limit");
    this.root = resolve(root);
  }

  private pathFor(objectKey: string): string {
    const target = resolve(this.root, safeObjectKey(objectKey));
    // 以下为安全注释COSEC：所有 Artifact 路径必须保持在 Singlebox 数据根目录内。
    if (!target.startsWith(`${this.root}${sep}`)) throw new Error("Artifact path escaped its root");
    return target;
  }

  async getObject(objectKey: string): Promise<StoredObject> {
    const content = await readFile(this.pathFor(objectKey));
    if (content.byteLength > this.maxObjectBytes) throw new Error("Artifact exceeds the configured size limit");
    return {
      content,
      etag: createHash("sha256").update(content).digest("hex"),
      contentType: null,
    };
  }

  async putObject(
    objectKey: string,
    content: Buffer | Uint8Array | string,
    _contentType: string,
  ): Promise<{ etag: string }> {
    const payload = typeof content === "string" ? Buffer.from(content) : Buffer.from(content);
    if (payload.byteLength > this.maxObjectBytes) throw new Error("Artifact exceeds the configured size limit");
    const target = this.pathFor(objectKey);
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    await writeFile(target, payload, { mode: 0o600 });
    return { etag: createHash("sha256").update(payload).digest("hex") };
  }

  async putStream(objectKey: string, source: Readable): Promise<{ etag: string }> {
    const target = this.pathFor(objectKey);
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    const root = await realpath(this.root);
    const parent = await realpath(dirname(target));
    // COSEC: do not follow a redirected artifact directory, and publish only complete uploads.
    if (parent !== root && !parent.startsWith(`${root}${sep}`)) throw new Error("Artifact path escaped its root");
    const temp = `${target}.${randomBytes(12).toString("hex")}.upload`;
    const hash = createHash("sha256");
    let bytes = 0;
    const limit = this.maxObjectBytes;
    const counter = new Transform({ transform(chunk: Buffer, _encoding, callback) {
      bytes += chunk.length;
      if (bytes > limit) { callback(new Error("Artifact exceeds the configured size limit")); return; }
      hash.update(chunk); callback(null, chunk);
    } });
    try {
      await pipeline(source, counter, createWriteStream(temp, { flags: "wx", mode: 0o600 }));
      await rename(temp, target);
      return { etag: hash.digest("hex") };
    } finally { await rm(temp, { force: true }); }
  }

  async openStream(objectKey: string) {
    const file = await realpath(this.pathFor(objectKey));
    const root = await realpath(this.root);
    if (!file.startsWith(`${root}${sep}`)) throw new Error("Artifact path escaped its root");
    const info = await stat(file);
    if (!info.isFile() || info.size > this.maxObjectBytes) throw new Error("Artifact exceeds the configured size limit or is not a file");
    return { size: info.size, stream: createReadStream(file) };
  }

  async createSignedUrl(
    objectKey: string,
    method: "GET" | "PUT",
    expiresSeconds: number,
  ): Promise<string> {
    if (!Number.isSafeInteger(expiresSeconds) || expiresSeconds < 1 || expiresSeconds > 86_400) {
      throw new Error("Invalid Singlebox artifact URL lifetime");
    }
    const payload = Buffer.from(JSON.stringify({
      key: safeObjectKey(objectKey),
      method,
      expiresAt: Date.now() + expiresSeconds * 1000,
    })).toString("base64url");
    const signature = createHmac("sha256", this.signingKey).update(payload).digest("base64url");
    return `${this.publicBaseUrl}/api/singlebox/artifacts/${payload}.${signature}`;
  }

  resolveSignedRequest(token: string, method: string): string {
    const [payload, providedSignature, ...rest] = token.split(".");
    if (!payload || !providedSignature || rest.length) throw new Error("Invalid artifact token");
    const expectedSignature = createHmac("sha256", this.signingKey).update(payload).digest();
    let actualSignature: Buffer;
    try { actualSignature = Buffer.from(providedSignature, "base64url"); }
    catch { throw new Error("Invalid artifact token"); }
    // 以下为安全注释COSEC：常量时间比较防止签名侧信道泄露。
    if (actualSignature.length !== expectedSignature.length
        || !timingSafeEqual(actualSignature, expectedSignature)) {
      throw new Error("Invalid artifact token");
    }
    const decoded = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as {
      key?: unknown; method?: unknown; expiresAt?: unknown;
    };
    if (decoded.method !== method || !Number.isFinite(Number(decoded.expiresAt))
        || Number(decoded.expiresAt) < Date.now()) {
      throw new Error("Artifact token expired or has the wrong method");
    }
    return safeObjectKey(String(decoded.key ?? ""));
  }
}
