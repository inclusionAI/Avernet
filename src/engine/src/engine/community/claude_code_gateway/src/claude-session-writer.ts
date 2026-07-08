/**
 * claude-session-writer.ts — Write inject entries to Claude SDK session JSONL files.
 *
 * Claude Code SDK uses `.claude/projects/<encoded-cwd>/<sessionId>.jsonl` to persist
 * conversation state. Each entry has a `parentUuid` forming a tree structure; entries
 * without `parentUuid` are orphaned and ignored by `--resume`.
 *
 * This module provides `appendToClaudeSessionFile` which:
 * 1. Encodes the cwd path (matching Claude Code's convention)
 * 2. Scans the existing JSONL to find the leaf UUID (for parentUuid)
 * 3. Appends a properly-linked user entry
 */

import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync, appendFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import { createLogger } from './debug.js';

const log = createLogger('server');

const DEFAULT_PROJECTS_DIR = join(homedir(), '.claude', 'projects');

function getProjectsDir(): string {
  return process.env.CLAUDE_PROJECTS_DIR || DEFAULT_PROJECTS_DIR;
}

/**
 * Encode a cwd path the same way Claude Code does:
 * `/Users/helloworld` → `-Users-helloworld`
 * `/path/with.dot` → `-path-with-dot`
 *
 * Special case: `.claude_code` in path → hardcoded `-home-admin--claude-code-workspace`
 * (container path mismatch: binding stores `.claude_code` but CLI uses `.claude-code`)
 */
function encodeProjectDir(cwd: string): string {
  if (cwd.includes('.claude_code')) {
    return '-home-admin--claude-code-workspace';
  }
  return cwd.replace(/\//g, '-').replace(/\./g, '-');
}

/**
 * Scan a JSONL file to find the conversation tree leaf UUID.
 * Priority: last `last-prompt` entry's `leafUuid`, fallback: last entry with `uuid`.
 */
function findLeafUuid(filePath: string): string | null {
  if (!existsSync(filePath)) return null;

  let leafUuid: string | null = null;
  let lastUuid: string | null = null;

  try {
    const content = readFileSync(filePath, 'utf-8');
    for (const line of content.split('\n')) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line);
        if (entry.uuid) lastUuid = entry.uuid;
        if (entry.type === 'last-prompt' && entry.leafUuid) {
          leafUuid = entry.leafUuid;
        }
      } catch {
        // skip malformed lines
      }
    }
  } catch (err) {
    log.warn('findLeafUuid: failed to read', { filePath, error: String(err) });
    return null;
  }

  return leafUuid ?? lastUuid;
}

/**
 * 探测某 sdkSessionId 对应的 Claude session JSONL 是否已在盘上。
 *
 * 用于判定 chat.send 该走 new 还是 resume：relay 用 sessionKey 派生的稳定 UUID
 * 作为 SDK sessionId，若 binding.sdkSessionId 因首轮失败从未回写，仅凭它会永远判
 * "新建" 并用 `--session-id <已存在>` 启动 CLI，触发 `Session ID already in use`
 * 退出（code 1），同一会话永久死锁。落盘探测打破该死循环：文件已存在即应 resume。
 *
 * 路径编码完全复用 `appendToClaudeSessionFile` 的 `encodeProjectDir`/`getProjectsDir`，
 * 确保探测路径与 CLI 真实写入路径一致。缺参或异常一律返回 false（无从判定 / 探测
 * 失败时退化为原 "按新建" 行为，绝不抛错阻断 chat.send）。
 */
export function claudeSessionFileExists(opts: {
  sdkSessionId: string | undefined;
  cwd: string | undefined;
}): boolean {
  const { sdkSessionId, cwd } = opts;

  if (!sdkSessionId || !cwd) {
    log.debug('claudeSessionFileExists: missing sdkSessionId or cwd, treating as not-exist', {
      hasSdkSessionId: Boolean(sdkSessionId),
      hasCwd: Boolean(cwd),
    });
    return false;
  }

  try {
    const filePath = join(getProjectsDir(), encodeProjectDir(cwd), `${sdkSessionId}.jsonl`);
    const exists = existsSync(filePath);
    log.debug('claudeSessionFileExists: probe', { filePath, exists });
    return exists;
  } catch (err) {
    log.warn('claudeSessionFileExists: probe failed, treating as not-exist', {
      sdkSessionId,
      cwd,
      error: String(err),
    });
    return false;
  }
}

export type InjectJSONLResult = {
  written: boolean;
  filePath?: string;
  parentUuid?: string | null;
  reason?: string;
};

/**
 * Append an inject entry to the Claude SDK session JSONL file.
 *
 * Returns `{written: false}` if sdkSessionId or cwd is missing (first chat.send
 * hasn't happened yet — no session file to inject into).
 */
export function appendToClaudeSessionFile(opts: {
  sdkSessionId: string | undefined;
  cwd: string | undefined;
  message: string;
  timestamp: string;
}): InjectJSONLResult {
  const { sdkSessionId, cwd, message, timestamp } = opts;

  if (!sdkSessionId) {
    log.debug('appendToClaudeSessionFile: no sdkSessionId, skipping JSONL write');
    return { written: false, reason: 'no sdkSessionId' };
  }
  if (!cwd) {
    log.debug('appendToClaudeSessionFile: no cwd, skipping JSONL write');
    return { written: false, reason: 'no cwd' };
  }

  const projectsDir = getProjectsDir();
  const encoded = encodeProjectDir(cwd);
  const filePath = join(projectsDir, encoded, `${sdkSessionId}.jsonl`);

  mkdirSync(dirname(filePath), { recursive: true });

  const parentUuid = findLeafUuid(filePath);
  const uuid = randomUUID();

  const entry = {
    parentUuid,
    isSidechain: false,
    type: 'user',
    message: { role: 'user', content: message },
    uuid,
    timestamp,
    sessionId: sdkSessionId,
    cwd,
    userType: 'external',
    syntheticInject: true,
  };

  appendFileSync(filePath, JSON.stringify(entry) + '\n', 'utf-8');

  // 更新 last-prompt 使 inject 成为新的叶子节点。
  // 没有这一步，下次 chat.send --resume 会从旧 leafUuid 出发，
  // inject 和 chat.send 形成分叉，inject 在死分支上永远不被加载。
  const lastPrompt = {
    type: 'last-prompt',
    lastPrompt: message.slice(0, 200),
    leafUuid: uuid,
    sessionId: sdkSessionId,
  };
  appendFileSync(filePath, JSON.stringify(lastPrompt) + '\n', 'utf-8');

  log.debug('appendToClaudeSessionFile: written', { filePath, uuid, parentUuid });
  return { written: true, filePath, parentUuid };
}
