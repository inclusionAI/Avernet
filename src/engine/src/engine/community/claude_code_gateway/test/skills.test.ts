import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { SkillsStore, parseFrontmatter } from '../src/skills/store.js';
import {
  handleGet,
  handleInstall,
  handleList,
  handleUninstall,
  handleUpdate,
} from '../src/skills/handlers.js';

async function tmpDir(prefix: string): Promise<string> {
  return fsp.mkdtemp(path.join(os.tmpdir(), `teamclaw-relay-skills-${prefix}-`));
}

async function rmDir(p: string): Promise<void> {
  try { await fsp.rm(p, { recursive: true, force: true }); } catch { /* ignore */ }
}

/** Helper: set up a real skill dir on disk that we can symlink-mount. */
async function makeSkillDir(parent: string, name: string, frontmatter: string): Promise<string> {
  const dir = path.join(parent, name);
  await fsp.mkdir(dir, { recursive: true });
  await fsp.writeFile(path.join(dir, 'SKILL.md'), `---\n${frontmatter}\n---\n\nBody.\n`, 'utf8');
  return dir;
}

describe('parseFrontmatter', () => {
  it('returns empty object when no leading ---', () => {
    assert.deepEqual(parseFrontmatter('hello\nworld'), {});
  });
  it('parses quoted strings and bracketed lists', () => {
    const fm = parseFrontmatter([
      '---',
      'name: "My Skill"',
      'version: 1.2.3',
      'capabilities: [alpha, "beta", gamma]',
      'dependencies: []',
      'description: no quotes here',
      '---',
      'body',
    ].join('\n'));
    assert.equal(fm.name, 'My Skill');
    assert.equal(fm.version, '1.2.3');
    assert.equal(fm.description, 'no quotes here');
    assert.deepEqual(fm.capabilities, [ 'alpha', 'beta', 'gamma' ]);
    assert.deepEqual(fm.dependencies, []);
  });
  it('ignores malformed lines silently', () => {
    const fm = parseFrontmatter('---\nfoo bar baz\nname: ok\n---\n');
    assert.equal(fm.name, 'ok');
    assert.equal(fm.foo, undefined);
  });
});

describe('SkillsStore', () => {
  let root: string;
  let sources: string;

  beforeEach(async () => {
    root = await tmpDir('root');
    sources = await tmpDir('src');
  });
  afterEach(async () => {
    await rmDir(root);
    await rmDir(sources);
  });

  it('list is empty on empty root', () => {
    const s = new SkillsStore({ rootDir: root });
    assert.deepEqual(s.list(), []);
  });

  it('install symlink → get → list round-trips metadata', async () => {
    const src = await makeSkillDir(sources, 'alpha', 'name: Alpha\ndescription: first\nversion: 0.1.0');
    const s = new SkillsStore({ rootDir: root });
    const installed = await s.installSymlink('alpha', src);
    assert.equal(installed.skillId, 'alpha');
    assert.equal(installed.name, 'Alpha');
    assert.equal(installed.description, 'first');
    assert.equal(installed.version, '0.1.0');
    assert.equal(installed.skillType, 'symlink');
    assert.equal(installed.enabled, true);
    assert.equal(installed.status, 'installed');

    const got = s.get('alpha');
    assert.ok(got);
    assert.equal(got.name, 'Alpha');

    const list = s.list();
    assert.equal(list.length, 1);
    assert.equal(list[0].skillId, 'alpha');
  });

  it('installSymlink rejects duplicates and non-dir sources', async () => {
    const src = await makeSkillDir(sources, 'dup', 'name: dup');
    const s = new SkillsStore({ rootDir: root });
    await s.installSymlink('dup', src);
    await assert.rejects(() => s.installSymlink('dup', src), /ALREADY_EXISTS/);
    await assert.rejects(() => s.installSymlink('bad', path.join(sources, 'ghost')), /INVALID_SOURCE/);
    // non-absolute path
    await assert.rejects(() => s.installSymlink('rel', 'relative/path'), /INVALID_SOURCE/);
  });

  it('setEnabled writes .state.json and flips status', async () => {
    const src = await makeSkillDir(sources, 'toggle', 'name: toggle');
    const s = new SkillsStore({ rootDir: root });
    await s.installSymlink('toggle', src);

    const off = await s.setEnabled('toggle', false);
    assert.ok(off);
    assert.equal(off.enabled, false);
    assert.equal(off.status, 'disabled');

    const statePath = path.join(root, '.state.json');
    const state = JSON.parse(await fsp.readFile(statePath, 'utf8'));
    assert.deepEqual(state.disabled, [ 'toggle' ]);

    const on = await s.setEnabled('toggle', true);
    assert.ok(on);
    assert.equal(on.enabled, true);
    assert.equal(on.status, 'installed');
  });

  it('setEnabled returns null for missing skill', async () => {
    const s = new SkillsStore({ rootDir: root });
    assert.equal(await s.setEnabled('ghost', false), null);
  });

  it('uninstall removes symlink and clears disabled state', async () => {
    const src = await makeSkillDir(sources, 'bye', 'name: bye');
    const s = new SkillsStore({ rootDir: root });
    await s.installSymlink('bye', src);
    await s.setEnabled('bye', false);
    assert.equal(await s.uninstall('bye'), true);
    assert.equal(s.get('bye'), null);
    // disabled-state cleared
    const statePath = path.join(root, '.state.json');
    if (fs.existsSync(statePath)) {
      const state = JSON.parse(await fsp.readFile(statePath, 'utf8'));
      assert.deepEqual(state.disabled, []);
    }
    // uninstall on missing is false (no throw)
    assert.equal(await s.uninstall('bye'), false);
  });

  it('uninstall refuses to remove a non-symlink skill dir', async () => {
    const s = new SkillsStore({ rootDir: root });
    // create a REAL directory directly in the root with a SKILL.md
    const real = path.join(root, 'real');
    await fsp.mkdir(real);
    await fsp.writeFile(path.join(real, 'SKILL.md'), '---\nname: real\n---\n', 'utf8');
    await assert.rejects(() => s.uninstall('real'), /NOT_ALLOWED/);
    assert.ok(fs.existsSync(real), 'real dir still present');
  });

  it('updateSymlinkSource rebinds and reads new metadata', async () => {
    const s = new SkillsStore({ rootDir: root });
    const srcA = await makeSkillDir(sources, 'A', 'name: Old');
    const srcB = await makeSkillDir(sources, 'B', 'name: New');
    await s.installSymlink('mover', srcA);
    const out = await s.updateSymlinkSource('mover', srcB);
    assert.equal(out.name, 'New');
    assert.equal(out.source, srcB);
  });

  it('reports broken symlinks as status=error', async () => {
    const s = new SkillsStore({ rootDir: root });
    // Point at a path that won't exist at read time.
    const ghost = path.join(sources, 'ghost');
    await fsp.mkdir(ghost);
    await s.installSymlink('gh', ghost);
    await fsp.rm(ghost, { recursive: true, force: true });
    const got = s.get('gh');
    assert.ok(got);
    assert.equal(got.status, 'error');
  });
});

describe('skills.* handlers', () => {
  let root: string;
  let sources: string;
  let store: SkillsStore;

  beforeEach(async () => {
    root = await tmpDir('root-h');
    sources = await tmpDir('src-h');
    store = new SkillsStore({ rootDir: root });
  });
  afterEach(async () => {
    await rmDir(root);
    await rmDir(sources);
  });

  it('list / get happy path', async () => {
    const src = await makeSkillDir(sources, 'alpha', 'name: Alpha\ndescription: first');
    await store.installSymlink('alpha', src);

    const l = await handleList(store, {});
    assert.equal(l.ok, true);
    if (l.ok) assert.equal(l.payload.skills.length, 1);

    const g = await handleGet(store, { skillId: 'alpha' });
    assert.equal(g.ok, true);
    if (g.ok) assert.equal(g.payload.skill.name, 'Alpha');
  });

  it('get returns NOT_FOUND for unknown skill', async () => {
    const r = await handleGet(store, { skillId: 'ghost' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_FOUND');
  });

  it('get returns INVALID_PARAMS when skillId missing', async () => {
    const r = await handleGet(store, {});
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PARAMS');
  });

  it('install symlink succeeds and reports enabled flag', async () => {
    const src = await makeSkillDir(sources, 'x', 'name: X');
    const r = await handleInstall(store, {
      skillId: 'x',
      skillType: 'symlink',
      source: src,
      enabled: false,
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.skill.skillId, 'x');
      assert.equal(r.payload.skill.enabled, false);
      assert.equal(r.payload.skill.status, 'disabled');
    }
  });

  it('install defaults skillType to symlink and enabled to true', async () => {
    const src = await makeSkillDir(sources, 'd', 'name: D');
    const r = await handleInstall(store, { skillId: 'd', source: src });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.skill.skillType, 'symlink');
      assert.equal(r.payload.skill.enabled, true);
    }
  });

  it('install returns ALREADY_EXISTS on duplicate', async () => {
    const src = await makeSkillDir(sources, 'dup', 'name: dup');
    const r1 = await handleInstall(store, { skillId: 'dup', source: src });
    assert.equal(r1.ok, true);
    const r2 = await handleInstall(store, { skillId: 'dup', source: src });
    assert.equal(r2.ok, false);
    if (!r2.ok) assert.equal(r2.error.code, 'ALREADY_EXISTS');
  });

  it('install returns INVALID_SOURCE for missing source dir', async () => {
    const r = await handleInstall(store, {
      skillId: 'nope',
      source: path.join(sources, 'missing'),
    });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_SOURCE');
  });

  it('install returns INVALID_PARAMS when source missing', async () => {
    const r = await handleInstall(store, { skillId: 'nope' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PARAMS');
  });

  it('install returns NOT_IMPLEMENTED for non-symlink skill types', async () => {
    const r = await handleInstall(store, { skillId: 'pkg', skillType: 'package', source: '/some/pkg' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_IMPLEMENTED');
  });

  it('uninstall returns {removed: true} then {removed: false}', async () => {
    const src = await makeSkillDir(sources, 'g', 'name: g');
    await store.installSymlink('g', src);
    const r1 = await handleUninstall(store, { skillId: 'g' });
    assert.equal(r1.ok, true);
    if (r1.ok) assert.equal(r1.payload.removed, true);
    const r2 = await handleUninstall(store, { skillId: 'g' });
    assert.equal(r2.ok, true);
    if (r2.ok) assert.equal(r2.payload.removed, false);
  });

  it('uninstall returns NOT_ALLOWED for real (non-symlink) dirs', async () => {
    const real = path.join(root, 'real');
    await fsp.mkdir(real);
    await fsp.writeFile(path.join(real, 'SKILL.md'), '---\nname: r\n---\n', 'utf8');
    const r = await handleUninstall(store, { skillId: 'real' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_ALLOWED');
  });

  it('update toggles enabled flag (enable_skill / disable_skill shape)', async () => {
    const src = await makeSkillDir(sources, 't', 'name: t');
    await store.installSymlink('t', src);

    const off = await handleUpdate(store, { skillId: 't', enabled: false });
    assert.equal(off.ok, true);
    if (off.ok) assert.equal(off.payload.skill.enabled, false);

    const on = await handleUpdate(store, { skillId: 't', enabled: true });
    assert.equal(on.ok, true);
    if (on.ok) assert.equal(on.payload.skill.enabled, true);
  });

  it('update with source rebinds symlink', async () => {
    const srcA = await makeSkillDir(sources, 'A', 'name: Old');
    const srcB = await makeSkillDir(sources, 'B', 'name: New');
    await store.installSymlink('mv', srcA);
    const r = await handleUpdate(store, { skillId: 'mv', source: srcB });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.skill.name, 'New');
      assert.equal(r.payload.skill.source, srcB);
    }
  });

  it('update returns NOT_FOUND for unknown skill', async () => {
    const r = await handleUpdate(store, { skillId: 'ghost', enabled: false });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_FOUND');
  });

  it('update returns NOT_IMPLEMENTED when source change targets non-symlink type', async () => {
    const src = await makeSkillDir(sources, 's', 'name: s');
    await store.installSymlink('s', src);
    const r = await handleUpdate(store, { skillId: 's', source: src, skillType: 'package' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_IMPLEMENTED');
  });

  it('update returns INVALID_PARAMS when skillId missing', async () => {
    const r = await handleUpdate(store, { enabled: false });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PARAMS');
  });
});
