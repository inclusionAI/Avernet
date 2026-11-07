#!/usr/bin/env node
/** Offline, opt-in object-only repair of one confirmed duplicate Pack layout. */
import { spawnSync } from 'node:child_process';
import { lstatSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { TextDecoder } from 'node:util';
import { parseDocument } from 'yaml';

const MAX_DOCUMENT = 1024 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true });
const manifestName = 'workflow.pack.yaml';
const oidPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const fail = message => { throw new Error(message); };
const validId = value => typeof value === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
  && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
const safePath = value => typeof value === 'string' && value.length > 0 && Buffer.byteLength(value) <= 4096
  && !/[\\:\u0000-\u001f\u007f]/.test(value) && value.split('/').every(part => part && part !== '.'
    && part !== '..' && part.toLowerCase() !== '.git' && !/[. ]$/.test(part));
const objectMap = value => value && typeof value === 'object' && !Array.isArray(value);

function options(argv) {
  const valued = new Set(['--repo', '--commit', '--pack-id', '--workflow-id', '--workflow-source', '--workflow-source-file']);
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (Object.hasOwn(result, flag)) fail(`Duplicate option: ${flag}`);
    if (flag === '--write-candidate' || flag === '--help') result[flag] = true;
    else if (valued.has(flag)) {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) fail(`Missing value: ${flag}`);
      result[flag] = argv[++i];
    } else fail('Unknown option');
  }
  if (result['--help']) return result;
  for (const flag of ['--repo', '--commit', '--pack-id', '--workflow-id']) if (!result[flag]) fail(`Required option: ${flag}`);
  if (!validId(result['--pack-id']) || !validId(result['--workflow-id'])) fail('Invalid Pack/workflow ID');
  if (!oidPattern.test(result['--commit'])) fail('Input commit must be a full SHA');
  if (Number(Boolean(result['--workflow-source'])) + Number(Boolean(result['--workflow-source-file'])) !== 1) {
    fail('Choose exactly one explicit workflow source');
  }
  if (result['--workflow-source'] && !['root', 'nested'].includes(result['--workflow-source'])) fail('Invalid workflow source');
  return result;
}

function gitRunner(repo) {
  const identity = new Set(['GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_AUTHOR_DATE',
    'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'GIT_COMMITTER_DATE']);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_') || identity.has(key)));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1',
    GIT_NO_LAZY_FETCH: '1', GIT_OPTIONAL_LOCKS: '0', GIT_TERMINAL_PROMPT: '0' });
  const bytes = (args, input, index) => {
    const result = spawnSync('git', ['-C', repo, '-c', 'protocol.allow=never', '-c', 'core.fsmonitor=false',
      '-c', 'core.splitIndex=false', '-c', 'commit.gpgSign=false', '-c', 'user.useConfigOnly=true', ...args], {
      env: index ? { ...env, GIT_INDEX_FILE: index } : env, input, maxBuffer: 32 * 1024 * 1024,
    });
    if (result.error || result.status !== 0) fail(`Git ${args[0]} failed; check local objects, repository and identity`);
    return result.stdout;
  };
  const text = (args, input, index) => bytes(args, input, index).toString('utf8').trim();
  return { bytes, text };
}

function readTree(git, revision) {
  const data = git.bytes(['ls-tree', '-r', '-t', '-z', '--full-tree', revision]);
  if (data.length && data.at(-1) !== 0) fail('Invalid Git tree');
  const entries = new Map();
  const aliases = new Map();
  for (const record of data.toString('binary').split('\0')) {
    if (!record) continue;
    const raw = Buffer.from(record, 'binary');
    const tab = raw.indexOf(9);
    const header = raw.subarray(0, tab).toString('ascii').match(/^(\d{6}) (blob|tree|commit) ([a-f0-9]{40}|[a-f0-9]{64})$/);
    let name;
    try { name = decoder.decode(raw.subarray(tab + 1)); } catch { fail('Unsafe UTF8 tree path'); }
    if (tab < 0 || !header || !safePath(name) || entries.has(name) || entries.size >= 50000) fail('Unsafe tree path or entry');
    const [, mode, type, oid] = header;
    if (!(type === 'tree' && mode === '040000') && !(type === 'blob' && ['100644', '100755'].includes(mode))) {
      fail('Unsafe tree mode: symlink/gitlink not supported');
    }
    let prefix = '';
    for (const part of name.split('/')) {
      prefix = prefix ? `${prefix}/${part}` : part;
      const key = prefix.normalize('NFC').toLowerCase();
      if (aliases.has(key) && aliases.get(key) !== prefix) fail('Unsafe aliased tree path');
      aliases.set(key, prefix);
    }
    entries.set(name, { mode, type, oid });
  }
  // An index cannot preserve empty trees. Reject rather than silently lose them.
  const nonempty = new Set();
  for (const [name, entry] of entries) if (entry.type === 'blob') {
    const parts = name.split('/');
    for (let i = 1; i < parts.length; i++) nonempty.add(parts.slice(0, i).join('/'));
  }
  for (const [name, entry] of entries) if (entry.type === 'tree' && !nonempty.has(name)) fail('Unsupported empty tree entry');
  return entries;
}

function document(bytes, label) {
  if (bytes.length > MAX_DOCUMENT) fail(`Document too large: ${label}`);
  if (bytes.includes(0)) fail(`Invalid NUL text: ${label}`);
  let content;
  try { content = decoder.decode(bytes); } catch { fail(`Invalid UTF8 text: ${label}`); }
  try {
    const doc = parseDocument(content, { uniqueKeys: true, prettyErrors: false });
    if (doc.errors.length || doc.warnings.length) fail('Invalid YAML');
    const value = doc.toJS({ maxAliasCount: 50 });
    if (!objectMap(value)) fail('Expected YAML object');
    return value;
  } catch { fail(`Invalid YAML object: ${label}`); }
}

function blob(git, entries, name) {
  const entry = entries.get(name);
  if (!entry || entry.type !== 'blob') fail(`Missing workflow or manifest file: ${name}`);
  const size = git.text(['cat-file', '-s', entry.oid]);
  if (!/^\d+$/.test(size) || Number(size) > MAX_DOCUMENT) fail(`Document too large: ${name}`);
  return git.bytes(['cat-file', 'blob', entry.oid]);
}

function workflowPath(manifest, packId, workflowId) {
  if (manifest.id !== packId) fail('Manifest Pack identity mismatch');
  if (!Array.isArray(manifest.workflows) || manifest.workflows.length !== 1) fail('Manifest must contain exactly one workflow');
  const selected = manifest.workflows[0];
  const id = typeof selected === 'string' ? selected : objectMap(selected) ? selected.id : undefined;
  if (id !== workflowId) fail('Manifest workflow identity mismatch');
  const file = typeof selected === 'string' ? `workflows/${id}.yaml` : selected.file;
  if (!safePath(file) || file.split('/').some(part => part.toLowerCase() === manifestName)
    || file === 'scripts' || file.startsWith('scripts/')) fail('Unsafe workflow path');
  return file;
}

function externalSource(file) {
  const name = path.resolve(file);
  let stat;
  try { stat = lstatSync(name); } catch { fail('Cannot read local source file'); }
  if (!stat.isFile() || stat.isSymbolicLink()) fail('Source must be a regular file, not a symlink');
  if (stat.size > MAX_DOCUMENT) fail('Source file too large');
  const extension = path.extname(name).toLowerCase();
  if (!['.yaml', '.yml', '.json'].includes(extension)) fail('Source file extension must be YAML or JSON');
  let bytes;
  try { bytes = readFileSync(name); } catch { fail('Cannot read local source file'); }
  if (extension === '.json') {
    try {
      const value = JSON.parse(decoder.decode(bytes));
      if (!objectMap(value)) fail('Expected JSON object');
    } catch { fail('Invalid JSON source object'); }
  }
  // JSON is also YAML: keep its bytes instead of rounding numbers by reserializing.
  return { kind: extension === '.json' ? 'json-file' : 'yaml-file', path: name, bytes };
}

function verifyTree(git, revision, before, expected, allowed) {
  const actual = readTree(git, revision);
  const actualFiles = new Map([...actual].filter(([, entry]) => entry.type === 'blob'));
  if (actualFiles.size !== expected.size) fail('Candidate tree file count changed unexpectedly');
  for (const [name, entry] of expected) {
    const got = actualFiles.get(name);
    if (!got || got.oid !== entry.oid || got.mode !== entry.mode || got.type !== entry.type) fail(`Unexpected candidate tree change: ${name}`);
  }
  for (const [name, entry] of before) {
    if (entry.type !== 'tree' || allowed.some(file => file.startsWith(`${name}/`))) continue;
    const got = actual.get(name);
    if (!got || got.oid !== entry.oid || got.mode !== entry.mode) fail(`Unexpected candidate tree entry: ${name}`);
  }
}

function repair(input) {
  const repo = path.resolve(input['--repo']);
  const parent = input['--commit'];
  const packId = input['--pack-id'];
  const workflowId = input['--workflow-id'];
  const git = gitRunner(repo);
  const format = git.text(['rev-parse', '--show-object-format']);
  if (!['sha1', 'sha256'].includes(format) || parent.length !== (format === 'sha1' ? 40 : 64)) fail('Input commit SHA format mismatch');
  try {
    if (git.text(['cat-file', '-t', parent]) !== 'commit'
      || git.text(['rev-parse', '--verify', `${parent}^{commit}`]) !== parent) fail('Invalid commit');
  } catch { fail('Input SHA must name an existing local commit'); }
  const entries = readTree(git, parent);
  const nestedBase = `packs/${packId}/`;
  const nestedManifest = nestedBase + manifestName;
  const manifests = [...entries.keys()].filter(name => name.split('/').at(-1).toLowerCase() === manifestName);
  if (manifests.length !== 2 || !manifests.includes(manifestName) || !manifests.includes(nestedManifest)) fail('Pack identity/layout mismatch: expected strict root/nested duplicate manifests');
  const rootManifestBytes = blob(git, entries, manifestName);
  const nestedManifestBytes = blob(git, entries, nestedManifest);
  const rootPath = workflowPath(document(rootManifestBytes, manifestName), packId, workflowId);
  const nestedRelative = workflowPath(document(nestedManifestBytes, nestedManifest), packId, workflowId);
  if (rootPath === 'packs' || rootPath.startsWith('packs/')) fail('Unsafe root workflow path collides with nested layout');
  const nestedPath = nestedBase + nestedRelative;
  const allowedNested = [nestedManifest, nestedPath];
  for (const [name, entry] of entries) if (name.startsWith(nestedBase)) {
    if (entry.type === 'blob' ? !allowedNested.includes(name) : !allowedNested.some(file => file.startsWith(`${name}/`))) {
      fail(`Extra nested resource: ${name}`);
    }
  }
  const rootBytes = blob(git, entries, rootPath);
  const nestedBytes = blob(git, entries, nestedPath);
  for (const [name, bytes] of [[rootPath, rootBytes], [nestedPath, nestedBytes]]) {
    if (document(bytes, name).id !== workflowId) fail('Workflow YAML identity mismatch');
  }
  if (rootBytes.equals(nestedBytes)) fail('Duplicate workflow YAML files are identical; not this repair layout');
  const source = input['--workflow-source-file'] ? externalSource(input['--workflow-source-file'])
    : input['--workflow-source'] === 'root' ? { kind: 'root', path: rootPath, bytes: rootBytes }
      : { kind: 'nested', path: nestedPath, bytes: nestedBytes };
  if (document(source.bytes, 'selected source').id !== workflowId) fail('Selected source workflow identity mismatch');
  const sourceOid = git.text(['hash-object', '--no-filters', '--stdin'], source.bytes);
  if (!oidPattern.test(sourceOid)) fail('Invalid selected source OID');
  const files = new Map([...entries].filter(([, entry]) => entry.type === 'blob'));
  const expected = new Map(files);
  const destination = { ...entries.get(rootPath), oid: sourceOid };
  expected.set(rootPath, destination);
  for (const name of allowedNested) expected.delete(name);
  const replaced = sourceOid !== entries.get(rootPath).oid;
  const changes = allowedNested.map(name => ({ path: name, operation: 'delete', before: entries.get(name) }));
  if (replaced) changes.unshift({ path: rootPath, operation: 'replace', before: entries.get(rootPath), after: destination });
  const report = {
    mode: input['--write-candidate'] ? 'candidate' : 'dry-run', repository: repo, parent, packId, workflowId,
    source: { kind: source.kind, path: source.path, oid: sourceOid, bytes: source.bytes.length },
    recovery: { commit: parent, root: { path: rootPath, ...entries.get(rootPath) }, nested: { path: nestedPath, ...entries.get(nestedPath) } },
    manifest: { path: manifestName, ...entries.get(manifestName) }, changes,
    counts: { beforeFiles: files.size, afterFiles: expected.size, removedFiles: 2,
      replacedFiles: Number(replaced), preservedFiles: files.size - 2 - Number(replaced) },
  };
  if (!input['--write-candidate']) return report;
  // Ensure Git has an explicitly configured identity before writing objects.
  git.text(['var', 'GIT_AUTHOR_IDENT']);
  git.text(['var', 'GIT_COMMITTER_IDENT']);
  const temporary = mkdtempSync(path.join(tmpdir(), 'repair-pack-index-'));
  const index = path.join(temporary, 'index');
  try {
    git.text(['read-tree', parent], undefined, index);
    if (git.text(['hash-object', '-w', '--no-filters', '--stdin'], source.bytes, index) !== sourceOid) fail('Selected source OID changed');
    const zero = '0'.repeat(parent.length);
    const records = allowedNested.map(name => `0 ${zero}\t${name}\0`).join('')
      + `${destination.mode} ${sourceOid}\t${rootPath}\0`;
    git.text(['update-index', '-z', '--index-info'], Buffer.from(records), index);
    const tree = git.text(['write-tree'], undefined, index);
    verifyTree(git, tree, entries, expected, [rootPath, ...allowedNested]);
    const candidate = git.text(['commit-tree', tree, '-p', parent, '--no-gpg-sign', '-m', `Repair duplicate Pack layout for ${workflowId}`], undefined, index);
    if (!oidPattern.test(candidate)) fail('Invalid candidate commit');
    const header = git.bytes(['cat-file', 'commit', candidate]).toString('utf8').split('\n\n')[0].split('\n');
    if (header.filter(line => line.startsWith('parent ')).join('\n') !== `parent ${parent}` || header[0] !== `tree ${tree}`) fail('Candidate parent/tree mismatch');
    verifyTree(git, candidate, entries, expected, [rootPath, ...allowedNested]);
    return { ...report, candidateTree: tree, candidateCommit: candidate };
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}

try {
  const input = options(process.argv.slice(2));
  if (input['--help']) console.log('Usage: repair-pack-layout.mjs --repo PATH --commit FULL_SHA --pack-id ID --workflow-id ID\n  (--workflow-source root|nested | --workflow-source-file FILE.yaml|FILE.json) [--write-candidate]\nDefault: dry-run metadata only; candidate writes objects, never refs/index/worktree/DB/network.');
  else console.log(JSON.stringify(repair(input), null, 2));
} catch (error) {
  // Parser/Git diagnostics may contain private workflow text; never relay them.
  console.error(`Error: ${error instanceof Error ? error.message : 'Pack repair failed'}`);
  process.exitCode = 1;
}
