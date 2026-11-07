// @vitest-environment node
import express from 'express'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database'
import { createAutoHealConfigTestDb } from '../../../server/config/__tests__/auto-heal-git-fixtures'
import { WorkflowSpecRepository } from '../../../server/repositories/workflow-spec-repository'
import { createPackVersionRouter } from '../../../server/routes/pack-version'
import { candidateProjection } from '../../../server/services/pack-operation-service'
import { readPackScriptSnapshot } from '../../../server/services/pack-script-snapshot'
import { createLocalRemote, runGit } from '../../../server/services/__tests__/clawflows-git-client-fixture'

const document = { id: 'flow', title: '中文工作流', content: '用户正文', custom: { keep: false }, nodes: [
  { id: 'start', title: '开始', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: '结束', executor: { type: 'end' }, dependsOn: ['start'] },
] }
const yaml = stringify(document)
const historyJson = candidateProjection('flow', yaml, '{}').specJson
let root = ''
let db: SqliteDatabase | undefined
let server: ReturnType<express.Application['listen']> | undefined

afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()))
  server = undefined
  if (db) await db.close()
  db = undefined
  if (root) rmSync(root, { recursive: true, force: true })
  root = ''
  vi.restoreAllMocks()
})

async function fixture() {
  root = mkdtempSync(path.join(tmpdir(), 'editor-pack-boundary-'))
  createLocalRemote(root)
  const repo = path.join(root, 'seed')
  const put = (file: string, data: string) => {
    const target = path.join(repo, file)
    mkdirSync(path.dirname(target), { recursive: true })
    writeFileSync(target, data)
  }
  put('packs/pack/workflow.pack.yaml', 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n')
  put('packs/pack/workflows/flow.yaml', yaml)
  put('packs/pack/scripts/run.sh', 'original\n')
  runGit(repo, ['add', '.'])
  runGit(repo, ['commit', '-m', 'deployed'])
  const release = runGit(repo, ['rev-parse', 'HEAD'])
  runGit(repo, ['tag', 'deploy/flow/#3', release])
  runGit(repo, ['update-ref', 'refs/pack-version/active', 'refs/tags/deploy/flow/#3'])
  runGit(repo, ['update-ref', 'refs/remotes/origin/wf/flow', release])
  db = createAutoHealConfigTestDb()
  await db.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
    title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, pack_id TEXT, deploy_number INTEGER, version INTEGER, is_active INTEGER, spec_json TEXT);`)
  await db.exec('INSERT INTO workflow_deploy_history VALUES (?, ?, ?, ?, ?, ?)', ['flow', 'pack', 3, 7, 1, historyJson])
  const readSnapshot = vi.fn((workflowId: string, packId: string, version: 'current' | 'draft') =>
    readPackScriptSnapshot(repo, workflowId, packId, version, { includeVersionFiles: true, activeTag: 'deploy/flow/#3' }))
  const hasEditPermission = vi.fn(async () => true)
  const app = express()
  app.use('/api/workflows', createPackVersionRouter({
    resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: { hasEditPermission },
    workflowSpecRepo: new WorkflowSpecRepository(db), readSnapshot,
  }))
  server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('server address unavailable')
  return { repo, put, release, database: db, readSnapshot, hasEditPermission,
    url: `http://127.0.0.1:${address.port}/api/workflows/flow/pack-version?version=current` }
}

it('returns 404 before reading an existing Git snapshot when the active workflow has no draft row', async () => {
  const f = await fixture()
  const gitSnapshot = await readPackScriptSnapshot(f.repo, 'flow', 'pack', 'current', { includeVersionFiles: true, activeTag: 'deploy/flow/#3' })
  expect(gitSnapshot.baseCommit).toBe(f.release)
  const workflow = gitSnapshot.versionFiles!.find(file => file.path === 'workflows/flow.yaml')!
  expect(Buffer.from(workflow.contentBase64, 'base64').toString('utf8')).toBe(JSON.parse(historyJson).content)
  expect(await f.database.query('SELECT is_active FROM workflow_deploy_history')).toEqual([{ is_active: 1 }])
  const missing = await fetch(f.url)
  expect(missing.status).toBe(404)
  expect(await missing.json()).toMatchObject({ code: 'WORKFLOW_NOT_FOUND' })
  expect(f.readSnapshot).not.toHaveBeenCalled()
  await f.database.exec('INSERT INTO workflow_specs (workflow_id, pack_id, spec_json) VALUES (?, ?, ?)', ['flow', 'pack', JSON.stringify(document)])
  const present = await fetch(f.url)
  expect(present.status).toBe(200)
  expect((await present.json()).baseCommit).toBe(f.release)
  expect(f.readSnapshot).toHaveBeenCalledTimes(1)
  f.hasEditPermission.mockResolvedValue(false)
  expect((await fetch(f.url)).status).toBe(403)
  expect(f.readSnapshot).toHaveBeenCalledTimes(1)
})

it('reads the active tag rather than a changed wf tip even with identical workflow YAML', async () => {
  const f = await fixture()
  f.put('packs/pack/scripts/run.sh', 'changed after deployment\n')
  runGit(f.repo, ['add', '.'])
  runGit(f.repo, ['commit', '-m', 'manual wf change'])
  const current = runGit(f.repo, ['rev-parse', 'HEAD'])
  runGit(f.repo, ['update-ref', 'refs/remotes/origin/wf/flow', current])
  const snapshot = await readPackScriptSnapshot(f.repo, 'flow', 'pack', 'current', { includeVersionFiles: true, activeTag: 'deploy/flow/#3' })
  expect(snapshot.baseCommit).toBe(f.release)
  expect(snapshot.baseRef).toBe('refs/tags/deploy/flow/#3')
  expect(snapshot.draftHead).toBe(current)
  expect(current).not.toBe(f.release)
  expect(runGit(f.repo, ['rev-parse', 'refs/tags/deploy/flow/#3^{commit}'])).toBe(f.release)
  const file = snapshot.versionFiles!.find(item => item.path === 'workflows/flow.yaml')!
  expect(Buffer.from(file.contentBase64, 'base64').toString('utf8')).toBe(JSON.parse(historyJson).content)
  const script = snapshot.versionFiles!.find(item => item.path === 'scripts/run.sh')!
  expect(Buffer.from(script.contentBase64, 'base64').toString('utf8')).toBe('original\n')
  const draft = await readPackScriptSnapshot(f.repo, 'flow', 'pack', 'draft', { includeVersionFiles: true })
  expect(draft).toMatchObject({ baseRef: 'origin/wf/flow', baseCommit: current, draftHead: current })
  expect(Buffer.from(draft.versionFiles!.find(item => item.path === 'scripts/run.sh')!.contentBase64, 'base64').toString('utf8')).toBe('changed after deployment\n')
})
