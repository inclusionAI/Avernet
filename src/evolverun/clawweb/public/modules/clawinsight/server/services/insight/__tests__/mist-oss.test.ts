import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_CLAWWEB_OSS_SECRET_NAMES } from "../../object-storage/clawweb-oss-runtime.js";
import { MistCredentialProvider, MistSecretValueProvider } from "../../object-storage/mist-credential-provider.js";
import { MistOssObjectStore } from "../../object-storage/oss-object-store.js";
import { OssEvidenceProvider } from "../providers/oss-evidence-provider.js";

const SESSION_ID = "7e82d8f2-a7f9-40ab-b0ac-7a6142ce3ca0";
const OBJECT_KEY = `evolution/pre/evidence/dev_local/20260603_fp6to0gv/20260726/${SESSION_ID}.json`;
const PAYLOAD_REF = `oss://antsys-agentclaw-prod/${OBJECT_KEY}`;

describe("MistCredentialProvider", () => {
  it("initializes Layotto once, reads the configured secret and caches credentials", async () => {
    const calls: Array<{ action: string; request: Record<string, unknown> }> = [];
    const client = {
      init(request: Record<string, unknown>, callback: (error: Error | null, response?: Record<string, never>) => void) {
        calls.push({ action: "init", request });
        callback(null, {});
      },
      getSecret(request: Record<string, unknown>, callback: (error: Error | null, response?: { data?: Record<string, string> }) => void) {
        calls.push({ action: "getSecret", request });
        callback(null, { data: {
          "Layotto-MIST-Output-SecretUser": "ak-id",
          "Layotto-MIST-Output-SecretValue": "ak-secret",
        } });
      },
    };
    const provider = new MistCredentialProvider({
      endpoint: "127.0.0.1:11004",
      tenant: "ALIPAY",
      mode: "pre",
      appName: "clawweb",
      secretName: DEFAULT_CLAWWEB_OSS_SECRET_NAMES.pre,
      credentialTtlMs: 60_000,
      client,
    });

    await expect(provider.getCredentials()).resolves.toEqual({
      accessKeyId: "ak-id",
      accessKeySecret: "ak-secret",
    });
    await provider.getCredentials();
    expect(calls.map((item) => item.action)).toEqual(["init", "getSecret"]);
    expect(calls[0].request).toEqual(expect.objectContaining({
      app: "clawweb",
      metadata: expect.objectContaining({
        "component-type": "mist",
        "Layotto-MIST-Tenant": "ALIPAY",
        "Layotto-MIST-Mode": "pre",
        "Layotto-MIST-Secret-List": "other_manual_clawweb_agentclaw_oss_pre",
      }),
    }));
  });

  it("can read and cache one runtime-only SecretValue without exposing SecretUser", async () => {
    const calls: string[] = [];
    const client = {
      init(_request: Record<string, unknown>, callback: (error: Error | null, response?: Record<string, never>) => void) {
        calls.push("init");
        callback(null, {});
      },
      getSecret(_request: Record<string, unknown>, callback: (error: Error | null, response?: { data?: Record<string, string> }) => void) {
        calls.push("getSecret");
        callback(null, { data: {
          "Layotto-MIST-Output-SecretUser": "unused-user",
          "Layotto-MIST-Output-SecretValue": "runtime-secret",
        } });
      },
    };
    const provider = new MistSecretValueProvider({
      endpoint: "127.0.0.1:11004",
      tenant: "ALIPAY",
      mode: "pre",
      appName: "clawweb",
      secretName: "runtime-secret-name",
      credentialTtlMs: 60_000,
      client,
    });

    await expect(provider.getSecretValue()).resolves.toBe("runtime-secret");
    await expect(provider.getSecretValue()).resolves.toBe("runtime-secret");
    expect(calls).toEqual(["init", "getSecret"]);
  });
});

describe("OssEvidenceProvider", () => {
  it("reads the exact object version, verifies ETag and validates Evidence", async () => {
    const fixture = await readFile(join(process.cwd(), "server/fixtures/insight/v1", OBJECT_KEY));
    let requestedKey = "";
    let requestedOptions: Record<string, unknown> | undefined;
    const objectStore = new MistOssObjectStore({
      endpoint: "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com",
      bucketName: "antsys-agentclaw-prod",
      credentialProvider: {
        async getCredentials() {
          return { accessKeyId: "memory-only-id", accessKeySecret: "memory-only-secret" };
        },
      },
      clientFactory: () => ({
        async get(objectKey, _file, options) {
          requestedKey = objectKey;
          requestedOptions = options;
          return { content: fixture, res: { headers: { etag: '"fixture-etag"' } } };
        },
      }),
    });
    const provider = new OssEvidenceProvider({
      objectStore,
      expectedEnvironment: "pre",
    });

    const evidence = await provider.readEvidence(PAYLOAD_REF, {
      versionId: "version-1",
      expectedEtag: "fixture-etag",
    });
    expect(evidence.session_id).toBe(SESSION_ID);
    expect(requestedKey).toBe(OBJECT_KEY);
    expect(requestedOptions).toEqual(expect.objectContaining({ versionId: "version-1" }));
  });

  it("lets pre read a production Evidence URI through the production object store", async () => {
    const fixture = await readFile(join(process.cwd(), "server/fixtures/insight/v1", OBJECT_KEY));
    const requestedKeys: string[] = [];
    const provider = new OssEvidenceProvider({
      expectedEnvironment: "pre",
      allowProductionFallback: true,
      objectStore: { async getObject() { throw new Error("pre store should not be used"); } },
      productionObjectStore: {
        async getObject(objectKey) {
          requestedKeys.push(objectKey);
          return { content: fixture, etag: "fixture-etag", contentType: "application/json" };
        },
      },
    });
    const prodRef = PAYLOAD_REF.replace("/evolution/pre/", "/evolution/prod/");

    await expect(provider.readEvidence(prodRef, { expectedEtag: "fixture-etag" }))
      .resolves.toEqual(expect.objectContaining({ session_id: SESSION_ID }));
    expect(requestedKeys).toEqual([OBJECT_KEY.replace("evolution/pre/", "evolution/prod/")]);
  });

  it("can use the current pre credential store for a production Evidence URI", async () => {
    const fixture = await readFile(join(process.cwd(), "server/fixtures/insight/v1", OBJECT_KEY));
    const requestedKeys: string[] = [];
    const sharedStore = {
      async getObject(objectKey: string) {
        requestedKeys.push(objectKey);
        return { content: fixture, etag: "fixture-etag", contentType: "application/json" };
      },
    };
    const provider = new OssEvidenceProvider({
      expectedEnvironment: "pre",
      allowProductionFallback: true,
      objectStore: sharedStore,
      productionObjectStore: sharedStore,
    });
    const prodRef = PAYLOAD_REF.replace("/evolution/pre/", "/evolution/prod/");

    await expect(provider.readEvidence(prodRef, { expectedEtag: "fixture-etag" }))
      .resolves.toEqual(expect.objectContaining({ session_id: SESSION_ID }));
    expect(requestedKeys).toEqual([OBJECT_KEY.replace("evolution/pre/", "evolution/prod/")]);
  });

  it("falls back from a missing pre object to the matching production object", async () => {
    const fixture = await readFile(join(process.cwd(), "server/fixtures/insight/v1", OBJECT_KEY));
    const requestedKeys: string[] = [];
    const provider = new OssEvidenceProvider({
      expectedEnvironment: "pre",
      allowProductionFallback: true,
      objectStore: {
        async getObject(objectKey) {
          requestedKeys.push(objectKey);
          throw Object.assign(new Error("missing"), { code: "NoSuchKey", status: 404 });
        },
      },
      productionObjectStore: {
        async getObject(objectKey) {
          requestedKeys.push(objectKey);
          return { content: fixture, etag: "fixture-etag", contentType: "application/json" };
        },
      },
    });

    await expect(provider.readEvidence(PAYLOAD_REF, { expectedEtag: "fixture-etag" }))
      .resolves.toEqual(expect.objectContaining({ session_id: SESSION_ID }));
    expect(requestedKeys).toEqual([
      OBJECT_KEY,
      OBJECT_KEY.replace("evolution/pre/", "evolution/prod/"),
    ]);
  });

  it("does not hide non-404 pre OSS failures behind production fallback", async () => {
    let productionReads = 0;
    const provider = new OssEvidenceProvider({
      expectedEnvironment: "pre",
      allowProductionFallback: true,
      objectStore: { async getObject() { throw Object.assign(new Error("forbidden"), { status: 403 }); } },
      productionObjectStore: {
        async getObject() {
          productionReads += 1;
          return { content: Buffer.from("{}"), etag: null, contentType: "application/json" };
        },
      },
    });

    await expect(provider.readEvidence(PAYLOAD_REF)).rejects.toThrow("forbidden");
    expect(productionReads).toBe(0);
  });

  it("keeps production runtime strict about pre Evidence URIs", async () => {
    const provider = new OssEvidenceProvider({
      expectedEnvironment: "prod",
      objectStore: { async getObject() { return { content: Buffer.from("{}"), etag: null, contentType: null }; } },
    });
    await expect(provider.readEvidence(PAYLOAD_REF)).rejects.toThrow("OSS env 必须为 prod");
  });
});
