// Tests for claudeSessionFileExists — the on-disk session probe that decides
// whether chat.send starts a new Claude session or resumes an existing one.
//
// Regression: relay used a stable sessionKey-derived UUID as the SDK sessionId.
// When binding.sdkSessionId was never written back (first run failed), chat.send
// always judged "new" and launched the CLI with `--session-id <already-on-disk>`,
// triggering "Session ID already in use" → exit code 1, deadlocking the session.
// claudeSessionFileExists breaks that loop by detecting the on-disk JSONL.

import { strict as assert } from 'node:assert';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { appendToClaudeSessionFile, claudeSessionFileExists } from '../src/claude-session-writer.js';

function makeProjectsDir(): string {
  const dir = path.join(os.tmpdir(), `cc-session-probe-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// Mirror encodeProjectDir for a plain cwd: '/' → '-', '.' → '-'.
function encodePlain(cwd: string): string {
  return cwd.replace(/\//g, '-').replace(/\./g, '-');
}

describe('claudeSessionFileExists', () => {
  let projectsDir: string;
  const prevEnv = process.env.CLAUDE_PROJECTS_DIR;
  const prevRelayConfigDir = process.env.RELAY_CLAUDE_CONFIG_DIR;
  const prevClaudeConfigDir = process.env.CLAUDE_CONFIG_DIR;

  beforeEach(() => {
    projectsDir = makeProjectsDir();
    process.env.CLAUDE_PROJECTS_DIR = projectsDir;
    delete process.env.RELAY_CLAUDE_CONFIG_DIR;
    delete process.env.CLAUDE_CONFIG_DIR;
  });

  afterEach(() => {
    if (prevEnv === undefined) delete process.env.CLAUDE_PROJECTS_DIR;
    else process.env.CLAUDE_PROJECTS_DIR = prevEnv;
    if (prevRelayConfigDir === undefined) delete process.env.RELAY_CLAUDE_CONFIG_DIR;
    else process.env.RELAY_CLAUDE_CONFIG_DIR = prevRelayConfigDir;
    if (prevClaudeConfigDir === undefined) delete process.env.CLAUDE_CONFIG_DIR;
    else process.env.CLAUDE_CONFIG_DIR = prevClaudeConfigDir;
    if (fs.existsSync(projectsDir)) fs.rmSync(projectsDir, { recursive: true, force: true });
  });

  function writeSessionFile(cwd: string, sdkSessionId: string, encode: (c: string) => string) {
    const encoded = encode(cwd);
    const dir = path.join(projectsDir, encoded);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, `${sdkSessionId}.jsonl`), '{"type":"user"}\n', 'utf-8');
  }

  it('returns true when the session JSONL exists on disk', () => {
    const cwd = '/home/user/workspace';
    const sid = '2f193d54-9289-4ff9-b9e8-09bd4faa7f29';
    writeSessionFile(cwd, sid, encodePlain);
    assert.equal(claudeSessionFileExists({ sdkSessionId: sid, cwd }), true);
  });

  it('returns false when the session JSONL does not exist', () => {
    assert.equal(
      claudeSessionFileExists({ sdkSessionId: 'no-such-session', cwd: '/home/user/workspace' }),
      false,
    );
  });

  it('returns false when sdkSessionId is missing', () => {
    assert.equal(claudeSessionFileExists({ sdkSessionId: undefined, cwd: '/home/user/workspace' }), false);
  });

  it('returns false when cwd is missing', () => {
    assert.equal(claudeSessionFileExists({ sdkSessionId: 'abc', cwd: undefined }), false);
  });

  it('uses the special .claude_code path encoding (container path mismatch)', () => {
    // A cwd containing .claude_code is hardcoded to -home-admin--claude-code-workspace,
    // matching the real on-disk path the CLI writes inside the container.
    const cwd = '/home/admin/.claude_code/workspace';
    const sid = 'c0e796e7-e3eb-4190-9a5c-7b65da200516';
    writeSessionFile(cwd, sid, () => '-home-admin--claude-code-workspace');
    assert.equal(claudeSessionFileExists({ sdkSessionId: sid, cwd }), true);
  });

  it('writes and probes inject state in the configured role projects directory', () => {
    const roleConfigDir = path.join(os.tmpdir(), `cc-role-config-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
    const legacyProjectsDir = path.join(os.tmpdir(), `cc-legacy-projects-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
    const cwd = '/home/user/developer-workspace';
    const sid = 'bf9ae70b-44a3-4ed2-a35a-8bd81483f899';
    const marker = 'INJECT-PROBE-role-projects-root';
    const expectedFile = path.join(roleConfigDir, 'projects', encodePlain(cwd), `${sid}.jsonl`);

    fs.mkdirSync(legacyProjectsDir, { recursive: true });
    process.env.RELAY_CLAUDE_CONFIG_DIR = roleConfigDir;
    process.env.CLAUDE_PROJECTS_DIR = legacyProjectsDir;
    try {
      const result = appendToClaudeSessionFile({ sdkSessionId: sid, cwd, message: marker, timestamp: '2026-08-09T04:01:21.772Z' });

      assert.equal(result.filePath, expectedFile);
      assert.equal(fs.existsSync(expectedFile), true);
      assert.equal(fs.readFileSync(expectedFile, 'utf8').includes(marker), true);
      const rows = fs.readFileSync(expectedFile, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
      const injectEntry = rows.find((entry) => entry.type === 'user');
      assert.deepEqual(injectEntry.message, {
        role: 'user',
        content: [{ type: 'text', text: marker }],
      });
      assert.equal(typeof injectEntry.version, 'string');
      assert.equal(typeof injectEntry.entrypoint, 'string');
      assert.equal(typeof injectEntry.gitBranch, 'string');
      assert.equal(typeof injectEntry.permissionMode, 'string');
      assert.equal(typeof injectEntry.promptId, 'string');
      assert.equal('syntheticInject' in injectEntry, false);
      assert.equal(fs.existsSync(path.join(legacyProjectsDir, encodePlain(cwd), `${sid}.jsonl`)), false);
      assert.equal(claudeSessionFileExists({ sdkSessionId: sid, cwd }), true);
    } finally {
      fs.rmSync(roleConfigDir, { recursive: true, force: true });
      fs.rmSync(legacyProjectsDir, { recursive: true, force: true });
    }
  });

  it('returns false (does not throw) when path construction fails', () => {
    // Contract (Review Spec R-02): a probe failure must degrade to false and never
    // abort chat.send. Force the internal path encoding to throw via a cwd whose
    // .includes() blows up, and assert we swallow it and return false.
    const hostileCwd = {
      includes() {
        throw new Error('boom');
      },
    } as unknown as string;
    assert.equal(claudeSessionFileExists({ sdkSessionId: 'abc', cwd: hostileCwd }), false);
  });
});
