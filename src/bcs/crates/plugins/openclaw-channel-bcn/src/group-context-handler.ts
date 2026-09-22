/**
 * Group Context tool handlers — call BCS HTTP API with framework-injected origin.
 *
 * Origin parameters are resolved as follows:
 *   tenant_id  — hardcoded constant ('default')
 *   group_id   — resolveBcsGroupId(), from in-process Map populated by BCS chat.send
 *   session_id — resolveBcsSessionId(), from the same Map
 *   run_id     — resolveActiveRunId(), from the same Map
 *   actor_id   — getActiveBcsClient().botUuid, from the active BCS WebSocket session
 *
 * BCS re-resolves actor_id from the Bearer token (bot_uuid_from_headers) and
 * ignores any actor_id in the request body — this is the security property that
 * makes origin non-forgeable. The LLM only passes business parameters
 * (domain, key, content, query, limit). See also:
 *   specs/2026-09-09-group-context-api/bcn-integration-plan.md
 */

import { getBcsRuntime } from './runtime.js';
import { getActiveBcsClient } from './channel.js';
import {
  resolveActiveRunId,
  resolveBcsGroupId,
  resolveBcsSessionId,
} from './inbound-handler.js';

// ── helpers ────────────────────────────────────────────────────────────────

const TENANT_ID = 'default';

interface BcsApiCallOptions {
  sessionKey: string;
  path: string;
  body: Record<string, unknown>;
}

async function bcsApiCall(opts: BcsApiCallOptions): Promise<unknown> {
  const groupId = resolveBcsGroupId(opts.sessionKey);
  if (!groupId) {
    throw new Error('BCS group context unavailable: no group_id for this session');
  }
  const sessionId = resolveBcsSessionId(opts.sessionKey) ?? undefined;
  const runId = resolveActiveRunId(opts.sessionKey) ?? undefined;

  const activeClient = getActiveBcsClient();
  const bearer = activeClient?.sessionToken ?? process.env.BCN_BOT_TOKEN;
  if (!bearer) {
    throw new Error('BCS group context unavailable: no BCS session token');
  }
  const actorId = activeClient?.botUuid ?? process.env.BCN_BOT_UUID;

  const runtime = getBcsRuntime();
  const config = await runtime.config.loadConfig();
  const rawBcsUrl = (config?.channels?.bcs?.bcsUrl as string) ?? process.env.BCS_URL;
  // Prefer the HTTP base URL env var; otherwise convert the WebSocket URL
  // (ws://host:port/ws/bot) to an HTTP base (http://host:port).
  const bcsUrl = process.env.BCS_API_BASE_URL
    ?? (rawBcsUrl ? rawBcsUrl.replace(/^wss:\/\//, 'https://').replace(/^ws:\/\//, 'http://').replace(/\/ws\/bot\/?$/, '') : undefined);
  if (!bcsUrl) {
    throw new Error('BCS group context unavailable: BCS_URL not configured');
  }

  const url = `${bcsUrl.replace(/\/$/, '')}${opts.path}`;
  console.log('[group-context] origin:', { tenant_id: TENANT_ID, group_id: groupId, session_id: sessionId, run_id: runId, actor_id: actorId, path: opts.path });
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${bearer}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      tenant_id: TENANT_ID,
      group_id: groupId,
      session_id: sessionId,
      run_id: runId,
      ...opts.body,
    }),
  });

  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`BCS group context ${opts.path} failed: HTTP ${resp.status} ${text}`);
  }

  return resp.json();
}

// ── tool schemas ───────────────────────────────────────────────────────────

export const GROUP_CONTEXT_STATUS_TOOL_SCHEMA = {
  name: 'bcs_group_context_status',
  description:
    'Query the group context: active contexts and available policy templates. ' +
    'Call this when joining a group or when you need to discover what shared ' +
    'context is available.',
  parameters: {
    type: 'object' as const,
    properties: {
      domain: {
        type: 'string' as const,
        description: 'Optional domain filter (e.g. "game_rule", "player_state").',
      },
      limit: {
        type: 'number' as const,
        description: 'Max entries to return (default 20).',
      },
    },
  },
};

export const GROUP_CONTEXT_CREATE_TOOL_SCHEMA = {
  name: 'bcs_group_context_create',
  description:
    'Create a new context entry governed by a policy template. ' +
    'Use this to write shared state (game rules, player status, votes, etc.). ' +
    'If an active entry with the same domain+key already exists, use bcs_group_context_update instead.',
  parameters: {
    type: 'object' as const,
    properties: {
      template_id: {
        type: 'string' as const,
        description: 'Policy template ID from bcs_group_context_status (e.g. "tpl_player_word").',
      },
      domain: {
        type: 'string' as const,
        description: 'Domain for the context entry (e.g. "player_word_player_1").',
      },
      key: {
        type: 'string' as const,
        description: 'Key within the domain for version tracking.',
      },
      content: {
        type: 'string' as const,
        description: 'Content to store (free-form text or JSON string).',
      },
    },
    required: ['template_id', 'domain', 'key', 'content'],
  },
};

export const GROUP_CONTEXT_UPDATE_TOOL_SCHEMA = {
  name: 'bcs_group_context_update',
  description:
    'Update the content of an existing context entry. ' +
    'This creates a new version; the old one is superseded but retained for audit. ' +
    'Optionally pass expected_version for compare-and-swap safety.',
  parameters: {
    type: 'object' as const,
    properties: {
      entry_id: {
        type: 'string' as const,
        description: 'ID of the context entry to update.',
      },
      new_content: {
        type: 'string' as const,
        description: 'New content (free-form text or JSON string).',
      },
      expected_version: {
        type: 'number' as const,
        description: 'Optional: only update if current version matches this value.',
      },
    },
    required: ['entry_id', 'new_content'],
  },
};

export const GROUP_CONTEXT_RETRIEVE_TOOL_SCHEMA = {
  name: 'bcs_group_context_retrieve',
  description:
    'Retrieve context entries from the group context. ' +
    'Use this to read shared state. Optionally filter by domain or pass a query for semantic search.',
  parameters: {
    type: 'object' as const,
    properties: {
      domain: {
        type: 'string' as const,
        description: 'Optional domain filter.',
      },
      query: {
        type: 'string' as const,
        description: 'Optional semantic search query.',
      },
      limit: {
        type: 'number' as const,
        description: 'Max entries to return (default 20).',
      },
    },
  },
};

// ── handler functions ──────────────────────────────────────────────────────

export async function handleGroupContextStatus(
  sessionKey: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  return bcsApiCall({
    sessionKey,
    path: '/groupcontext/status',
    body: {
      domain: typeof params.domain === 'string' ? params.domain : undefined,
      limit: typeof params.limit === 'number' ? params.limit : 20,
    },
  });
}

export async function handleGroupContextCreate(
  sessionKey: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  return bcsApiCall({
    sessionKey,
    path: '/groupcontext/createByTemplate',
    body: {
      template_id: params.template_id as string,
      domain: params.domain as string,
      key: params.key as string,
      content: params.content as string,
    },
  });
}

export async function handleGroupContextUpdate(
  sessionKey: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  return bcsApiCall({
    sessionKey,
    path: '/groupcontext/updateContent',
    body: {
      entry_id: params.entry_id as string,
      new_content: params.new_content as string,
      expected_version:
        typeof params.expected_version === 'number' ? params.expected_version : undefined,
    },
  });
}

export async function handleGroupContextRetrieve(
  sessionKey: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  return bcsApiCall({
    sessionKey,
    path: '/groupcontext/retrieve',
    body: {
      domain: typeof params.domain === 'string' ? params.domain : undefined,
      query: typeof params.query === 'string' ? params.query : undefined,
      limit: typeof params.limit === 'number' ? params.limit : 20,
    },
  });
}