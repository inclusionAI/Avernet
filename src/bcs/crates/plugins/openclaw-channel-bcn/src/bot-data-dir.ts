import * as os from 'node:os';
import * as path from 'node:path';

export interface BotDataDirRuntime {
  config?: {
    loadConfig?: () => Promise<{ session?: { store?: unknown } }> | { session?: { store?: unknown } };
  };
  channel?: {
    session?: {
      resolveStorePath?: (store: unknown, opts: { agentId: string }) => string;
    };
  };
}

export interface BotDataDirLog {
  info?: (...args: unknown[]) => void;
  warn?: (...args: unknown[]) => void;
}

function dataDirFromStorePath(storePath: string): string {
  return path.dirname(path.dirname(path.dirname(path.dirname(storePath))));
}

export async function resolveBotDataDir(
  runtime?: BotDataDirRuntime | null,
  log?: BotDataDirLog,
): Promise<string> {
  if (process.env.BOT_DATA_DIR) {
    log?.info?.(`[BCS] BOT_DATA_DIR already set: ${process.env.BOT_DATA_DIR}`);
    return process.env.BOT_DATA_DIR;
  }

  try {
    const loadConfig = runtime?.config?.loadConfig;
    const resolveStorePath = runtime?.channel?.session?.resolveStorePath;
    if (loadConfig && resolveStorePath) {
      const currentCfg = await loadConfig.call(runtime.config);
      const storePath = resolveStorePath.call(runtime.channel?.session, currentCfg?.session?.store, {
        agentId: 'main',
      });
      if (storePath) {
        const dataDir = dataDirFromStorePath(storePath);
        log?.info?.(`[BCS] Resolved BOT_DATA_DIR from runtime: ${dataDir} (storePath=${storePath})`);
        return dataDir;
      }
    }
  } catch (err) {
    log?.warn?.(`Failed to resolve BOT_DATA_DIR from runtime: ${err instanceof Error ? err.message : err}`);
  }

  if (process.env.OPENCLAW_DATA_DIR) {
    log?.info?.(`[BCS] Resolved BOT_DATA_DIR from OPENCLAW_DATA_DIR: ${process.env.OPENCLAW_DATA_DIR}`);
    return process.env.OPENCLAW_DATA_DIR;
  }

  const fallback = path.join(os.homedir(), '.openclaw');
  log?.info?.(`[BCS] Resolved BOT_DATA_DIR from default fallback: ${fallback}`);
  return fallback;
}

export async function injectBotDataDir(
  runtime?: BotDataDirRuntime | null,
  log?: BotDataDirLog,
): Promise<string> {
  const dataDir = await resolveBotDataDir(runtime, log);
  process.env.BOT_DATA_DIR = dataDir;
  log?.info?.(`[BCS] Injected BOT_DATA_DIR=${dataDir}`);
  return dataDir;
}

export function createBotDataDirInjector(
  runtime?: BotDataDirRuntime | null,
  log?: BotDataDirLog,
): () => Promise<string | undefined> {
  let injected = false;
  let pending: Promise<string> | null = null;

  return async () => {
    if (injected) {
      log?.info?.(`[BCS] Skipping BOT_DATA_DIR injection; already injected once${process.env.BOT_DATA_DIR ? ` (${process.env.BOT_DATA_DIR})` : ''}`);
      return process.env.BOT_DATA_DIR;
    }

    if (!pending) {
      log?.info?.('[BCS] Injecting BOT_DATA_DIR for the first time');
      pending = injectBotDataDir(runtime, log).then(dataDir => {
        injected = true;
        return dataDir;
      }, err => {
        pending = null;
        throw err;
      });
    }

    return pending;
  };
}
