import { credentials, loadPackageDefinition, type Client, type ServiceClientConstructor } from "@grpc/grpc-js";
import { loadSync } from "@grpc/proto-loader";
import { fileURLToPath } from "node:url";

const STORE_NAME = "mist";
const OUTPUT_SECRET_USER = "Layotto-MIST-Output-SecretUser";
const OUTPUT_SECRET_VALUE = "Layotto-MIST-Output-SecretValue";

export type OssCredentials = {
  accessKeyId: string;
  accessKeySecret: string;
  stsToken?: string;
};

export interface OssCredentialProvider {
  getCredentials(): Promise<OssCredentials>;
}

type UnaryCallback<T> = (error: Error | null, response?: T) => void;

type MistRpcClient = {
  init(request: { app: string; metadata: Record<string, string> }, callback: UnaryCallback<Record<string, never>>): void;
  getSecret(
    request: { storeName: string; key: string; metadata: Record<string, string> },
    callback: UnaryCallback<{ data?: Record<string, string> }>,
  ): void;
  close?(): void;
};

export type MistCredentialProviderOptions = {
  endpoint: string;
  tenant: string;
  mode: string;
  appName: string;
  secretName: string;
  timeoutMs?: number;
  credentialTtlMs?: number;
  client?: MistRpcClient;
  now?: () => number;
};

export type MistSecretValueProviderOptions = MistCredentialProviderOptions;

function required(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} 不能为空`);
  return normalized;
}

function normalizeEndpoint(endpoint: string): string {
  return required(endpoint, "MIST endpoint").replace(/^https?:\/\//, "");
}

function unaryCall<TRequest, TResponse>(
  invoke: (request: TRequest, callback: UnaryCallback<TResponse>) => void,
  request: TRequest,
  timeoutMs: number,
  action: string,
): Promise<TResponse> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${action} 超过 ${timeoutMs}ms`)), timeoutMs);
    invoke(request, (error, response) => {
      clearTimeout(timer);
      if (error) reject(error);
      else if (response === undefined) reject(new Error(`${action} 返回空响应`));
      else resolve(response);
    });
  });
}

function createGrpcClient(endpoint: string): MistRpcClient {
  const loaderOptions = {
    keepCase: false,
    longs: String,
    enums: String,
    defaults: true,
    oneofs: true,
  } as const;
  const mosnDefinition = loadSync(
    fileURLToPath(new URL("./proto/layotto-mosn.proto", import.meta.url)),
    loaderOptions,
  );
  const runtimeDefinition = loadSync(
    fileURLToPath(new URL("./proto/layotto-runtime.proto", import.meta.url)),
    loaderOptions,
  );
  const mosnPackage = loadPackageDefinition(mosnDefinition) as Record<string, unknown>;
  const runtimePackage = loadPackageDefinition(runtimeDefinition) as Record<string, unknown>;
  const MosnRuntime = (((mosnPackage.mosn as Record<string, unknown>).proto as Record<string, unknown>)
    .runtime as Record<string, unknown>).v1 as Record<string, ServiceClientConstructor>;
  const Runtime = (((runtimePackage.spec as Record<string, unknown>).proto as Record<string, unknown>)
    .runtime as Record<string, unknown>).v1 as Record<string, ServiceClientConstructor>;
  const channelCredentials = credentials.createInsecure();
  const mosn = new MosnRuntime.MosnRuntime(endpoint, channelCredentials) as unknown as Client & {
    init: MistRpcClient["init"];
  };
  const runtime = new Runtime.Runtime(endpoint, channelCredentials) as unknown as Client & {
    getSecret: MistRpcClient["getSecret"];
  };
  return {
    init: mosn.init.bind(mosn),
    getSecret: runtime.getSecret.bind(runtime),
    close: () => {
      mosn.close();
      runtime.close();
    },
  };
}

/** Runtime-only credential provider. AK/SK are cached in memory and never logged or persisted. */
export class MistCredentialProvider implements OssCredentialProvider {
  private readonly client: MistRpcClient;
  private readonly tenant: string;
  private readonly mode: string;
  private readonly appName: string;
  private readonly secretName: string;
  private readonly timeoutMs: number;
  private readonly credentialTtlMs: number;
  private readonly now: () => number;
  private initPromise: Promise<void> | null = null;
  private cached: { value: OssCredentials; expiresAt: number } | null = null;

  constructor(options: MistCredentialProviderOptions) {
    this.tenant = required(options.tenant, "MIST tenant");
    this.mode = required(options.mode, "MIST mode");
    this.appName = required(options.appName, "MIST appName");
    this.secretName = required(options.secretName, "MIST secretName");
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.credentialTtlMs = options.credentialTtlMs ?? 5 * 60_000;
    this.now = options.now ?? Date.now;
    this.client = options.client ?? createGrpcClient(normalizeEndpoint(options.endpoint));
  }

  async getCredentials(): Promise<OssCredentials> {
    const now = this.now();
    if (this.cached && this.cached.expiresAt > now) return this.cached.value;
    await this.ensureInitialized();
    const response = await unaryCall(
      this.client.getSecret.bind(this.client),
      { storeName: STORE_NAME, key: this.secretName, metadata: {} },
      this.timeoutMs,
      `Mist GetSecret(${this.secretName})`,
    );
    const data = response.data ?? {};
    const accessKeyId = data[OUTPUT_SECRET_USER]?.trim();
    const accessKeySecret = data[OUTPUT_SECRET_VALUE]?.trim();
    if (!accessKeyId || !accessKeySecret) {
      throw new Error("Mist 返回的 OSS 凭证缺少 SecretUser 或 SecretValue");
    }
    const value = { accessKeyId, accessKeySecret };
    this.cached = { value, expiresAt: now + Math.max(1_000, this.credentialTtlMs) };
    return value;
  }

  private ensureInitialized(): Promise<void> {
    if (!this.initPromise) {
      this.initPromise = unaryCall(
        this.client.init.bind(this.client),
        {
          app: this.appName,
          metadata: {
            "component-type": "mist",
            "Layotto-MIST-Tenant": this.tenant,
            "Layotto-MIST-Mode": this.mode,
            "Layotto-MIST-App-Name": this.appName,
            "Layotto-MIST-Secret-List": this.secretName,
          },
        },
        this.timeoutMs,
        "Mist Init",
      ).then(() => undefined).catch((error: unknown) => {
        this.initPromise = null;
        throw error;
      });
    }
    return this.initPromise;
  }
}

/** Runtime-only provider for a single Mist SecretValue. The value never leaves memory. */
export class MistSecretValueProvider {
  private readonly client: MistRpcClient;
  private readonly tenant: string;
  private readonly mode: string;
  private readonly appName: string;
  private readonly secretName: string;
  private readonly timeoutMs: number;
  private readonly credentialTtlMs: number;
  private readonly now: () => number;
  private initPromise: Promise<void> | null = null;
  private cached: { value: string; expiresAt: number } | null = null;

  constructor(options: MistSecretValueProviderOptions) {
    this.tenant = required(options.tenant, "MIST tenant");
    this.mode = required(options.mode, "MIST mode");
    this.appName = required(options.appName, "MIST appName");
    this.secretName = required(options.secretName, "MIST secretName");
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.credentialTtlMs = options.credentialTtlMs ?? 5 * 60_000;
    this.now = options.now ?? Date.now;
    this.client = options.client ?? createGrpcClient(normalizeEndpoint(options.endpoint));
  }

  async getSecretValue(): Promise<string> {
    const now = this.now();
    if (this.cached && this.cached.expiresAt > now) return this.cached.value;
    await this.ensureInitialized();
    const response = await unaryCall(
      this.client.getSecret.bind(this.client),
      { storeName: STORE_NAME, key: this.secretName, metadata: {} },
      this.timeoutMs,
      `Mist GetSecret(${this.secretName})`,
    );
    const value = response.data?.[OUTPUT_SECRET_VALUE]?.trim();
    if (!value) throw new Error("Mist 返回的凭据缺少 SecretValue");
    this.cached = { value, expiresAt: now + Math.max(1_000, this.credentialTtlMs) };
    return value;
  }

  private ensureInitialized(): Promise<void> {
    if (!this.initPromise) {
      this.initPromise = unaryCall(
        this.client.init.bind(this.client),
        {
          app: this.appName,
          metadata: {
            "component-type": "mist",
            "Layotto-MIST-Tenant": this.tenant,
            "Layotto-MIST-Mode": this.mode,
            "Layotto-MIST-App-Name": this.appName,
            "Layotto-MIST-Secret-List": this.secretName,
          },
        },
        this.timeoutMs,
        "Mist Init",
      ).then(() => undefined).catch((error: unknown) => {
        this.initPromise = null;
        throw error;
      });
    }
    return this.initPromise;
  }
}
