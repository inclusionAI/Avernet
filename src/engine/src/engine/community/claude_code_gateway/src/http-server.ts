// Lightweight HTTP request handler for the relay gateway.
//
// Mounted on the same http.Server that backs the WebSocket server so both
// share a single port. Only non-upgrade (regular HTTP) requests arrive here.

import { spawn } from 'node:child_process';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createLogger } from './debug.js';
import type { SessionStore } from './store.js';

const log = createLogger('http');

const MAX_BODY_BYTES = 4096;
const CLONE_TIMEOUT_MS = 300_000; // 5 minutes

// Paths where git clone is allowed to write.
const ALLOWED_PATH_PREFIXES = [ '/workspace/', '/home/admin/' ];

// ── helpers ──────────────────────────────────────────────────────────────────

function json(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('body too large'));
        req.destroy();
      } else {
        chunks.push(chunk);
      }
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}

/**
 * Decode a URL-safe Base64 session ID back to the original string.
 * The frontend encodes session keys with base64 before sending in the URL path.
 */
function decodeSessionId(encoded: string): string {
  // Replace URL-safe base64 characters back to standard
  const standard = encoded.replace(/-/g, '+').replace(/_/g, '/');
  // Add padding if needed
  const padded = standard + '='.repeat((4 - (standard.length % 4)) % 4);
  try {
    return Buffer.from(padded, 'base64').toString('utf-8');
  } catch {
    // If base64 decode fails, return as-is (might be an unencoded session key)
    return encoded;
  }
}

// ── git clone ────────────────────────────────────────────────────────────────

interface CloneRequest {
  url: string;
  target_dir: string;
  branch?: string;
}

function validateCloneRequest(body: unknown): CloneRequest {
  if (!body || typeof body !== 'object') throw new Error('invalid JSON body');
  const b = body as Record<string, unknown>;

  const url = String(b.url ?? '').trim();
  if (!url) throw new Error('missing field: url');
  if (!url.startsWith('https://')) throw new Error('only HTTPS URLs are supported');

  const dangerousChars = [ ';', '|', '&', '$', '`', '\n', '\r' ];
  for (const ch of dangerousChars) {
    if (url.includes(ch)) throw new Error(`url contains forbidden character: '${ch}'`);
  }

  const targetDir = String(b.target_dir ?? '').trim();
  if (!targetDir) throw new Error('missing field: target_dir');
  if (!targetDir.startsWith('/')) throw new Error('target_dir must be an absolute path');
  if (targetDir.includes('..')) throw new Error('target_dir must not contain ".."');

  const allowed = ALLOWED_PATH_PREFIXES.some(p => targetDir.startsWith(p));
  if (!allowed) {
    throw new Error(
      `target_dir must be under one of: ${ALLOWED_PATH_PREFIXES.join(', ')}`,
    );
  }

  const branch = b.branch != null ? String(b.branch).trim() : undefined;

  return { url, target_dir: targetDir, branch: branch || undefined };
}

async function execGitClone(params: CloneRequest): Promise<{ success: boolean; error?: string; path?: string }> {
  const args = [ 'clone' ];
  if (params.branch) args.push('--branch', params.branch);
  args.push(params.url, params.target_dir);

  log.debug('git-clone:start', { args });

  return new Promise(resolve => {
    const proc = spawn('git', args, { stdio: [ 'ignore', 'pipe', 'pipe' ] });
    const stderr: Buffer[] = [];

    proc.stdout.on('data', () => { /* drain */ });
    proc.stderr.on('data', (d: Buffer) => stderr.push(d));

    const timer = setTimeout(() => {
      proc.kill('SIGKILL');
      resolve({ success: false, error: 'git clone timed out' });
    }, CLONE_TIMEOUT_MS);

    proc.on('close', code => {
      clearTimeout(timer);
      if (code === 0) {
        log.debug('git-clone:ok', { path: params.target_dir });
        resolve({ success: true, path: params.target_dir });
      } else {
        const errMsg = Buffer.concat(stderr).toString('utf-8').trim() || `exit code ${code}`;
        log.warn('git-clone:fail', { code, error: errMsg });
        resolve({ success: false, error: errMsg });
      }
    });

    proc.on('error', err => {
      clearTimeout(timer);
      resolve({ success: false, error: err.message });
    });
  });
}

// ── session history ──────────────────────────────────────────────────────────

/**
 * Build the history messages response for a session, mirroring the
 * `handleChatHistory` WebSocket handler format so the frontend can
 * consume HTTP and WS responses interchangeably.
 */
function buildSessionHistoryResponse(store: SessionStore, sessionKey: string, limit: number) {
  const binding = store.findBySessionKey(sessionKey);
  if (!binding) {
    return { success: false, error: 'session not found' };
  }

  const messages = (binding.history ?? []).slice(-Math.max(1, limit)).map(m => {
    // Build content blocks based on role and stored data
    let content: Array<{ type: string; text?: string; toolCallId?: string; toolName?: string; input?: Record<string, unknown> }> | null = null;
    if (m.content) {
      content = m.content;
    } else if (m.role === 'user' || m.role === 'assistant') {
      content = [{ type: 'text' as const, text: m.text }];
    } else if (m.role === 'thinking') {
      const thinkingText = (m.metadata as { text?: string } | undefined)?.text ?? m.text;
      content = [{ type: 'thinking' as const, text: thinkingText }];
    } else if (m.role === 'tool_use') {
      const meta = m.metadata as { toolCallId?: string; toolName?: string; input?: Record<string, unknown> } | undefined;
      content = meta ? [{ type: 'tool_use' as const, toolCallId: meta.toolCallId, toolName: meta.toolName, input: meta.input }] : null;
    } else if (m.role === 'tool_result') {
      content = null;
    }

    return {
      id: m.id,
      role: m.role,
      text: m.text,
      content,
      timestamp: m.timestamp,
      metadata: m.metadata ? { runId: m.runId, ...m.metadata } : { runId: m.runId },
    };
  });

  return { success: true, data: messages };
}

// ── route dispatch ───────────────────────────────────────────────────────────

export type HttpHandlerDeps = {
  store: SessionStore;
};

export async function handleHttpRequest(req: IncomingMessage, res: ServerResponse, deps?: HttpHandlerDeps): Promise<void> {
  const method = req.method ?? 'GET';
  const url = req.url ?? '/';

  // Health check
  if (url === '/health' && method === 'GET') {
    return json(res, 200, { ok: true });
  }

  // POST /api/git/clone
  if (url === '/api/git/clone' && method === 'POST') {
    try {
      const raw = await readBody(req);
      const body = JSON.parse(raw);
      const params = validateCloneRequest(body);
      const result = await execGitClone(params);
      return json(res, result.success ? 200 : 400, result);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log.warn('git-clone:bad-request', { error: message });
      return json(res, 400, { success: false, error: message });
    }
  }

  // Session history and listing endpoints (require store)
  if (deps?.store) {
    // GET /api/sessions/:id/messages — session history
    const messagesMatch = url.match(/^\/api\/sessions\/([^/]+)\/messages$/);
    if (messagesMatch && method === 'GET') {
      const encodedId = messagesMatch[1];
      const sessionKey = decodeSessionId(encodedId);
      const limitParam = new URL(url, 'http://localhost').searchParams.get('limit');
      const limit = Number(limitParam ?? 200);

      log.debug('http:session-messages', { encodedId, sessionKey, limit });

      try {
        const result = buildSessionHistoryResponse(deps.store, sessionKey, limit);
        if (result.success) {
          return json(res, 200, result);
        }
        return json(res, 404, result);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        log.error('http:session-messages:error', { error: message });
        return json(res, 500, { success: false, error: message });
      }
    }

    // GET /api/sessions — list sessions
    const sessionsMatch = url.match(/^\/api\/sessions\/?$/);
    if (sessionsMatch && method === 'GET') {
      log.debug('http:sessions-list');

      try {
        const sessions = deps.store.list().map(binding => {
          const history = binding.history ?? [];
          const preview = [ ...history ].reverse().find(m => m.role === 'assistant' || m.role === 'user')?.text?.slice(0, 160) ?? '';
          return {
            key: binding.gatewaySessionKey,
            label: binding.title || binding.gatewaySessionKey,
            displayName: binding.title || binding.gatewaySessionKey,
            derivedTitle: binding.title || binding.gatewaySessionKey,
            createdAt: binding.createdAt,
            updatedAt: binding.updatedAt,
            model: binding.model,
            permissionMode: binding.permissionMode,
            cwd: binding.cwd,
            additionalDirectories: binding.additionalDirectories,
            preview,
            messageCount: history.length,
            inputTokens: 0,
            outputTokens: 0,
          };
        });
        return json(res, 200, { success: true, data: sessions });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        log.error('http:sessions-list:error', { error: message });
        return json(res, 500, { success: false, error: message });
      }
    }
  }

  // Fallback: 404
  json(res, 404, { error: 'not found' });
}
