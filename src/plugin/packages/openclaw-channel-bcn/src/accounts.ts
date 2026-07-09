/**
 * Account resolution: reads config from channels.bcs,
 * merges per-account overrides, falls back to environment variables.
 */

import type { BcsChannelConfig, ResolvedBcsAccount } from './types.js';

/**
 * Determine default BCS URL based on environment variables.
 * Public builds only use explicit configuration.
 */
export function getDefaultBcsUrl(): string {
  return process.env.BCS_URL?.trim() ?? '';
}

function getChannelConfig(cfg: any): BcsChannelConfig | undefined {
  return cfg?.channels?.bcs;
}

function parseList(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);
}

function parseConfigList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(value => (typeof value === 'string' ? value.trim() : ''))
    .filter(Boolean);
}

/**
 * List all configured account IDs for this channel.
 * Returns ["default"] if there's a base config, plus any named accounts.
 */
export function listAccountIds(cfg: any): string[] {
  const channelCfg = getChannelConfig(cfg);

  const ids = new Set<string>();

  // Always include "default" account — falls back to env vars or hardcoded defaults
  ids.add('default');

  if (channelCfg?.accounts) {
    for (const id of Object.keys(channelCfg.accounts)) {
      ids.add(id);
    }
  }

  return Array.from(ids);
}

/**
 * Resolve a specific account by ID with full defaults applied.
 * Falls back to env vars for the "default" account.
 */
export function resolveAccount(cfg: any, accountId?: string | null): ResolvedBcsAccount {
  const channelCfg = getChannelConfig(cfg) ?? {};
  const id = accountId || 'default';

  const accountOverride = channelCfg.accounts?.[id] ?? {};

  const envUrl = getDefaultBcsUrl();
  const explicitEnvBotId = process.env.BCS_BOT_ID;
  const envBotId = explicitEnvBotId ?? 'openclaw-bot';
  const envBotName = process.env.BCS_BOT_NAME ?? 'OpenClaw Agent';
  const envSummary = process.env.BCS_BOT_SUMMARY ?? 'AI Agent';
  const envDomains = parseList(process.env.BCS_BOT_DOMAINS);
  const envSkills = parseList(process.env.BCS_BOT_SKILLS);
  const envScopes = parseList(process.env.BCS_BOT_SCOPES);

  const baseCaps = channelCfg.capabilities ?? {};
  const overrideCaps = accountOverride.capabilities ?? {};
  const connectBotId = accountOverride.botId ?? channelCfg.botId ?? explicitEnvBotId;

  return {
    accountId: id,
    enabled: accountOverride.enabled ?? channelCfg.enabled ?? true,
    bcsUrl: (accountOverride.bcsUrl ?? channelCfg.bcsUrl ?? envUrl).trim(),
    botId: accountOverride.botId ?? channelCfg.botId ?? envBotId,
    ...(connectBotId ? { connectBotId } : {}),
    botName: accountOverride.botName ?? channelCfg.botName ?? envBotName,
    capabilities: {
      summary: overrideCaps.summary ?? baseCaps.summary ?? envSummary,
      domains: overrideCaps.domains ?? baseCaps.domains ?? envDomains,
      skills: overrideCaps.skills ?? baseCaps.skills ?? envSkills,
      scopes: overrideCaps.scopes ?? baseCaps.scopes ?? envScopes,
    },
    heartbeatIntervalMs:
      accountOverride.heartbeatIntervalMs ?? channelCfg.heartbeatIntervalMs ?? 60_000,
    reconnectIntervalMs:
      accountOverride.reconnectIntervalMs ?? channelCfg.reconnectIntervalMs ?? 5_000,
    connectionTimeoutMs:
        accountOverride.connectionTimeoutMs ?? channelCfg.connectionTimeoutMs ?? 10_000,
    dmPolicy: accountOverride.dmPolicy ?? channelCfg.dmPolicy ?? 'open',
    allowFrom: parseConfigList(accountOverride.allowFrom ?? channelCfg.allowFrom),
  };
}
