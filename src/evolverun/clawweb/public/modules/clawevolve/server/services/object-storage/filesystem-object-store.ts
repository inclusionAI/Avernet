import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";
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

  constructor(root: string) {
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
    if (content.byteLength > MAX_OBJECT_BYTES) throw new Error("Artifact exceeds the 10 MiB limit");
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
    if (payload.byteLength > MAX_OBJECT_BYTES) throw new Error("Artifact exceeds the 10 MiB limit");
    const target = this.pathFor(objectKey);
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    await writeFile(target, payload, { mode: 0o600 });
    return { etag: createHash("sha256").update(payload).digest("hex") };
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
    return `/api/singlebox/artifacts/${payload}.${signature}`;
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
