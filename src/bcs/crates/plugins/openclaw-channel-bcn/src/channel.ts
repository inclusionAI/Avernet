/**
 * BCS Channel Plugin for OpenClaw.
 *
 * Connects to BCS via WebSocket long-connection for multi-bot collaboration.
 */

import {
  createScopedChannelConfigBase,
  createScopedAccountConfigAccessors,
  createScopedDmSecurityResolver,
} from 'openclaw/plugin-sdk/channel-config-helpers';
import { formatAllowFromLowercase } from 'openclaw/plugin-sdk/allow-from';
import { DEFAULT_ACCOUNT_ID } from './api.js';
import { listAccountIds, resolveAccount } from './accounts.js';
import { BcsWsClient, sanitizeBcsUrlForLog } from './bcs-ws-client.js';
import { handleChatSend, handleChatInject, handleChatHistory, handleSessionDelete, abortAllStreams, initAgentEventsSubscription, cleanupAgentEventsSubscription, resolveGroupIdFromSessionKey } from './inbound-handler.js';
import { getBcsRuntime } from './runtime.js';
import { resolveBcsSessionCleanupConfig, startBcsSessionCleanup } from './session-cleanup.js';
import type { ResolvedBcsAccount } from './types.js';

const CHANNEL_ID = 'bcs';

export type BcsAccountResolver = (cfg: any, accountId?: string | null) => ResolvedBcsAccount;

export interface BcsChannelPluginOptions {
  resolveAccount?: BcsAccountResolver;
  shouldSkipConnection?: (ctx: any, account: ResolvedBcsAccount) => boolean | Promise<boolean>;
  onBeforeStartAccount?: (ctx: any, account: ResolvedBcsAccount) => void | Promise<void>;
  onClientConnected?: (ctx: any, account: ResolvedBcsAccount, client: BcsWsClient, dataDir?: string) => void | Promise<void>;
  resolveConnectBotId?: (ctx: any, account: ResolvedBcsAccount) => string | undefined;
}

function waitUntilAbort(signal?: AbortSignal, onAbort?: () => void): Promise<void> {
  return new Promise(resolve => {
    const complete = () => {
      onAbort?.();
      resolve();
    };
    if (!signal) return;
    if (signal.aborted) {
      complete();
      return;
    }
    signal.addEventListener('abort', complete, { once: true });
  });
}

/** Active WS clients per account for status probing. */
const activeClients = new Map<string, BcsWsClient>();

/** Whether agent events subscription has been initialized */
let agentEventsInitialized = false;

/** One cleanup loop per channel process. */
let stopSessionCleanup: (() => void) | null = null;

function startSessionCleanupIfConfigured(
  cfg: Record<string, unknown>,
  dataDir: string | undefined,
  log?: { info?: (...args: unknown[]) => void; warn?: (...args: unknown[]) => void },
): void {
  if (stopSessionCleanup) return;

  const cleanup = resolveBcsSessionCleanupConfig(cfg);
  if (!cleanup.enabled && !cleanup.disabledReason) return;

  stopSessionCleanup = startBcsSessionCleanup({
    loadConfig: async () => {
      const currentCfg = await getBcsRuntime().config.loadConfig();
      return currentCfg as Record<string, unknown>;
    },
    dataDir,
    log,
  });

  if (cleanup.enabled) {
    log?.info?.(
      `[BCS sessionCleanup] started pruneAfterMs=${cleanup.pruneAfterMs} intervalMs=${cleanup.intervalMs}`,
    );
  } else if (cleanup.disabledReason) {
    log?.warn?.(`[BCS sessionCleanup] configured but disabled: ${cleanup.disabledReason}`);
  }
}

function stopSessionCleanupIfIdle(
  log?: { info?: (...args: unknown[]) => void },
): void {
  if (activeClients.size !== 0 || !stopSessionCleanup) return;
  stopSessionCleanup();
  stopSessionCleanup = null;
  log?.info?.('[BCS sessionCleanup] stopped');
}

export function createBcsPlugin(options: BcsChannelPluginOptions = {}) {
  const resolveBcsAccount = options.resolveAccount ?? resolveAccount;

  const bcsConfigAccessors = createScopedAccountConfigAccessors({
    resolveAccount: ({ cfg, accountId }: { cfg: any; accountId?: string | null }) => resolveBcsAccount(cfg, accountId ?? undefined),
    resolveAllowFrom: (account: ResolvedBcsAccount) => account.allowFrom ?? [],
    formatAllowFrom: (allowFrom: any) => formatAllowFromLowercase({ allowFrom }),
    resolveDefaultTo: () => null,
  });

  const bcsConfigBase = createScopedChannelConfigBase({
    sectionKey: CHANNEL_ID,
    listAccountIds: (cfg: any) => listAccountIds(cfg),
    resolveAccount: (cfg: any, accountId?: string | null) => resolveBcsAccount(cfg, accountId),
    defaultAccountId: () => DEFAULT_ACCOUNT_ID,
    clearBaseFields: [
      'bcsUrl',
      'botId',
      'botName',
      'capabilities',
      'heartbeatIntervalMs',
      'reconnectIntervalMs',
      'connectionTimeoutMs',
      'dmPolicy',
      'allowFrom',
    ],
  });

  const resolveBcsDmPolicy = createScopedDmSecurityResolver<ResolvedBcsAccount>({
    channelKey: CHANNEL_ID,
    resolvePolicy: (account: ResolvedBcsAccount) => account.dmPolicy ?? 'open',
    resolveAllowFrom: (account: ResolvedBcsAccount) => account.allowFrom ?? [],
    policyPathSuffix: 'dmPolicy',
    defaultPolicy: 'open',
  });

  return {
    id: CHANNEL_ID,

    meta: {
      id: CHANNEL_ID,
      label: 'BCS',
      selectionLabel: 'BCS (WebSocket)',
      detailLabel: 'BCS Multi-Bot Collaboration',
      docsPath: '/channels/bcs',
      blurb: 'Connect to BCS multi-bot collaboration platform via WebSocket',
      order: 100,
    },

    capabilities: {
      chatTypes: [ 'group' as const ],
      media: true,
      threads: false,
      reactions: false,
      edit: false,
      unsend: false,
      reply: false,
      effects: false,
      blockStreaming: false,
    },

    reload: { configPrefixes: [ `channels.${CHANNEL_ID}` ] },

    config: {
      ...bcsConfigBase,
      ...bcsConfigAccessors,
    },

    security: {
      resolveDmPolicy: resolveBcsDmPolicy,
    },

    outbound: {
      deliveryMode: 'gateway' as const,

      sendText: async ({ to, text, accountId }: any) => {
        const client = activeClients.get(accountId ?? 'default');
        if (!client?.connected) {
          throw new Error('BCS WebSocket not connected');
        }
        // `to` is the OpenClaw session key (e.g. "bcs:BotName"); resolve to actual BCS group UUID
        const bcsGroupId = resolveGroupIdFromSessionKey(to) ?? to;
        // Send as a chat.event frame
        client.sendEvent('chat.event', {
          run_id: `outbound-${Date.now()}`,
          bcs_group_id: bcsGroupId,
          state: 'final',
          message: {
            role: 'assistant',
            content: [{ type: 'text', text }],
            timestamp: Date.now(),
          },
        }, 0);
        return { channel: CHANNEL_ID, messageId: `bcs-${Date.now()}`, chatId: to };
      },
    },

    gateway: {
      startAccount: async (ctx: any) => {
        const { cfg, accountId, log } = ctx;
        const account = resolveBcsAccount(cfg, accountId);

        if (!account.enabled) {
          log?.info?.(`BCS account ${accountId} is disabled, skipping`);
          return waitUntilAbort(ctx.abortSignal);
        }

        if (!account.bcsUrl) {
          log?.warn?.(
            `BCS account ${accountId ?? 'default'} is missing channels.bcs.bcsUrl or BCS_URL, skipping WebSocket connection`,
          );
          return waitUntilAbort(ctx.abortSignal);
        }

        if (await options.shouldSkipConnection?.(ctx, account)) {
          log?.info?.(`BCS account ${accountId ?? 'default'} skipped by runtime hook`);
          return waitUntilAbort(ctx.abortSignal);
        }

        await options.onBeforeStartAccount?.(ctx, account);

        log?.info?.(
          `Starting BCS channel (account: ${accountId}, url: ${sanitizeBcsUrlForLog(account.bcsUrl)}, bot: ${account.botId})`,
        );

        // Initialize agent events subscription once (global)
        if (!agentEventsInitialized) {
          initAgentEventsSubscription(log);
          agentEventsInitialized = true;
        }

        // Get the correct data directory using runtime
        let dataDir: string | undefined;
        let currentCfg: Record<string, unknown> | undefined;
        try {
          const rt = getBcsRuntime();
          currentCfg = await rt.config.loadConfig() as Record<string, unknown>;
          const sessionCfg = currentCfg.session && typeof currentCfg.session === 'object'
            ? currentCfg.session as { store?: unknown }
            : undefined;
          // Resolve store path - this will use the correct profile data dir
          const storePath = rt.channel.session.resolveStorePath(sessionCfg?.store, {
            agentId: 'main',
          });
          // Extract data directory from store path (go up from agents/main/sessions)
          // storePath is like /path/to/profile/agents/main/sessions/sessions.json
          const path = await import('node:path');
          dataDir = path.dirname(path.dirname(path.dirname(path.dirname(storePath))));
          log?.info?.(`[DEBUG] Resolved dataDir from storePath: ${dataDir}`);
        } catch (err) {
          log?.warn?.(`Failed to resolve dataDir from runtime: ${err}`);
        }

        if (currentCfg) {
          startSessionCleanupIfConfigured(currentCfg, dataDir, log);
        }

        const client = new BcsWsClient({
          account,
          dataDir,
          log,
          resolveConnectBotId: () => options.resolveConnectBotId?.(ctx, account),
        });

        // Register request handlers
        client.onRequest('chat.send', req => handleChatSend(req, client, account, log));
        client.onRequest('chat.inject', req => handleChatInject(req, client, account, log, dataDir));
        client.onRequest('chat.history', req => handleChatHistory(req, client, account, log, dataDir));
        client.onRequest('session.delete', req => handleSessionDelete(req, client, account, log, dataDir));

        activeClients.set(accountId ?? 'default', client);

        let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

        function scheduleReconnect() {
          if (ctx.abortSignal?.aborted) return;
          if (reconnectTimer) return;

          log?.info?.(`Reconnecting to BCS in ${account.reconnectIntervalMs}ms...`);
          reconnectTimer = setTimeout(async () => {
            reconnectTimer = null;
            if (ctx.abortSignal?.aborted) return;
            await client.disconnect();
            await connectWithRetry();
          }, account.reconnectIntervalMs);
        }

        // Connect with reconnection logic
        async function connectWithRetry() {
          try {
            await client.connect(null);
            await options.onClientConnected?.(ctx, account, client, dataDir);

            // Set up reconnection on unexpected close
            client.onClose((code, reason) => {
              log?.warn?.(`BCS WebSocket closed: code=${code} reason=${reason}`);
              if (!ctx.abortSignal?.aborted) {
                scheduleReconnect();
              }
            });
          } catch (err) {
            log?.warn?.(`BCS connection failed: ${err instanceof Error ? err.message : err}`);
            scheduleReconnect();
          }
        }

        await connectWithRetry();

        // Keep alive until abort signal fires
        return waitUntilAbort(ctx.abortSignal, () => {
          log?.info?.(`Stopping BCS channel (account: ${accountId})`);
          if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
          }
          abortAllStreams();
          // Fire-and-forget disconnect; stopAccount also handles cleanup
          client.disconnect().catch(err => {
            log?.warn?.(`Error during BCS disconnect: ${err}`);
          });
          activeClients.delete(accountId ?? 'default');

          // Cleanup agent events subscription if no more active clients
          if (activeClients.size === 0) {
            cleanupAgentEventsSubscription();
            agentEventsInitialized = false;
            stopSessionCleanupIfIdle(log);
          }
        });
      },

      stopAccount: async (ctx: any) => {
        const client = activeClients.get(ctx.accountId ?? 'default');
        if (client) {
          await client.disconnect();
          activeClients.delete(ctx.accountId ?? 'default');
        }
        ctx.log?.info?.(`BCS account ${ctx.accountId} stopped`);

        // Cleanup agent events subscription if no more active clients
        if (activeClients.size === 0) {
          cleanupAgentEventsSubscription();
          agentEventsInitialized = false;
          stopSessionCleanupIfIdle(ctx.log);
        }
      },
    },

    status: {
      probeAccount: async ({ account }: { account: any; timeoutMs: number; cfg: any }) => {
        const client = activeClients.get(account?.accountId ?? 'default');
        return {
          connected: client?.connected ?? false,
          sessionToken: client?.sessionToken ?? null,
        };
      },
    },

    directory: {
      self: async () => null,
      listPeers: async () => [],
      listGroups: async () => [],
    },
  };
}

export const bcsPlugin = createBcsPlugin();
