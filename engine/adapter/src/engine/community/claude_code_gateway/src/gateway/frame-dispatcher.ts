// Frame dispatcher: parse incoming WS messages and route them to the right
// handler group. This is the only place that knows the full method list.

import { createLogger } from '../debug.js';
import { CRON_METHODS } from '../cron/handlers.js';
import { MCP_METHODS } from '../mcp/handlers.js';
import { SKILLS_METHODS } from '../skills/handlers.js';
import { COMMANDS_METHODS } from '../commands/handlers.js';
import type { CronStore } from '../cron/store.js';
import type { CronScheduler } from '../cron/scheduler.js';
import type { McpStore } from '../mcp/store.js';
import type { SkillsStore } from '../skills/store.js';
import type { CommandsStore } from '../commands/store.js';
import type { SessionStore } from '../store.js';
import type { GatewayFrame } from '../types.js';
import { META_METHODS, type MetaHandlerDeps } from './handlers/meta.js';
import { SESSIONS_METHODS, type SessionsHandlerDeps } from './handlers/sessions.js';
import { CHAT_METHODS, type ChatHandlerDeps } from './handlers/chat.js';
import { INTERACTION_METHODS, type InteractionHandlerDeps } from '../interaction/resolve.js';
import { replayPendingInteractions } from '../interaction/emitters.js';
import type { PendingInteractionRegistry } from '../interaction/registry.js';
import type { SessionRuntimeRegistry } from '../runtime/session-runtime-registry.js';
import type { ConnectionContext } from './connection-context.js';

const log = createLogger('server');

const PROTOCOL = 3;

const SUPPORTED_METHODS = [
  'chat.send', 'chat.abort', 'chat.history', 'chat.inject',
  'interaction.resolve', 'interaction.pending.list', 'mode_transition.resolve',
  'sessions.list', 'sessions.patch', 'sessions.delete', 'sessions.reset',
  'session.new', 'session.attach', 'session.detach', 'session.status',
  'health.claude', 'providers.available', 'providers.list', 'models.list',
  'cron.list', 'cron.get', 'cron.status',
  'cron.add', 'cron.update', 'cron.remove',
  'cron.run', 'cron.runs',
  'mcp.config.list', 'mcp.config.get',
  'mcp.config.create', 'mcp.config.update', 'mcp.config.delete',
  'mcp.tools.list', 'mcp.tools.call',
  'skills.list', 'skills.get',
  'skills.install', 'skills.uninstall', 'skills.update',
  'commands.list', 'commands.get',
];

const SUPPORTED_EVENTS = [
  'connect.challenge', 'tick', 'chat', 'agent',
  'interaction.requested', 'interaction.resolved',
  'mode_transition', 'message', 'content_block',
];

export type DispatcherDeps = {
  sessionStore: SessionStore;
  cronStore: CronStore;
  cronScheduler: CronScheduler;
  mcpStore: McpStore;
  skillsStore: SkillsStore;
  commandsStore: CommandsStore;
  registry: PendingInteractionRegistry;
  runtimeRegistry: SessionRuntimeRegistry;
  meta: MetaHandlerDeps;
  chat: ChatHandlerDeps;
  interaction: InteractionHandlerDeps;
  sessions: SessionsHandlerDeps;
  tickIntervalMs: number;
};

export async function handleFrame(ctx: ConnectionContext, raw: string, deps: DispatcherDeps): Promise<void> {
  const frame = JSON.parse(raw) as GatewayFrame;
  if (frame.type !== 'req') return;
  log.debug('frame:req', { connId: ctx.connId, id: frame.id, method: frame.method });

  if (frame.method === 'connect') {
    ctx.response(frame.id, true, {
      type: 'hello-ok',
      protocol: PROTOCOL,
      server: {
        version: '0.1.0',
        connId: ctx.connId,
      },
      features: {
        methods: SUPPORTED_METHODS,
        events: SUPPORTED_EVENTS,
      },
      snapshot: {
        presence: [],
        health: {},
        stateVersion: { presence: 0, health: 0 },
      },
      policy: { maxPayload: 524288, maxBufferedBytes: 1572864, tickIntervalMs: deps.tickIntervalMs },
      auth: {
        deviceToken: '',
        role: 'operator',
        scopes: [ 'operator.admin', 'operator.approvals' ],
        issuedAtMs: Date.now(),
      },
    });
    ctx.startTicks();
    replayPendingInteractions(ctx, deps.registry);
    return;
  }

  const metaHandler = META_METHODS[frame.method];
  if (metaHandler) return metaHandler(ctx, frame, deps.meta);

  const sessionsHandler = SESSIONS_METHODS[frame.method];
  if (sessionsHandler) return sessionsHandler(ctx, frame, deps.sessions);

  const chatHandler = CHAT_METHODS[frame.method];
  if (chatHandler) return chatHandler(ctx, frame, deps.chat);

  const interactionHandler = INTERACTION_METHODS[frame.method];
  if (interactionHandler) return interactionHandler(ctx, frame, deps.interaction);

  const cronHandler = CRON_METHODS[frame.method];
  if (cronHandler) {
    try {
      const result = await cronHandler(deps.cronStore, deps.cronScheduler, frame.params);
      if (result.ok) {
        ctx.response(frame.id, true, result.payload);
      } else {
        ctx.response(frame.id, false, undefined, result.error);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('cron:handler-threw', { method: frame.method, error: message });
      ctx.response(frame.id, false, undefined, { code: 'INTERNAL_ERROR', message });
    }
    return;
  }

  const mcpHandler = MCP_METHODS[frame.method];
  if (mcpHandler) {
    try {
      const result = await mcpHandler(deps.mcpStore, frame.params);
      if (result.ok) {
        ctx.response(frame.id, true, result.payload);
      } else {
        ctx.response(frame.id, false, undefined, result.error);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('mcp:handler-threw', { method: frame.method, error: message });
      ctx.response(frame.id, false, undefined, { code: 'INTERNAL_ERROR', message });
    }
    return;
  }

  const skillsHandler = SKILLS_METHODS[frame.method];
  if (skillsHandler) {
    try {
      const result = await skillsHandler(deps.skillsStore, frame.params);
      if (result.ok) {
        ctx.response(frame.id, true, result.payload);
      } else {
        ctx.response(frame.id, false, undefined, result.error);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('skills:handler-threw', { method: frame.method, error: message });
      ctx.response(frame.id, false, undefined, { code: 'INTERNAL_ERROR', message });
    }
    return;
  }

  const commandsHandler = COMMANDS_METHODS[frame.method];
  if (commandsHandler) {
    try {
      const result = await commandsHandler(deps.commandsStore, frame.params);
      if (result.ok) {
        ctx.response(frame.id, true, result.payload);
      } else {
        ctx.response(frame.id, false, undefined, result.error);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.error('commands:handler-threw', { method: frame.method, error: message });
      ctx.response(frame.id, false, undefined, { code: 'INTERNAL_ERROR', message });
    }
    return;
  }

  ctx.response(frame.id, false, undefined, { message: `unsupported method: ${frame.method}`, code: 'METHOD_NOT_FOUND' });
}
