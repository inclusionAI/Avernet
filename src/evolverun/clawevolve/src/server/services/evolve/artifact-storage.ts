export type ArtifactStorageConfig = {
  bucket: string;
  scheme?: "oss" | "s3";
};

function normalizedScheme(config: ArtifactStorageConfig): string {
  const scheme = config.scheme?.trim() || "oss";
  if (scheme !== "oss" && scheme !== "s3") {
    throw new Error("Artifact storage scheme 不合法");
  }
  return scheme;
}

function normalizedBucket(config: ArtifactStorageConfig): string {
  const bucket = config.bucket.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$/.test(bucket)) {
    throw new Error("Artifact storage bucket 不合法");
  }
  return bucket;
}

export function artifactStoragePrefix(config: ArtifactStorageConfig): string {
  return `${normalizedScheme(config)}://${normalizedBucket(config)}/`;
}

export function artifactRefForObjectKey(
  config: ArtifactStorageConfig,
  objectKey: string,
): string {
  if (!objectKey || /^\/|\s|[\u0000-\u001f\u007f?#]/.test(objectKey)) {
    throw new Error("Artifact object key 不合法");
  }
  return `${artifactStoragePrefix(config)}${objectKey}`;
}

export function objectKeyFromArtifactRef(
  config: ArtifactStorageConfig,
  ref: unknown,
): string {
  const uri = String(ref ?? "");
  const prefix = artifactStoragePrefix(config);
  if (!uri.startsWith(prefix) || /\s|[\u0000-\u001f\u007f?#]/.test(uri)) {
    throw new Error("Artifact ref 不属于配置的存储位置");
  }
  const objectKey = uri.slice(prefix.length);
  if (!objectKey || objectKey.startsWith("/")) {
    throw new Error("Artifact object key 不合法");
  }
  return objectKey;
}
