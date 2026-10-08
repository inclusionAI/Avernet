declare module "openclaw/plugin-sdk/plugin-entry" {
  export type PluginApi = {
    runtime: {
      agent: {
        runEmbeddedPiAgent(params: Record<string, unknown>): Promise<{
          output?: string;
          error?: string;
        }>;
      };
      system: {
        runCommandWithTimeout(argv: string[], options?: { timeoutMs?: number }): Promise<unknown>;
      };
      taskFlow: {
        bindSession(params: Record<string, unknown>): unknown;
        fromToolContext(ctx: unknown): unknown;
      };
      /** Optional database access. Undefined on older SDK versions. */
      db?: import("./db/types.js").IDatabase;
    };
    registerCommand(command: {
      name: string;
      description?: string;
      handler(ctx: Record<string, unknown> & {
        args?: string;
        sessionKey: string;
        workspaceDir?: string;
        deliveryContext?: unknown;
      }): unknown;
    }): void;
    registerTool(
      factory: (ctx: Record<string, unknown> & {
        sessionKey?: string;
        workspaceDir?: string;
        deliveryContext?: unknown;
      }) => {
        name: string;
        description?: string;
        parameters?: unknown;
        execute(
          id: string,
          params: unknown,
          signalOrOnUpdate?: unknown,
          onUpdateArg?: unknown,
        ): unknown;
      },
      options?: { name?: string },
    ): void;
    on(event: string, handler: (event: { cleanedBody?: string }, ctx: { sessionKey?: string }) => unknown): void;
  };

  export type PluginEntry = {
    id: string;
    name: string;
    register(api: PluginApi): unknown;
  };

  export function definePluginEntry<T extends PluginEntry>(entry: T): T;
}

declare module "openclaw/plugin-sdk/sandbox" {
  export type CommandRunResult = {
    code: number;
    stdout: string;
    stderr: string;
  };

  export type CommandRunOptions = {
    argv: string[];
    timeoutMs: number;
    cwd?: string;
    env?: Record<string, string | undefined>;
  };

  export function runPluginCommandWithTimeout(options: CommandRunOptions): Promise<CommandRunResult>;
}
