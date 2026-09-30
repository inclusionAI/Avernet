import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { resolveAccount } from './accounts.js';
import { injectBotDataDir, type BotDataDirLog, type BotDataDirRuntime } from './bot-data-dir.js';
import type { ResolvedBcsAccount } from './types.js';

const DUMMY_SERVICE_BOT_TOKEN = 'dummy';

export interface ServiceBotSessionInfo {
  bot_uuid?: string;
  token: string;
  bcs_url: string;
}

export interface ServiceBotCredentials {
  botType: string;
  botId: string;
  ownerId: string;
  entityId: string;
  botUuid: string;
}

export interface EnsureServiceBotSessionOptions {
  runtime?: BotDataDirRuntime | null;
  cfg?: any;
  accountId?: string | null;
  log?: BotDataDirLog;
  credentialsPath?: string;
  dataDir?: string;
  resolveAccount?: (cfg: any, accountId?: string | null) => ResolvedBcsAccount;
}

export interface EnsureServiceBotSessionResult {
  created: boolean;
  reason: 'created' | 'not_service_bot' | 'session_exists';
  sessionPath?: string;
  session?: ServiceBotSessionInfo;
}

function defaultCredentialsPath(): string {
  return path.join(os.homedir(), '.credentials');
}

function shouldIgnoreServiceBotCredentials(): boolean {
  return process.env.BCS_IGNORE_CREDENTIALS === '1';
}

function parseCredentialsKeyValues(content: string): Record<string, string> {
  const values: Record<string, string> = {};

  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;

    values[trimmed.slice(0, eqIndex).trim()] = trimmed.slice(eqIndex + 1).trim();
  }

  return values;
}

export function parseServiceBotCredentials(content: string): ServiceBotCredentials {
  const values = parseCredentialsKeyValues(content);
  const botId = values.BOT_ID ?? '';
  const ownerId = values.OWNER_ID ?? '';
  const entityId = values.ENTITY_ID ?? '';
  const owner = ownerId || entityId;

  return {
    botType: values.BOT_TYPE ?? '',
    botId,
    ownerId,
    entityId,
    botUuid: botId && owner ? `${botId}:${owner}` : '',
  };
}

export function loadServiceBotCredentials(
  credentialsPath = defaultCredentialsPath(),
  log?: BotDataDirLog,
): ServiceBotCredentials | null {
  try {
    if (!fs.existsSync(credentialsPath)) {
      log?.info?.(`[BCS] No credentials file found at ${credentialsPath}`);
      return null;
    }

    return parseServiceBotCredentials(fs.readFileSync(credentialsPath, 'utf-8'));
  } catch (err) {
    log?.warn?.(`[BCS] Failed to read service bot credentials: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

export function resolveServiceBotCredentialsBotId(
  credentialsPath = defaultCredentialsPath(),
  log?: BotDataDirLog,
): string | undefined {
  if (shouldIgnoreServiceBotCredentials()) return undefined;

  const credentials = loadServiceBotCredentials(credentialsPath, log);
  return credentials?.botUuid || undefined;
}

export function isServiceBot(credentialsPath = defaultCredentialsPath(), log?: BotDataDirLog): boolean {
  if (shouldIgnoreServiceBotCredentials()) return false;

  return (loadServiceBotCredentials(credentialsPath, log)?.botType || process.env.BOT_TYPE) === 'service';
}

export async function ensureServiceBotSession(
  opts: EnsureServiceBotSessionOptions = {},
): Promise<EnsureServiceBotSessionResult> {
  const log = opts.log;
  if (shouldIgnoreServiceBotCredentials()) {
    log?.info?.('[BCS] Service bot session bootstrap skipped; BCS credentials are ignored');
    return { created: false, reason: 'not_service_bot' };
  }

  const credentialsPath = opts.credentialsPath ?? defaultCredentialsPath();
  const credentials = loadServiceBotCredentials(credentialsPath, log);
  const botType = credentials?.botType || process.env.BOT_TYPE || '';

  if (botType !== 'service') {
    log?.info?.('[BCS] Service bot session bootstrap skipped; BOT_TYPE is not service');
    return { created: false, reason: 'not_service_bot' };
  }

  const dataDir = opts.dataDir ?? await injectBotDataDir(opts.runtime, log);
  const sessionPath = path.join(dataDir, '.bcs', 'session.json');

  if (fs.existsSync(sessionPath)) {
    log?.info?.(`[BCS] Service bot session already exists: ${sessionPath}`);
    return { created: false, reason: 'session_exists', sessionPath };
  }

  const resolveBcsAccount = opts.resolveAccount ?? resolveAccount;
  const account = resolveBcsAccount(opts.cfg ?? {}, opts.accountId);
  const session: ServiceBotSessionInfo = {
    ...(credentials?.botUuid ? { bot_uuid: credentials.botUuid } : {}),
    token: DUMMY_SERVICE_BOT_TOKEN,
    bcs_url: account.bcsUrl,
  };

  fs.mkdirSync(path.dirname(sessionPath), { recursive: true });
  fs.writeFileSync(sessionPath, JSON.stringify(session, null, 2), 'utf-8');
  log?.info?.(`[BCS] Created service bot session at ${sessionPath} (${credentials?.botUuid ? `bot_uuid=${credentials.botUuid}, ` : ''}bcs_url=${account.bcsUrl})`);

  return {
    created: true,
    reason: 'created',
    sessionPath,
    session,
  };
}
