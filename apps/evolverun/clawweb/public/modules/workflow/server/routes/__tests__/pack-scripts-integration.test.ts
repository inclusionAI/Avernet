// @vitest-environment node
import express from 'express'
import { once } from 'node:events'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { IDatabase } from '@avernet/clawweb-shared/server/db'
import * as settings from '../../config/auto-heal-git-config.js'
import { readPackScriptsFromRemote } from '../../services/pack-script-read-service.js'
import { createPackScriptsRouter } from '../pack-scripts.js'

const gitAuthor = { name: 'Local Fixture', email: 'fixture@example.invalid' }
const versionSpec = { id: 'flow', title: 'Flow', nodes: [{ id: 'step', executor: { type: 'agent' } }] }
const activeDb = (packId: string) => ({ query: async () => [{ workflow_id: 'flow', is_active: 1, pack_id: packId, deploy_number: 1, version: 1, tag_name: 'deploy/flow/#1', action: 'deploy', spec_json: JSON.stringify(versionSpec) }] }) as unknown as IDatabase
let root = ''
let server: ReturnType<express.Application['listen']> | undefined
const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()

afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()))
  server = undefined
  vi.restoreAllMocks()
  if (root) rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  root = ''
})

describe('pack scripts HTTP to local Git contract', () => {
  it.each(['pack', '.'])('returns the authorized snapshot for %s layout without using a client-selected pack', async layout => {
    root = mkdtempSync(path.join(tmpdir(), 'pack-scripts-api-'))
    const work = path.join(root, 'work'), bare = path.join(root, 'remote.git')
    const pack = path.join(work, layout)
    mkdirSync(path.join(pack, 'scripts'), { recursive: true })
    mkdirSync(path.join(pack, 'workflows'))
    git(work, 'init', '-q'); git(work, 'config', 'user.name', gitAuthor.name); git(work, 'config', 'user.email', gitAuthor.email)
    writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n')
    writeFileSync(path.join(pack, 'workflows', 'flow.yaml'), JSON.stringify(versionSpec))
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'print("ok")\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'fixture'); const commit = git(work, 'rev-parse', 'HEAD')
    git(work, 'branch', 'wf/flow'); git(root, 'clone', '-q', '--bare', work, bare)
    const gitConfig = { remoteUrl: bare, username: 'fixture', email: gitAuthor.email, token: '' }
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor })
    const app = express()
    app.use('/api/workflows', createPackScriptsRouter({
      resolvePrincipal: async () => ({ actorId: 'verified-local-fixture' }),
      botPermRepo: { hasEditPermission: async (id, actor) => id === 'flow' && actor === 'verified-local-fixture' },
      workflowSpecRepo: { findByWorkflowId: async id => id === 'flow' ? { workflow_id: id, pack_id: 'pack' } : null },
      authorizePackRead: async (workflowId, packId, actorId) => workflowId === 'flow' && packId === 'pack' && actorId === 'verified-local-fixture',
      readSnapshot: (workflowId, packId) => readPackScriptsFromRemote({ db: {} as IDatabase, gitConfig, gitAuthor, workflowId, packId }),
    }))
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No local address')
    const response = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts?packId=other`)
    expect(response.status).toBe(200)
    expect(response.headers.get('cache-control')).toBe('no-store')
    expect(await response.json()).toEqual({ workflowId: 'flow', packId: 'pack', baseRef: 'origin/wf/flow', baseCommit: commit, skippedFiles: { count: 0, paths: [], truncated: false }, files: [
      { path: 'scripts/task.py', content: 'print("ok")\n', mode: '100644' },
    ] })
    // The injected policy authorizes the entire Pack; shared and residual scripts are included.
    writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n  - id: other\n    file: workflows/other.yaml\n')
    writeFileSync(path.join(pack, 'workflows', 'other.yaml'), 'id: other\nnodes: []\n')
    writeFileSync(path.join(pack, 'scripts', 'other.py'), 'confidential-other-workflow\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'shared pack'); const sharedCommit = git(work, 'rev-parse', 'HEAD'); git(work, 'push', bare, 'HEAD:refs/heads/wf/flow')
    const sharedResponse = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts`)
    expect(sharedResponse.status).toBe(200)
    expect(await sharedResponse.json()).toEqual({ workflowId: 'flow', packId: 'pack', baseRef: 'origin/wf/flow', baseCommit: sharedCommit, skippedFiles: { count: 0, paths: [], truncated: false }, files: [
      { path: 'scripts/other.py', content: 'confidential-other-workflow\n', mode: '100644' },
      { path: 'scripts/task.py', content: 'print("ok")\n', mode: '100644' },
    ] })
  })
  it('selects exact versions on divergent refs and ignores non-script binaries', async () => {
    root = mkdtempSync(path.join(tmpdir(), 'pack-scripts-versions-'))
    const work = path.join(root, 'work'), pack = path.join(work, 'pack'), bare = path.join(root, 'remote.git')
    mkdirSync(path.join(pack, 'scripts'), { recursive: true }); mkdirSync(path.join(pack, 'workflows'))
    git(work, 'init', '-q'); git(work, 'config', 'user.name', gitAuthor.name); git(work, 'config', 'user.email', gitAuthor.email)
    writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n')
    writeFileSync(path.join(pack, 'workflows', 'flow.yaml'), JSON.stringify(versionSpec))
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'base\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'base'); const baseCommit = git(work, 'rev-parse', 'HEAD')
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'current\n')
    writeFileSync(path.join(pack, 'scripts', 'native.so'), Buffer.from([0, 255, 1]))
    writeFileSync(path.join(pack, 'scripts', 'task.py.bak'), 'private backup')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'current'); const current = git(work, 'rev-parse', 'HEAD'); git(work, 'tag', 'deploy/flow/#1')
    git(work, 'checkout', '-q', '--detach', baseCommit)
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'draft\n'); writeFileSync(path.join(pack, 'scripts', 'run.sh'), 'echo draft\n')
    writeFileSync(path.join(pack, 'scripts', 'native.so'), Buffer.from([0, 255, 2]))
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'draft'); const draft = git(work, 'rev-parse', 'HEAD'); git(work, 'branch', 'wf/flow'); git(work, 'branch', 'saved/flow', baseCommit)
    git(root, 'clone', '-q', '--bare', work, bare)
    const gitConfig = { remoteUrl: bare, username: 'fixture', email: gitAuthor.email, token: '' }
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor })
    const app = express()
    app.use('/api/workflows', createPackScriptsRouter({
      resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: { hasEditPermission: async () => true },
      workflowSpecRepo: { findByWorkflowId: async id => ({ workflow_id: id, pack_id: 'pack' }) }, authorizePackRead: async () => true,
      supportsVersionedSnapshots: true, supportsVersionedPackResolution: true,
      readSnapshot: (workflowId, packId, version) => readPackScriptsFromRemote({ db: activeDb('pack'), gitConfig, gitAuthor, workflowId, packId, version }),
    }))
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No local address')
    const url = `http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts`
    for (const [version, sha, ref, files] of [
      ['current', current, 'refs/tags/deploy/flow/#1', [{ path: 'scripts/task.py', content: 'current\n', mode: '100644' }]],
      ['draft', draft, 'origin/wf/flow', [{ path: 'scripts/run.sh', content: 'echo draft\n', mode: '100644' }, { path: 'scripts/task.py', content: 'draft\n', mode: '100644' }]],
    ] as const) {
      const response = await fetch(`${url}?version=${version}&ref=wf/other`)
      expect(response.status).toBe(200); expect(await response.json()).toEqual({ workflowId: 'flow', packId: 'pack', baseRef: ref, baseCommit: sha, files, skippedFiles: { count: version === 'current' ? 2 : 1, paths: version === 'current' ? ['scripts/native.so', 'scripts/task.py.bak'] : ['scripts/native.so'], truncated: false } })
    }
    expect((await fetch(url)).status).toBe(503)
    for (const query of ['version=wf/flow', 'version=', 'version=current&version=draft']) {
      const response = await fetch(`${url}?${query}`); expect(response.status).toBe(400)
      expect((await response.json()).code).toBe('INVALID_SNAPSHOT_VERSION')
    }
    git(bare, 'update-ref', '-d', 'refs/heads/wf/flow')
    expect((await fetch(`${url}?version=current`)).status).toBe(200)
    const missingDraft = await fetch(`${url}?version=draft`)
    expect(missingDraft.status).toBe(404); expect((await missingDraft.json()).code).toBe('SNAPSHOT_NOT_FOUND')
    git(bare, 'update-ref', 'refs/heads/wf/flow', draft); git(bare, 'update-ref', '-d', 'refs/heads/saved/flow')
    expect((await fetch(`${url}?version=draft`)).status).toBe(200)
    expect((await fetch(`${url}?version=current`)).status).toBe(200)
    const legacy = await fetch(url); expect(legacy.status).toBe(503)
    expect(await legacy.text()).not.toContain('private backup')
  })
  it('resolves renamed packs independently within concurrent version snapshots', async () => {
    root = mkdtempSync(path.join(tmpdir(), 'pack-version-renames-'))
    const work = path.join(root, 'work'), bare = path.join(root, 'remote.git'); mkdirSync(work)
    const put = (file: string, content: string | Buffer) => { const target = path.join(work, file); mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, content) }
    git(work, 'init', '-q'); git(work, 'config', 'user.name', gitAuthor.name); git(work, 'config', 'user.email', gitAuthor.email)
    put('old/workflow.pack.yaml', 'id: old\nworkflows: [flow]\n'); put('old/workflows/flow.yaml', JSON.stringify(versionSpec))
    put('old/scripts/legacy.mjs', Buffer.from([0, 255])); put('old/scripts/query.sql', 'secret sql')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'old'); const current = git(work, 'rev-parse', 'HEAD'); git(work, 'tag', 'deploy/flow/#1')
    git(work, 'rm', '-qr', 'old')
    put('packs/new/workflow.pack.yaml', 'id: new\nworkflows: [flow]\n'); put('packs/new/workflows/flow.yaml', JSON.stringify(versionSpec)); put('packs/new/scripts/run.sh', 'echo new\n')
    put('unrelated/workflow.pack.yaml', 'id: unrelated\nworkflows: [other]\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'new'); const draft = git(work, 'rev-parse', 'HEAD'); git(work, 'branch', 'wf/flow'); git(work, 'branch', 'saved/flow', current)
    git(root, 'clone', '-q', '--bare', work, bare)
    const gitConfig = { remoteUrl: bare, username: 'fixture', email: gitAuthor.email, token: '' }
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor })
    const authorizePackRead = vi.fn(async (_flow: string, id: string) => id === 'old' || id === 'new')
    const app = express(); app.use('/api/workflows', createPackScriptsRouter({
      resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: { hasEditPermission: async () => true },
      workflowSpecRepo: { findByWorkflowId: async id => ({ workflow_id: id, pack_id: 'new' }) }, authorizePackRead,
      supportsVersionedSnapshots: true, supportsVersionedPackResolution: true,
      readSnapshot: (workflowId, packId, version) => readPackScriptsFromRemote({ db: activeDb('old'), gitConfig, gitAuthor, workflowId, packId, version }),
    }))
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No local address')
    const url = `http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts`
    const [a, b] = await Promise.all([fetch(`${url}?version=current`), fetch(`${url}?version=draft`)])
    expect(a.status).toBe(200); expect(await a.json()).toEqual({ workflowId: 'flow', packId: 'old', baseRef: 'refs/tags/deploy/flow/#1', baseCommit: current, files: [], skippedFiles: { count: 2, paths: ['scripts/legacy.mjs', 'scripts/query.sql'], truncated: false } })
    expect(b.status).toBe(200); expect(await b.json()).toEqual({ workflowId: 'flow', packId: 'new', baseRef: 'origin/wf/flow', baseCommit: draft, skippedFiles: { count: 0, paths: [], truncated: false }, files: [{ path: 'scripts/run.sh', content: 'echo new\n', mode: '100644' }] })
    expect(authorizePackRead).toHaveBeenCalledWith('flow', 'old', 'editor'); expect(authorizePackRead).toHaveBeenCalledWith('flow', 'new', 'editor')
    // A second matching manifest is ambiguous; unrelated manifests alone are not.
    put('duplicate/workflow.pack.yaml', 'id: duplicate\nworkflows: [flow]\n'); put('duplicate/workflows/flow.yaml', JSON.stringify(versionSpec))
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'duplicate'); git(work, 'push', bare, 'HEAD:refs/heads/wf/flow')
    const conflict = await fetch(`${url}?version=draft`); expect(conflict.status).toBe(409)
    writeFileSync(path.join(work, 'duplicate/workflow.pack.yaml'), 'id: duplicate\nworkflows: [other]\n')
    writeFileSync(path.join(work, 'packs/new/workflow.pack.yaml'), 'id: new\nworkflows: [other]\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'no match'); git(work, 'push', bare, 'HEAD:refs/heads/wf/flow')
    const missing = await fetch(`${url}?version=draft`); expect(missing.status).toBe(404)
  })
  it('reads only the permitted workflow ref even when a client supplies a different ref or workflow', async () => {
    root = mkdtempSync(path.join(tmpdir(), 'pack-scripts-boundary-'))
    const work = path.join(root, 'work'), pack = path.join(work, 'pack')
    mkdirSync(path.join(pack, 'workflows'), { recursive: true })
    mkdirSync(path.join(pack, 'scripts'))
    writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n')
    writeFileSync(path.join(pack, 'workflows', 'flow.yaml'), JSON.stringify(versionSpec))
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'flow version\n')
    const alternate = path.join(work, 'alternate')
    mkdirSync(path.join(alternate, 'workflows'), { recursive: true }); mkdirSync(path.join(alternate, 'scripts'))
    writeFileSync(path.join(alternate, 'workflow.pack.yaml'), 'id: alternate\nworkflows: [flow]\n')
    writeFileSync(path.join(alternate, 'workflows', 'flow.yaml'), JSON.stringify(versionSpec))
    writeFileSync(path.join(alternate, 'scripts', 'alt.py'), 'alternate pack\n')
    git(work, 'init', '-q'); git(work, 'config', 'user.name', gitAuthor.name); git(work, 'config', 'user.email', gitAuthor.email)
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'flow version'); const flowCommit = git(work, 'rev-parse', 'HEAD')
    git(work, 'branch', 'wf/flow'); git(work, 'checkout', '-qb', 'wf/other')
    writeFileSync(path.join(pack, 'scripts', 'task.py'), 'other version secret\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'other version')
    const bare = path.join(root, 'remote.git'); git(root, 'clone', '-q', '--bare', work, bare)
    const gitConfig = { remoteUrl: bare, username: 'fixture', email: gitAuthor.email, token: '' }
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor })
    const readSnapshot = vi.fn((workflowId: string, packId: string) => readPackScriptsFromRemote({ db: {} as IDatabase, gitConfig, gitAuthor, workflowId, packId }))
    let serverPack = 'pack'
    const app = express()
    app.use('/api/workflows', createPackScriptsRouter({
      resolvePrincipal: async () => ({ actorId: 'editor' }),
      botPermRepo: { hasEditPermission: async workflowId => workflowId === 'flow' },
      workflowSpecRepo: { findByWorkflowId: async id => ({ workflow_id: id, pack_id: serverPack }) },
      authorizePackRead: async workflowId => workflowId === 'flow',
      readSnapshot,
    }))
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No local address')
    const base = `http://127.0.0.1:${address.port}/api/workflows`
    const response = await fetch(`${base}/flow/pack-scripts?ref=wf/other&workflowId=other&packId=other`)
    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({ workflowId: 'flow', packId: 'pack', baseCommit: flowCommit,
      baseRef: 'origin/wf/flow', files: [{ path: 'scripts/task.py', content: 'flow version\n' }] })
    expect(readSnapshot).toHaveBeenCalledExactlyOnceWith('flow', 'pack')
    serverPack = 'alternate'
    const changedPack = await fetch(`${base}/flow/pack-scripts`)
    expect(changedPack.status).toBe(200)
    expect(await changedPack.json()).toMatchObject({ workflowId: 'flow', packId: 'alternate', baseCommit: flowCommit,
      files: [{ path: 'scripts/alt.py', content: 'alternate pack\n' }] })
    expect(readSnapshot).toHaveBeenLastCalledWith('flow', 'alternate')
    const forbidden = await fetch(`${base}/other/pack-scripts`)
    expect(forbidden.status).toBe(403)
    expect(await forbidden.text()).not.toContain('other version secret')
    expect(readSnapshot).toHaveBeenCalledTimes(2)
  })

  it('does not expose scripts from a nested Pack over any version selector', async () => {
    root = mkdtempSync(path.join(tmpdir(), 'nested-pack-scripts-api-'))
    const work = path.join(root, 'work'), bare = path.join(root, 'remote.git')
    const put = (file: string, value: string) => { const dest = path.join(work, file); mkdirSync(path.dirname(dest), { recursive: true }); writeFileSync(dest, value) }
    mkdirSync(work); git(work, 'init', '-q'); git(work, 'config', 'user.name', gitAuthor.name); git(work, 'config', 'user.email', gitAuthor.email)
    put('pack/workflow.pack.yaml', 'id: pack\nworkflows: [flow]\n')
    put('pack/workflows/flow.yaml', JSON.stringify(versionSpec))
    put('pack/scripts/shared.py', 'shared normal script\n')
    put('pack/scripts/private/workflow.pack.yaml', 'id: private\nworkflows: [other]\n')
    put('pack/scripts/private/scripts/secret.py', 'do-not-return-private-code\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'fixture'); git(work, 'branch', 'wf/flow'); git(work, 'branch', 'saved/flow'); git(work, 'tag', 'deploy/flow/#1'); git(root, 'clone', '-q', '--bare', work, bare)
    const gitConfig = { remoteUrl: bare, username: 'fixture', email: gitAuthor.email, token: '' }
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor })
    const app = express(); app.use('/api/workflows', createPackScriptsRouter({
      resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: { hasEditPermission: async () => true },
      workflowSpecRepo: { findByWorkflowId: async id => ({ workflow_id: id, pack_id: 'pack' }) }, authorizePackRead: async () => true,
      supportsVersionedSnapshots: true, supportsVersionedPackResolution: true,
      readSnapshot: (workflowId, packId, version) => readPackScriptsFromRemote({ db: activeDb('pack'), gitConfig, gitAuthor, workflowId, packId, version }),
    }))
    server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No local address')
    const url = `http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts`
    for (const query of ['', '?version=current', '?version=draft']) {
      const response = await fetch(url + query)
      expect(response.status).toBe(503); expect(await response.text()).not.toContain('do-not-return-private-code')
    }
  })

})
