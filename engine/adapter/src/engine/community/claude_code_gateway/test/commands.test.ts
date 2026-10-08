import { strict as assert } from 'node:assert';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { CommandsStore } from '../src/commands/store.js';
import { handleGet, handleList } from '../src/commands/handlers.js';

async function tmpDir(prefix: string): Promise<string> {
  return fsp.mkdtemp(path.join(os.tmpdir(), `teamclaw-relay-cmds-${prefix}-`));
}

async function rmDir(p: string): Promise<void> {
  try { await fsp.rm(p, { recursive: true, force: true }); } catch { /* ignore */ }
}

async function writeCommand(
  dir: string,
  name: string,
  body: string,
): Promise<string> {
  await fsp.mkdir(dir, { recursive: true });
  const file = path.join(dir, name);
  await fsp.writeFile(file, body, 'utf8');
  return file;
}

describe('CommandsStore', () => {
  let claudeHome: string;
  let projectCwd: string;

  beforeEach(async () => {
    claudeHome = await tmpDir('home');
    projectCwd = await tmpDir('proj');
  });
  afterEach(async () => {
    await rmDir(claudeHome);
    await rmDir(projectCwd);
  });

  it('list() returns built-in commands when no fs sources exist', () => {
    const store = new CommandsStore({ claudeHome });
    const all = store.list();
    // a few well-known builtins must be present
    const names = new Set(all.map(c => c.name));
    assert.ok(names.has('/clear'));
    assert.ok(names.has('/compact'));
    assert.ok(names.has('/help'));
    assert.ok(names.has('/review'));
    // sorted by name
    const sorted = [ ...all ].sort((a, b) => a.name.localeCompare(b.name));
    assert.deepEqual(all.map(c => c.name), sorted.map(c => c.name));
  });

  it('picks up user-level .md files and surfaces description from frontmatter', async () => {
    await writeCommand(
      path.join(claudeHome, 'commands'),
      'deploy.md',
      [
        '---',
        'description: Deploy to staging',
        'argument-hint: <env>',
        '---',
        '',
        'Please deploy {{env}} now.',
      ].join('\n'),
    );
    const store = new CommandsStore({ claudeHome });
    const cmd = store.get('deploy');
    assert.ok(cmd);
    assert.equal(cmd!.name, '/deploy');
    assert.equal(cmd!.source, 'user');
    assert.equal(cmd!.description, 'Deploy to staging');
    assert.equal(cmd!.argumentHint, '<env>');
    assert.ok(cmd!.filePath?.endsWith('deploy.md'));
  });

  it('project commands override user commands of the same name', async () => {
    await writeCommand(
      path.join(claudeHome, 'commands'),
      'release.md',
      '---\ndescription: user-level release\n---\nbody',
    );
    await writeCommand(
      path.join(projectCwd, '.claude', 'commands'),
      'release.md',
      '---\ndescription: project-level release\n---\nbody',
    );
    const store = new CommandsStore({ claudeHome });
    const cmd = store.get('release', projectCwd);
    assert.ok(cmd);
    assert.equal(cmd!.source, 'project');
    assert.equal(cmd!.description, 'project-level release');
  });

  it('user commands override built-in commands of the same name', async () => {
    await writeCommand(
      path.join(claudeHome, 'commands'),
      'clear.md',
      '---\ndescription: my custom clear\n---\nbody',
    );
    const store = new CommandsStore({ claudeHome });
    const cmd = store.get('clear');
    assert.ok(cmd);
    assert.equal(cmd!.source, 'user');
    assert.equal(cmd!.description, 'my custom clear');
  });

  it('discovers plugin commands under <home>/plugins/<id>/commands', async () => {
    await writeCommand(
      path.join(claudeHome, 'plugins', 'my-plugin', 'commands'),
      'lint.md',
      '---\ndescription: plugin-supplied lint\n---\nbody',
    );
    const store = new CommandsStore({ claudeHome });
    const cmd = store.get('/lint');
    assert.ok(cmd);
    assert.equal(cmd!.source, 'plugin');
    assert.equal(cmd!.pluginId, 'my-plugin');
  });

  it('ignores hidden / non-md files', async () => {
    const dir = path.join(claudeHome, 'commands');
    await writeCommand(dir, '.hidden.md', '---\ndescription: hidden\n---\nbody');
    await writeCommand(dir, 'README.txt', 'not a command');
    const store = new CommandsStore({ claudeHome });
    assert.equal(store.get('/.hidden'), null);
    assert.equal(store.get('/README'), null);
  });

  it('get() accepts both id and full /name', async () => {
    const store = new CommandsStore({ claudeHome });
    const a = store.get('compact');
    const b = store.get('/compact');
    assert.ok(a && b);
    assert.equal(a!.id, 'compact');
    assert.equal(b!.id, 'compact');
  });

  it('get() returns null for unknown command', () => {
    const store = new CommandsStore({ claudeHome });
    assert.equal(store.get('does-not-exist'), null);
  });
});

describe('commands.* handlers', () => {
  let claudeHome: string;
  let projectCwd: string;

  beforeEach(async () => {
    claudeHome = await tmpDir('home');
    projectCwd = await tmpDir('proj');
  });
  afterEach(async () => {
    await rmDir(claudeHome);
    await rmDir(projectCwd);
  });

  it('handleList returns ok with commands array', async () => {
    const store = new CommandsStore({ claudeHome });
    const result = await handleList(store, {});
    assert.equal(result.ok, true);
    if (!result.ok) return;
    assert.ok(Array.isArray(result.payload.commands));
    assert.ok(result.payload.commands.length >= 4);
  });

  it('handleList honors cwd to include project commands', async () => {
    await writeCommand(
      path.join(projectCwd, '.claude', 'commands'),
      'project-only.md',
      '---\ndescription: only here\n---\nbody',
    );
    const store = new CommandsStore({ claudeHome });

    const without = await handleList(store, {});
    assert.equal(without.ok, true);
    if (!without.ok) return;
    assert.ok(!without.payload.commands.some(c => c.id === 'project-only'));

    const withCwd = await handleList(store, { cwd: projectCwd });
    assert.equal(withCwd.ok, true);
    if (!withCwd.ok) return;
    const found = withCwd.payload.commands.find(c => c.id === 'project-only');
    assert.ok(found);
    assert.equal(found!.source, 'project');
  });

  it('handleGet returns NOT_FOUND for unknown command', async () => {
    const store = new CommandsStore({ claudeHome });
    const result = await handleGet(store, { id: 'no-such-thing' });
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, 'NOT_FOUND');
  });

  it('handleGet returns INVALID_PARAMS when neither id nor name supplied', async () => {
    const store = new CommandsStore({ claudeHome });
    const result = await handleGet(store, {});
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, 'INVALID_PARAMS');
  });

  it('handleGet looks up by both id and name', async () => {
    const store = new CommandsStore({ claudeHome });
    const byId = await handleGet(store, { id: 'compact' });
    const byName = await handleGet(store, { name: '/compact' });
    assert.equal(byId.ok, true);
    assert.equal(byName.ok, true);
    if (!byId.ok || !byName.ok) return;
    assert.equal(byId.payload.command.id, 'compact');
    assert.equal(byName.payload.command.id, 'compact');
  });
});
