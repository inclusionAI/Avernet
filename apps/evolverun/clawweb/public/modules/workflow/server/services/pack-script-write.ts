import { executeGit, withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { TextDecoder } from 'node:util';
import { parse as parseYaml } from 'yaml';

export type PackScriptWriteInput = {
  repoDir: string;
  workflowId: string;
  packId: string;
  baseCommit: string;
  targetBranch: string;
  basePackPath: string;
  changes: Array<{ path: string; content: string | null }>;
  author: { name: string; email: string };
};

// Conservative limits until the caller's API contract defines shared limits.
const MAX_CHANGES = 100;
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const MAX_FILES = 500;
const identifier = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const sha = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

function validText(text: string): boolean {
  return !text.includes('\0') && Buffer.from(text, 'utf8').toString('utf8') === text;
}

/**
 * Isolated Git primitive, not a safe publication path: it protects only targetBranch.
 * The other workflow ref is not compared atomically and may change concurrently.
 * Git and database changes are not one transaction; no reservation, activation,
 * idempotency, or recovery/reconciliation protocol is implemented here.
 * Do not mount this function on save/deploy APIs or treat it as publication until
 * cross-ref concurrency and Git/DB consistency are designed and verified.
 * Uses a private index and never runs pack scripts.
 */
export async function writePackScripts(input: PackScriptWriteInput): Promise<{ commit: string }> {
  const { repoDir, workflowId, packId, baseCommit, targetBranch, basePackPath, changes, author } = input;
  if (!identifier.test(workflowId) || !identifier.test(packId) || !sha.test(baseCommit)
    || ![`wf/${workflowId}`, `saved/${workflowId}`].includes(targetBranch)
    || ![packId, `packs/${packId}`].includes(basePackPath)) throw new Error('Invalid pack, workflow, branch, or base');
  if (!author || !author.name || !author.email || /[\r\n\0<>]/.test(author.name + author.email)) throw new Error('Invalid author');
  if (!Array.isArray(changes) || changes.length === 0 || changes.length > MAX_CHANGES) throw new Error('Invalid change count');
  let total = 0;
  const seen = new Set<string>();
  for (const change of changes) {
    if (!change || typeof change.path !== 'string' || !change.path.startsWith('scripts/')
      || change.path.length > 512 || !validText(change.path) || !/\.(?:py|js|ts|sh|bash)$/.test(change.path) || /[\\\x00-\x1f\x7f]/.test(change.path)
      || change.path.split('/').some((s) => !s || s === '.' || s === '..' || s.toLowerCase() === '.git')
      || seen.has(change.path) || !(change.content === null || typeof change.content === 'string')) throw new Error('Invalid script path or content');
    seen.add(change.path);
    if (change.content !== null) {
      const size = Buffer.byteLength(change.content);
      total += size;
      if (!validText(change.content) || size > MAX_FILE_BYTES || total > MAX_TOTAL_BYTES) throw new Error('Invalid or oversized text');
    }
  }
  return withGitOperation({}, async () => {
    const temp = await mkdtemp(path.join(tmpdir(), 'pack-write-'));
    try {
      const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_'))) as NodeJS.ProcessEnv;
      Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_REPLACE_OBJECTS: '1', GIT_INDEX_FILE: path.join(temp, 'index'), GIT_AUTHOR_NAME: author.name, GIT_AUTHOR_EMAIL: author.email, GIT_COMMITTER_NAME: author.name, GIT_COMMITTER_EMAIL: author.email });
      const git = async (...args: string[]) => (await executeGit(['-c', 'core.hooksPath=/dev/null', ...args], { cwd: repoDir, env, maxBuffer: 16 * 1024 * 1024 })).toString('utf8');
      const format = (await git('rev-parse', '--show-object-format')).trim();
      if ((format === 'sha1' && baseCommit.length !== 40) || (format === 'sha256' && baseCommit.length !== 64) || !['sha1', 'sha256'].includes(format)) throw new Error('Invalid object format');
      const verified = (await git('rev-parse', '--verify', `${baseCommit}^{commit}`)).trim();
      if (verified !== baseCommit) throw new Error('Invalid base commit');
      const fetchUrls = (await git('remote', 'get-url', '--all', 'origin')).trim().split('\n');
      const pushUrls = (await git('remote', 'get-url', '--push', '--all', 'origin')).trim().split('\n');
      if (fetchUrls.length !== 1 || pushUrls.length !== 1 || !fetchUrls[0] || fetchUrls[0] !== pushUrls[0]) throw new Error('Remote read and write endpoints differ');
      const ref = `refs/heads/${targetBranch}`;
      const remote = (await git('ls-remote', '--refs', 'origin', ref)).trim();
      const fields = remote ? remote.split('\n').map((line) => line.split('\t')) : [];
      if (fields.length > 1 || (fields.length && (fields[0][1] !== ref || !sha.test(fields[0][0])))) throw new Error('Cannot determine remote head');
      const expected = fields[0]?.[0] ?? '';
      if (!expected) throw new Error('Target branch does not exist');
      if (expected !== baseCommit) throw new Error('Target branch conflict');
      const entries = new Map<string, { mode: string; type: string; oid: string }>();
      const treeBytes = await executeGit(['ls-tree', '-r', '-t', '-z', baseCommit], { cwd: repoDir, env, maxBuffer: 16 * 1024 * 1024 });
      let raw: string;
      try { raw = decoder.decode(treeBytes); } catch { throw new Error('Tree contains invalid UTF-8 paths'); }
      for (const record of raw.split('\0')) {
        if (!record) continue;
        const match = /^([0-7]{6}) (\w+) ([0-9a-f]+)\t([\s\S]+)$/.exec(record);
        if (!match) throw new Error('Invalid tree');
        entries.set(match[4], { mode: match[1], type: match[2], oid: match[3] });
      }
      if (entries.get(basePackPath)?.type !== 'tree') throw new Error('Pack does not exist');
      for (const [name, entry] of entries) {
        if ((name === basePackPath || name.startsWith(`${basePackPath}/`)) && (entry.mode === '120000' || entry.mode === '160000')) throw new Error('Pack contains symlink or gitlink');
      }
      // Match snapshot validation against the explicit commit, not stale local tracking refs.
      for (const directory of [packId, 'packs', `packs/${packId}`]) {
        const item = entries.get(directory);
        if (item && (item.type !== 'tree' || item.mode !== '040000')) throw new Error('Invalid pack directory');
      }
      const directManifest = entries.get(`${packId}/workflow.pack.yaml`);
      const nestedManifest = entries.get(`packs/${packId}/workflow.pack.yaml`);
      if (directManifest && nestedManifest) throw new Error('Pack manifest conflict');
      const selectedPath = directManifest ? packId : nestedManifest ? `packs/${packId}` : '';
      if (!selectedPath || selectedPath !== basePackPath) throw new Error('Pack manifest path mismatch');
      const readYaml = async (name: string): Promise<Record<string, unknown>> => {
        const entry = entries.get(name);
        if (!entry || entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error('Invalid YAML file');
        const sizeText = (await git('cat-file', '-s', entry.oid)).trim();
        if (!/^\d+$/.test(sizeText) || Number(sizeText) > MAX_FILE_BYTES) throw new Error('Invalid YAML size');
        const bytes = await executeGit(['cat-file', 'blob', entry.oid], { cwd: repoDir, env, maxBuffer: MAX_FILE_BYTES + 1 });
        try {
          if (bytes.includes(0) || bytes.length !== Number(sizeText)) throw new Error();
          const value: unknown = parseYaml(decoder.decode(bytes));
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
          return value as Record<string, unknown>;
        } catch { throw new Error('Invalid YAML content'); }
      };
      const manifest = await readYaml(`${basePackPath}/workflow.pack.yaml`);
      if (manifest.id !== packId || !Array.isArray(manifest.workflows)) throw new Error('Pack manifest identity mismatch');
      const matches = manifest.workflows.filter((item: unknown) => typeof item === 'string' ? item === workflowId
        : Boolean(item && typeof item === 'object' && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId));
      if (matches.length !== 1) throw new Error('Pack manifest workflow mismatch');
      const workflowPath = typeof matches[0] === 'string' ? `workflows/${workflowId}.yaml` : (matches[0] as Record<string, unknown>).file;
      if (typeof workflowPath !== 'string' || !workflowPath || /[\\\0]/.test(workflowPath) || /^[A-Za-z]:/.test(workflowPath)
        || workflowPath.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('Invalid workflow path');
      const workflowParts = workflowPath.split('/');
      for (let i = 1; i < workflowParts.length; i++) {
        const directory = entries.get(`${basePackPath}/${workflowParts.slice(0, i).join('/')}`);
        if (!directory || directory.type !== 'tree' || directory.mode !== '040000') throw new Error('Invalid workflow directory');
      }
      if ((await readYaml(`${basePackPath}/${workflowPath}`)).id !== workflowId) throw new Error('Workflow identity mismatch');
      const scriptPrefix = `${basePackPath}/scripts/`;
      const scriptsDir = entries.get(`${basePackPath}/scripts`);
      if (scriptsDir && (scriptsDir.type !== 'tree' || scriptsDir.mode !== '040000')) throw new Error('Invalid scripts directory');
      const scriptFiles = [...entries].filter(([name, entry]) => name.startsWith(scriptPrefix) && entry.type !== 'tree');
      const finalNames = new Set(scriptFiles.map(([name]) => name));
      for (const change of changes) {
        const name = `${basePackPath}/${change.path}`;
        if (change.content === null) finalNames.delete(name); else finalNames.add(name);
      }
      if (finalNames.size > MAX_FILES) throw new Error('Script file count limit exceeded');
      let finalBytes = 0;
      for (const [name, entry] of scriptFiles) {
        if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode) || name.includes('\\') || !/\.(?:py|js|ts|sh|bash)$/.test(name)) throw new Error('Invalid script file');
        const sizeText = (await git('cat-file', '-s', entry.oid)).trim();
        const size = Number(sizeText);
        if (!/^\d+$/.test(sizeText) || !Number.isSafeInteger(size) || size > MAX_FILE_BYTES) throw new Error('Script size limit exceeded');
        const data = await executeGit(['cat-file', 'blob', entry.oid], { cwd: repoDir, env, maxBuffer: MAX_FILE_BYTES + 1 });
        try { if (data.length !== size || data.includes(0)) throw new Error(); decoder.decode(data); } catch { throw new Error('Existing script is binary or invalid UTF-8'); }
        if (!seen.has(name.slice(basePackPath.length + 1))) finalBytes += size;
      }
      for (const change of changes) if (change.content !== null) finalBytes += Buffer.byteLength(change.content);
      if (finalBytes > MAX_TOTAL_BYTES) throw new Error('Script size limit exceeded');
      await git('read-tree', baseCommit);
      for (const change of changes) {
        const name = `${basePackPath}/${change.path}`;
        const parts = name.split('/');
        for (let i = 1; i < parts.length; i++) {
          const ancestor = entries.get(parts.slice(0, i).join('/'));
          if (ancestor && ancestor.type !== 'tree') throw new Error('Invalid script parent');
        }
        const current = entries.get(name);
        if (current && (current.type !== 'blob' || !['100644', '100755'].includes(current.mode))) throw new Error('Invalid script type');
        if (change.content === null) {
          if (!current) throw new Error('Cannot delete missing script');
          await git('update-index', '--force-remove', '--', name);
        } else {
          const file = path.join(temp, 'blob');
          await writeFile(file, change.content, 'utf8');
          const oid = (await git('hash-object', '-w', '--no-filters', file)).trim();
          if (!sha.test(oid)) throw new Error('Invalid blob');
          await git('update-index', '--add', '--cacheinfo', `${current?.mode ?? '100644'},${oid},${name}`);
        }
      }
      const tree = (await git('write-tree')).trim();
      const commit = (await git('commit-tree', tree, '-p', baseCommit, '-m', `Update scripts for ${workflowId}`)).trim();
      if (!sha.test(commit) || commit.length !== baseCommit.length) throw new Error('Invalid commit');
      // An explicit lease pins the one observed head (or absence) across the entire operation.
      await git('push', '--no-follow-tags', `--force-with-lease=${ref}:${expected}`, 'origin', `${commit}:${ref}`);
      return { commit };
    } finally { await rm(temp, { recursive: true, force: true }); }
  });
}
