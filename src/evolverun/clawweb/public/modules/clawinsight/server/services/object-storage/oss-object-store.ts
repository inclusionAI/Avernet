import OSS from "ali-oss";
import type { OssCredentialProvider, OssCredentials } from "./mist-credential-provider.js";

export type OssObject = {
  content: Buffer;
  etag: string | null;
  contentType: string | null;
};

export type OssGetOptions = {
  versionId?: string;
};

export type OssSdkGetResult = {
  content?: unknown;
  res?: { headers?: object };
};

export type OssSdkPutResult = {
  name?: string;
  url?: string;
  res?: { headers?: object };
};

export interface OssSdkClient {
  get(objectKey: string, file?: unknown, options?: Record<string, unknown>): Promise<OssSdkGetResult>;
  put?(
    objectKey: string,
    content: Buffer | Uint8Array | string,
    options?: Record<string, unknown>,
  ): Promise<OssSdkPutResult>;
  signatureUrl?(
    objectKey: string,
    options?: Record<string, unknown>,
  ): string;
  signatureUrlV4?(
    method: "GET" | "PUT",
    expires: number,
    request: { headers?: object; queries?: object } | undefined,
    objectKey: string,
    additionalHeaders?: string[],
  ): Promise<string>;
}

export interface ObjectStore {
  getObject(objectKey: string, options?: OssGetOptions): Promise<OssObject>;
  putObject?(
    objectKey: string,
    content: Buffer | Uint8Array | string,
    contentType: string,
  ): Promise<{ etag: string | null }>;
}

export type MistOssObjectStoreOptions = {
  endpoint: string;
  bucketName: string;
  region?: string;
  credentialProvider: OssCredentialProvider;
  timeoutMs?: number;
  maxPayloadBytes?: number;
  signedUrlVersion?: "v1" | "v4";
  clientFactory?: (credentials: OssCredentials) => OssSdkClient;
};

function required(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} 不能为空`);
  return normalized;
}

function header(headers: object | undefined, name: string): unknown {
  if (!headers) return undefined;
  const expected = name.toLowerCase();
  return Object.entries(headers as Record<string, unknown>)
    .find(([key]) => key.toLowerCase() === expected)?.[1];
}

function normalizeHeader(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toBuffer(content: unknown): Buffer {
  if (Buffer.isBuffer(content)) return content;
  if (content instanceof Uint8Array) return Buffer.from(content);
  if (typeof content === "string") return Buffer.from(content, "utf8");
  throw new Error("OSS GetObject 未返回可读取的内容");
}

/** Shared server-side OSS reader backed by runtime Mist credentials. */
export class MistOssObjectStore implements ObjectStore {
  private readonly endpoint: string;
  private readonly bucketName: string;
  private readonly credentialProvider: OssCredentialProvider;
  private readonly timeoutMs: number;
  private readonly maxPayloadBytes: number;
  private readonly signedUrlVersion: "v1" | "v4";
  private readonly clientFactory: (credentials: OssCredentials) => OssSdkClient;

  constructor(options: MistOssObjectStoreOptions) {
    this.endpoint = required(options.endpoint, "OSS endpoint");
    this.bucketName = required(options.bucketName, "OSS bucketName");
    this.credentialProvider = options.credentialProvider;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.maxPayloadBytes = options.maxPayloadBytes ?? 10 * 1024 * 1024;
    this.signedUrlVersion = options.signedUrlVersion ?? "v4";
    this.clientFactory = options.clientFactory ?? ((credential) => new OSS({
      endpoint: this.endpoint,
      region: options.region ?? "oss-cn-shanghai",
      bucket: this.bucketName,
      accessKeyId: credential.accessKeyId,
      accessKeySecret: credential.accessKeySecret,
      ...(credential.stsToken ? { stsToken: credential.stsToken } : {}),
      timeout: this.timeoutMs,
    }) as unknown as OssSdkClient);
  }

  async getObject(objectKey: string, options: OssGetOptions = {}): Promise<OssObject> {
    const normalizedKey = required(objectKey, "OSS objectKey").replace(/^\/+/, "");
    const credentials = await this.credentialProvider.getCredentials();
    const client = this.clientFactory(credentials);
    const getOptions: Record<string, unknown> = { timeout: this.timeoutMs };
    if (options.versionId) getOptions.versionId = options.versionId;
    const result = await client.get(normalizedKey, undefined, getOptions);
    const content = toBuffer(result.content);
    if (content.byteLength > this.maxPayloadBytes) {
      throw new Error(`OSS 对象超过 ${this.maxPayloadBytes} 字节限制`);
    }
    return {
      content,
      etag: normalizeHeader(header(result.res?.headers, "etag")),
      contentType: normalizeHeader(header(result.res?.headers, "content-type")),
    };
  }

  async putObject(
    objectKey: string,
    content: Buffer | Uint8Array | string,
    contentType: string,
  ): Promise<{ etag: string | null }> {
    const normalizedKey = required(objectKey, "OSS objectKey").replace(/^\/+/, "");
    const payload = typeof content === "string" ? Buffer.from(content, "utf8") : Buffer.from(content);
    if (payload.byteLength > this.maxPayloadBytes) {
      throw new Error(`OSS 对象超过 ${this.maxPayloadBytes} 字节限制`);
    }
    const credentials = await this.credentialProvider.getCredentials();
    const client = this.clientFactory(credentials);
    if (typeof client.put !== "function") throw new Error("OSS SDK 不支持 PutObject");
    const result = await client.put(normalizedKey, payload, {
      timeout: this.timeoutMs,
      headers: { "Content-Type": required(contentType, "OSS contentType") },
    });
    return { etag: normalizeHeader(header(result.res?.headers, "etag")) };
  }

  async createSignedUrl(
    objectKey: string,
    method: "GET" | "PUT",
    expiresSeconds: number,
    headers: Record<string, string> = {},
    queries: Record<string, string> = {},
  ): Promise<string> {
    const normalizedKey = required(objectKey, "OSS objectKey").replace(/^\/+/, "");
    if (!Number.isSafeInteger(expiresSeconds) || expiresSeconds < 1 || expiresSeconds > 86_400) {
      throw new Error("OSS 签名 URL 有效期必须在 1 到 86400 秒之间");
    }
    const credentials = await this.credentialProvider.getCredentials();
    const client = this.clientFactory(credentials);
    if (this.signedUrlVersion === "v1") {
      if (typeof client.signatureUrl !== "function") throw new Error("OSS SDK 不支持 V1 签名 URL");
      const responseContentDisposition = queries["response-content-disposition"];
      return client.signatureUrl(normalizedKey, {
        expires: expiresSeconds,
        method,
        ...headers,
        ...(responseContentDisposition
          ? { response: { "content-disposition": responseContentDisposition } }
          : {}),
      });
    }
    const signedHeaders = Object.keys(headers).map((name) => name.toLowerCase());
    if (typeof client.signatureUrlV4 !== "function") throw new Error("OSS SDK 不支持 V4 签名 URL");
    return client.signatureUrlV4(
      method,
      expiresSeconds,
      signedHeaders.length || Object.keys(queries).length ? { headers, queries } : undefined,
      normalizedKey,
      signedHeaders,
    );
  }
}
