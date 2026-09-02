/** Provider-neutral object storage contract injected by the host. */
export const DEFAULT_ARTIFACT_BUCKET = "clawevolve-artifacts";
export type StoredObject = {
  content: Buffer;
  etag: string | null;
  contentType: string | null;
};

export interface ObjectStore {
  getObject(objectKey: string, options?: { versionId?: string }): Promise<StoredObject>;
  putObject?(
    objectKey: string,
    content: Buffer | Uint8Array | string,
    contentType: string,
  ): Promise<{ etag: string | null }>;
  createSignedUrl(
    objectKey: string,
    method: "GET" | "PUT",
    expiresSeconds: number,
    headers?: Record<string, string>,
    queries?: Record<string, string>,
  ): Promise<string>;
}

/** Explicit unavailable adapter used until the host supplies object storage. */
export class UnavailableObjectStore implements ObjectStore {
  private unavailable(): never {
    throw new Error("Clawevolve artifact storage is unavailable");
  }
  async getObject(): Promise<StoredObject> { return this.unavailable(); }
  async createSignedUrl(): Promise<string> { return this.unavailable(); }
}
