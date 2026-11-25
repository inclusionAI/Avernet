// @vitest-environment node
import express from 'express'
import { once } from 'node:events'
import { webcrypto } from 'node:crypto'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository'
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database'
import { createAutoHealConfigTestDb } from '../../../server/config/__tests__/auto-heal-git-fixtures'
import { WorkflowSpecRepository } from '../../../server/repositories/workflow-spec-repository'
import { WorkflowDeployHistoryRepository } from '../../../server/repositories/workflow-deploy-history-repository'
import { createPackVersionRouter } from '../../../server/routes/pack-version'
import { editorActiveDeploymentToken } from '../../../server/services/editor-active-deployment-token'
import { readActivePackVersionFromRemote, readPackVersionFromRemote } from '../../../server/services/pack-version-remote'
import { createLocalRemote, runGit } from '../../../server/services/__tests__/clawflows-git-client-fixture'
import { originalActiveDraftBaseline, readEditorWorkflowSource } from '../editorWorkflowSource'

const direct = { id: 'flow', title: 'Active', content: '用户正文', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] }
let root = '', db: SqliteDatabase | undefined, server: ReturnType<express.Application['listen']> | undefined
afterEach(async () => {
  vi.unstubAllGlobals(); vi.restoreAllMocks()
  if (server) await new Promise<void>(resolve => server!.close(() => resolve()))
  if (db) await db.close()
  if (root) rmSync(root, { recursive: true, force: true })
  server = undefined; db = undefined; root = ''
})
it('recovers direct content through the real active Git/SQLite route and rejects a changed row', async () => {
  root = mkdtempSync(path.join(tmpdir(), 'history-git-direct-'))
  const bare = createLocalRemote(root), seed = path.join(root, 'seed')
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true })
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n')
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), stringify(direct))
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'active release'])
  runGit(seed, ['tag', '-a', 'deploy/flow/#1', '-m', 'release'])
  runGit(seed, ['push', bare, 'HEAD:refs/heads/wf/flow', 'refs/tags/deploy/flow/#1'])
  db = createAutoHealConfigTestDb()
  await db.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);`)
  const localDb = db, specs = new WorkflowSpecRepository(db), history = new WorkflowDeployHistoryRepository(db), perms = new BotWorkflowPermissionRepository(db)
  const specJson = JSON.stringify(direct)
  await history.insert({ workflowId: 'flow', packId: 'pack', deployNumber: 1, version: 1, tagName: 'deploy/flow/#1', action: 'deploy', specJson, isActive: true })
  await perms.upsert({ bot_id: null, bot_owner_id: 'editor', workflow_id: 'flow', can_view: 1, can_execute: 0, can_edit: 1 })
  const ctx = { db, workflowId: 'flow', gitConfig: { remoteUrl: bare, username: 'fixture', email: 'fixture@example.invalid' }, gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' } }
  const app = express()
  app.get('/api/workflows/flow', (_req, res) => res.status(404).json({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }))
  app.get('/api/workflows/flow/history', async (_req, res) => {
    const row = (await localDb.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0]
    res.json({ workflowId: 'flow', history: [], active: { deployNumber: row.deploy_number, version: row.version, isActive: true, packId: row.pack_id, tagName: row.tag_name, action: row.action, activeToken: editorActiveDeploymentToken('flow', row) } })
  })
  app.get('/api/workflows/flow/history/by-deploy/1', async (_req, res) => {
    const row = (await localDb.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ?', ['flow']))[0]
    res.json({ workflowId: 'flow', deployNumber: row.deploy_number, version: row.version, tagName: row.tag_name, action: row.action, specJson: row.spec_json })
  })
  app.use('/api/workflows', createPackVersionRouter({ resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: perms, workflowSpecRepo: specs,
    readActiveSnapshot: (workflowId, version) => readActivePackVersionFromRemote({ ...ctx, workflowId, version }),
    readSnapshot: (workflowId, packId, version) => readPackVersionFromRemote({ ...ctx, workflowId, packId, version }),
  }))
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening')
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address unavailable')
  const realFetch = globalThis.fetch, base = `http://127.0.0.1:${address.port}`
  vi.stubGlobal('crypto', webcrypto); vi.stubGlobal('window', { location: { hostname: 'example.invalid' } })
  const storage = new Map<string, string>()
  vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) })
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => realFetch(`${base}${url}`, init))
  const source = await readEditorWorkflowSource('flow')
  expect(source.spec).toEqual(direct)
  expect(originalActiveDraftBaseline(source.spec)).toMatchObject({ workflowId: 'flow', packId: 'pack', specJson })
  await localDb.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...direct, title: 'Changed' })])
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证生效 Git')
  const rows = await localDb.query<{ spec_json: string }>('SELECT spec_json FROM workflow_deploy_history')
  expect(rows[0].spec_json).toBe(JSON.stringify({ ...direct, title: 'Changed' }))
}, 30000)
