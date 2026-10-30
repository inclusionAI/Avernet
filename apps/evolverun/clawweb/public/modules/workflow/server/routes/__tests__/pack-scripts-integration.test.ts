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
let root = ''
let server: ReturnType<express.Application['listen']> | undefined
const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()

afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()))
  server = undefined
  vi.restoreAllMocks()
  if (root) rmSync(root, { recursive: true, force: true })
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
    writeFileSync(path.join(pack, 'workflows', 'flow.yaml'), 'id: flow\nnodes: []\n')
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
    expect(await response.json()).toEqual({ workflowId: 'flow', packId: 'pack', baseRef: 'origin/wf/flow', baseCommit: commit, files: [
      { path: 'scripts/task.py', content: 'print("ok")\n', mode: '100644' },
    ] })
    // An editor of only one workflow must never receive the full scripts tree of a shared Pack.
    writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n  - id: other\n    file: workflows/other.yaml\n')
    writeFileSync(path.join(pack, 'workflows', 'other.yaml'), 'id: other\nnodes: []\n')
    writeFileSync(path.join(pack, 'scripts', 'other.py'), 'confidential-other-workflow\n')
    git(work, 'add', '.'); git(work, 'commit', '-qm', 'shared pack'); git(work, 'push', bare, 'HEAD:refs/heads/wf/flow')
    const sharedResponse = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/pack-scripts`)
    expect(sharedResponse.status).toBe(409)
    expect(await sharedResponse.text()).not.toContain('confidential-other-workflow')
  })
})
