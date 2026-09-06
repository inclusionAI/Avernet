/** Provider-neutral object storage contract injected by the host. */
export const DEFAULT_ARTIFACT_BUCKET = "clawevolve-artifacts";
let configuredArtifactBucket: string | undefined;

function normalizeArtifactBucket(value: string): string {
  const bucket = value.trim();
  if (!/^[a-z0-9][a-z0-9.-]{1,62}$/.test(bucket)) {
    throw new Error("Clawevolve artifact bucket is invalid");
  }
  return bucket;
}

/** Configure the exact artifact bucket supplied by an embedding host. */
export function configureArtifactBucket(value?: string): void {
  configuredArtifactBucket = value ? normalizeArtifactBucket(value) : undefined;
}

export function getArtifactBucket(
  env: NodeJS.ProcessEnv = process.env,
): string {
  return configuredArtifactBucket
    ?? (env.CLAWEVOLVE_ARTIFACT_BUCKET
      ? normalizeArtifactBucket(env.CLAWEVOLVE_ARTIFACT_BUCKET)
      : DEFAULT_ARTIFACT_BUCKET);
}
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
