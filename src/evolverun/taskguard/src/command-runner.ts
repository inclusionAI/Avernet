export type CommandRunResult = {
  code: number;
  stdout: string;
  stderr: string;
};

export type CommandRunOptions = {
  argv: string[];
  timeoutMs: number;
  cwd?: string;
  env?: NodeJS.ProcessEnv;
};

export type CommandRunner = (options: CommandRunOptions) => Promise<CommandRunResult>;

/**
 * Lazy-load openclaw/plugin-sdk/sandbox — only available when running
 * as an OpenClaw plugin (not in standalone teclaw MCP mode).
 */
let _runPluginCommandWithTimeout: CommandRunner | undefined;

async function loadOpenClawRunner(): Promise<CommandRunner | undefined> {
  if (_runPluginCommandWithTimeout !== undefined) return _runPluginCommandWithTimeout;
  try {
    const mod = await import("openclaw/plugin-sdk/sandbox");
    _runPluginCommandWithTimeout = mod.runPluginCommandWithTimeout;
    return _runPluginCommandWithTimeout;
  } catch {
    _runPluginCommandWithTimeout = undefined;
    return undefined;
  }
}

export const runOpenClawCommand: CommandRunner = async (options) => {
  const runner = await loadOpenClawRunner();
  if (!runner) {
    return {
      code: 1,
      stdout: "",
      stderr: "openclaw/plugin-sdk/sandbox not available (standalone mode)",
    };
  }
  return runner(options);
};