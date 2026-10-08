import { getArtifactBucket, type ObjectStore } from "./oss-object-store.js";

/** Host-selected storage for Stage implementations and versioned Skill packages. */
export type SkillPackageStorageOptions = {
  bucket: string;
  prefix: string;
  store: ObjectStore;
  urlStore?: Pick<ObjectStore, "createSignedUrl">;
};
type Location = SkillPackageStorageOptions;

function safeKey(value: string): string {
  if (!value || /[\\?#%\u0000-\u0020]/.test(value)
    || value.split("/").some(part => !part || part === "." || part === "..")) {
    throw new Error("Skill 包文件路径不合法");
  }
  return value;
}

function packageKey(value: string): string {
  safeKey(value);
  const logicalKey = value.replace(/^evolve\//, "");
  if (!/^(skills|stage-implementations|stage-tests)\//.test(logicalKey)) {
    throw new Error("文件不属于 Skill 包存储范围");
  }
  return logicalKey;
}

function location(input: Location): Location {
  if (!/^[a-z0-9][a-z0-9.-]{1,62}$/.test(input.bucket)) throw new Error("Skill 包 Bucket 不合法");
  const prefix = input.prefix.replace(/\/+$/, "");
  if (prefix) safeKey(prefix);
  return { ...input, prefix };
}

/** References select only explicitly configured stores; no arbitrary-bucket signing. */
export class SkillPackageStorage {
  private readonly current: Location;
  private readonly locations: Location[];

  constructor(options?: SkillPackageStorageOptions, legacyStore?: ObjectStore,
    legacyUrlStore?: Pick<ObjectStore, "createSignedUrl">) {
    const unavailable: ObjectStore = {
      async getObject() { throw new Error("Skill 包存储未配置"); },
      async createSignedUrl() { throw new Error("Skill 包存储未配置"); },
    };
    const legacy = location({ bucket: getArtifactBucket(), prefix: "", store: legacyStore ?? unavailable,
      urlStore: legacyUrlStore ?? legacyStore });
    this.current = options ? location(options) : legacy;
    this.locations = options ? [this.current, legacy] : [legacy];
  }

  get canWrite(): boolean { return typeof this.current.store.putObject === "function"; }

  ref(key: string): string {
    packageKey(key);
    return `oss://${this.current.bucket}/${this.current.prefix ? `${this.current.prefix}/` : ""}${key}`;
  }

  private resolve(ref: string): { location: Location; key: string; logicalKey: string } {
    for (const entry of this.locations) {
      const prefix = `oss://${entry.bucket}/${entry.prefix ? `${entry.prefix}/` : ""}`;
      if (!ref.startsWith(prefix)) continue;
      const logicalKey = ref.slice(prefix.length);
      safeKey(logicalKey);
      if (entry.prefix) packageKey(logicalKey);
      return { location: entry, key: `${entry.prefix ? `${entry.prefix}/` : ""}${logicalKey}`, logicalKey };
    }
    throw new Error("Skill 包引用不属于已配置的存储");
  }

  matches(ref: string, key: string): boolean {
    return packageKey(this.resolve(ref).logicalKey) === packageKey(key);
  }

  async put(key: string, content: Buffer | Uint8Array | string): Promise<string> {
    const ref = this.ref(key);
    const resolved = this.resolve(ref);
    const put = resolved.location.store.putObject;
    if (!put) throw new Error("Skill 包存储不支持上传");
    await put.call(resolved.location.store, resolved.key, content, "application/zip");
    return ref;
  }

  async read(ref: string) {
    const resolved = this.resolve(ref);
    return resolved.location.store.getObject(resolved.key);
  }

  async sign(ref: string, method: "GET" | "PUT", expiresSeconds: number, headers?: Record<string, string>) {
    const resolved = this.resolve(ref);
    const signer = resolved.location.urlStore ?? resolved.location.store;
    return headers ? signer.createSignedUrl(resolved.key, method, expiresSeconds, headers)
      : signer.createSignedUrl(resolved.key, method, expiresSeconds);
  }
}
