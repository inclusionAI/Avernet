import { describe, expect, it, vi } from "vitest";
import { MistOssObjectStore, type OssSdkClient } from "../oss-object-store.js";

const credentials = {
  accessKeyId: "test-ak",
  accessKeySecret: "test-sk",
};

function createStore(client: OssSdkClient, signedUrlVersion?: "v1" | "v4") {
  return new MistOssObjectStore({
    endpoint: "https://example.oss.invalid",
    bucketName: "test-bucket",
    credentialProvider: { getCredentials: async () => credentials },
    signedUrlVersion,
    clientFactory: () => client,
  });
}

describe("MistOssObjectStore signed URL version", () => {
  it("uses V1 only when explicitly configured", async () => {
    const signatureUrl = vi.fn(() => "https://example.oss.invalid/v1");
    const signatureUrlV4 = vi.fn(async () => "https://example.oss.invalid/v4");
    const store = createStore({ get: vi.fn(), signatureUrl, signatureUrlV4 }, "v1");

    await expect(store.createSignedUrl("evolution/task/artifact.zip", "PUT", 3600))
      .resolves.toBe("https://example.oss.invalid/v1");
    expect(signatureUrl).toHaveBeenCalledWith("evolution/task/artifact.zip", {
      expires: 3600,
      method: "PUT",
    });
    expect(signatureUrlV4).not.toHaveBeenCalled();
  });

  it("keeps V4 as the default for other OSS callers", async () => {
    const signatureUrl = vi.fn(() => "https://example.oss.invalid/v1");
    const signatureUrlV4 = vi.fn(async () => "https://example.oss.invalid/v4");
    const store = createStore({ get: vi.fn(), signatureUrl, signatureUrlV4 });

    await expect(store.createSignedUrl("other/artifact.zip", "GET", 3600))
      .resolves.toBe("https://example.oss.invalid/v4");
    expect(signatureUrlV4).toHaveBeenCalled();
    expect(signatureUrl).not.toHaveBeenCalled();
  });

  it("signs the requested download filename into a V4 URL", async () => {
    const signatureUrlV4 = vi.fn(async () => "https://example.oss.invalid/v4-download");
    const store = createStore({ get: vi.fn(), signatureUrlV4 });

    await expect(store.createSignedUrl(
      "evolution/EV-1/baseline/artifact_v0.zip",
      "GET",
      3600,
      {},
      { "response-content-disposition": 'attachment; filename="EV-1-baseline.zip"' },
    )).resolves.toBe("https://example.oss.invalid/v4-download");
    expect(signatureUrlV4).toHaveBeenCalledWith(
      "GET",
      3600,
      {
        headers: {},
        queries: {
          "response-content-disposition": 'attachment; filename="EV-1-baseline.zip"',
        },
      },
      "evolution/EV-1/baseline/artifact_v0.zip",
      [],
    );
  });

  it("writes a bounded object with an explicit content type", async () => {
    const put = vi.fn(async () => ({ res: { headers: { etag: "etag-1" } } }));
    const store = createStore({ get: vi.fn(), put });

    await expect(store.putObject("repair/dev/runs/repair-1/run.json", "{}\n", "application/json"))
      .resolves.toEqual({ etag: "etag-1" });
    expect(put).toHaveBeenCalledWith(
      "repair/dev/runs/repair-1/run.json",
      Buffer.from("{}\n"),
      expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
    );
  });
});
