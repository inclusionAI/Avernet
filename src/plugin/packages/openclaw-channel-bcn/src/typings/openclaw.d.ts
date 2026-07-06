/**
 * Type declarations for openclaw/plugin-sdk modules.
 *
 * These stubs allow the plugin to compile without installing the full openclaw
 * package. At runtime the host application provides the real implementations.
 */

declare module 'openclaw/plugin-sdk/core' {
  export interface AnyAgentTool {
    name: string;
    label?: string;
    description?: string;
    parameters?: any;
    execute(toolCallId: string, params: Record<string, unknown>): Promise<any>;
    [key: string]: any;
  }
  export interface OpenClawPluginToolContext {
    config?: any;
    runtimeConfig?: any;
    workspaceDir?: string;
    agentDir?: string;
    agentId?: string;
    sessionKey?: string;
    sessionId?: string;
    messageChannel?: string;
    agentAccountId?: string;
    deliveryContext?: any;
    requesterSenderId?: string;
    senderIsOwner?: boolean;
    sandboxed?: boolean;
    [key: string]: any;
  }
  export interface OpenClawPluginApi {
    runtime: any;
    pluginConfig: any;
    logger?: any;
    registrationMode?: string;
    registerChannel(channel: any): void;
    registerTool(factoryOrTool: AnyAgentTool | ((ctx: OpenClawPluginToolContext) => AnyAgentTool | AnyAgentTool[] | null | undefined), opts?: { name: string; optional?: boolean; [key: string]: any }): void;
    on(event: string, handler: (...args: any[]) => void): void;
    [key: string]: any;
  }
  export function emptyPluginConfigSchema(): any;
}

declare module 'openclaw/plugin-sdk/account-id' {
  export const DEFAULT_ACCOUNT_ID: string;
}

declare module 'openclaw/plugin-sdk/channel-config-helpers' {
  export function createScopedChannelConfigBase(opts: any): any;
  export function createScopedAccountConfigAccessors(opts: any): any;
  export function createScopedDmSecurityResolver<T = any>(opts: any): (ctx: T) => any;
}

declare module 'openclaw/plugin-sdk/allow-from' {
  export function formatAllowFromLowercase(opts: any): any;
}

declare module 'openclaw/plugin-sdk/runtime-store' {
  export function createPluginRuntimeStore<T = any>(
    errorMessage: string,
  ): { setRuntime: (rt: T) => void; getRuntime: () => T };
}

declare module 'openclaw/plugin-sdk/sandbox' {
  // --- Types ---

  export type SandboxBackendId = string;

  export type SandboxBackendExecSpec = {
    argv: string[];
    env: NodeJS.ProcessEnv;
    stdinMode: 'pipe-open' | 'pipe-closed';
    finalizeToken?: unknown;
  };

  export type SandboxBackendCommandParams = {
    script: string;
    args?: string[];
    stdin?: Buffer | string;
    allowFailure?: boolean;
    signal?: AbortSignal;
  };

  export type SandboxBackendCommandResult = {
    stdout: Buffer;
    stderr: Buffer;
    code: number;
  };

  export type SandboxBackendHandle = {
    id: SandboxBackendId;
    runtimeId: string;
    runtimeLabel: string;
    workdir: string;
    env?: Record<string, string>;
    configLabel?: string;
    configLabelKind?: string;
    capabilities?: { browser?: boolean };
    buildExecSpec(params: {
      command: string;
      workdir?: string;
      env: Record<string, string>;
      usePty: boolean;
    }): Promise<SandboxBackendExecSpec>;
    finalizeExec?(params: {
      status: 'completed' | 'failed';
      exitCode: number | null;
      timedOut: boolean;
      token?: unknown;
    }): Promise<void>;
    runShellCommand(params: SandboxBackendCommandParams): Promise<SandboxBackendCommandResult>;
    createFsBridge?(params: { sandbox: SandboxContext }): any;
  };

  export type SandboxBackendRuntimeInfo = {
    running: boolean;
    actualConfigLabel?: string;
    configLabelMatch: boolean;
  };

  export type SandboxBackendManager = {
    describeRuntime(params: {
      entry: any;
      config: any;
      agentId?: string;
    }): Promise<SandboxBackendRuntimeInfo>;
    removeRuntime(params: {
      entry: any;
      config: any;
      agentId?: string;
    }): Promise<void>;
  };

  export type SandboxBackendRegistration =
    | SandboxBackendFactory
    | { factory: SandboxBackendFactory; manager?: SandboxBackendManager };

  export type CreateSandboxBackendParams = {
    sessionKey: string;
    scopeKey: string;
    workspaceDir: string;
    agentWorkspaceDir: string;
    cfg: any;
  };

  export type SandboxBackendFactory = (
    params: CreateSandboxBackendParams,
  ) => Promise<SandboxBackendHandle>;

  export type RemoteShellSandboxHandle = {
    remoteWorkspaceDir: string;
    remoteAgentWorkspaceDir: string;
    runRemoteShellScript(params: SandboxBackendCommandParams): Promise<SandboxBackendCommandResult>;
  };

  export type SshSandboxSession = {
    command: string;
    configPath: string;
    host: string;
  };

  export type SshSandboxSettings = {
    command: string;
    target: string;
    strictHostKeyChecking: boolean;
    updateHostKeys: boolean;
    identityFile?: string;
    certificateFile?: string;
    knownHostsFile?: string;
    identityData?: string;
    certificateData?: string;
    knownHostsData?: string;
  };

  export type SandboxContext = {
    enabled: boolean;
    backendId: SandboxBackendId;
    sessionKey: string;
    workspaceDir: string;
    agentWorkspaceDir: string;
    workspaceAccess: 'none' | 'ro' | 'rw';
    runtimeId: string;
    runtimeLabel: string;
    containerName: string;
    containerWorkdir: string;
    tools: { allow: string[]; deny: string[] };
    browserAllowHostControl: boolean;
    browser?: any;
    fsBridge?: any;
    backend?: SandboxBackendHandle;
  };

  // --- Functions ---

  export function registerSandboxBackend(
    id: string,
    registration: SandboxBackendRegistration,
  ): () => void;

  export function buildExecRemoteCommand(params: {
    command: string;
    workdir?: string;
    env: Record<string, string>;
  }): string;

  export function buildRemoteCommand(argv: string[]): string;
  export function buildSshSandboxArgv(params: {
    session: SshSandboxSession;
    remoteCommand: string;
    tty?: boolean;
  }): string[];

  export function createSshSandboxSessionFromSettings(
    settings: SshSandboxSettings,
  ): Promise<SshSandboxSession>;

  export function createRemoteShellSandboxFsBridge(params: {
    sandbox: SandboxContext;
    runtime: RemoteShellSandboxHandle;
  }): any;

  export function disposeSshSandboxSession(session: SshSandboxSession): Promise<void>;

  export function runSshSandboxCommand(params: {
    session: SshSandboxSession;
    remoteCommand: string;
    stdin?: Buffer | string;
    allowFailure?: boolean;
    signal?: AbortSignal;
    tty?: boolean;
  }): Promise<SandboxBackendCommandResult>;

  export function shellEscape(value: string): string;
}
