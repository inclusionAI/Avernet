// @vitest-environment jsdom
import React from 'react'
import express from 'express'
import Database from 'better-sqlite3'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db'
import { SkillAssetRepository } from '../../../../server/repositories/skill-asset-repository'
import { StageSkillRepository } from '../../../../server/repositories/stage-skill-repository'
import { createSkillTaskDefaultsRouter } from '../../../../server/routes/skill-task-defaults'
import type { OcbSpace } from '../../../../server/internal/module-api'
import SkillTaskLaunchDialog, { type SkillTaskAction } from '../SkillTaskLaunchDialog'

const transportFetch = globalThis.fetch
const team: OcbSpace = { id: 'real-team-208', name: 'Renamed team', type: 'TEAM', role: 'MEMBER' }
const asset = { assetId: 'asset-1', botId: 'original-bot', name: 'Contract Skill', skillId: 'skill-1', currentVersion: 'v1', updatedAt: 1789060000 }
let db: SqliteDatabase
let stages: StageSkillRepository
let server: ReturnType<express.Application['listen']> | undefined
let baseUrl: string
let submissions: Array<Record<string, unknown>>

function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname + location.search}</output> }
function open(action: SkillTaskAction) { return render(<MemoryRouter><SkillTaskLaunchDialog asset={asset} action={action} onClose={() => undefined} /><Location /></MemoryRouter>) }

beforeEach(async () => {
  vi.stubGlobal('React', React)
  db = new SqliteDatabase(new Database(':memory:'))
  await runMigrations(db, 'sqlite')
  const skills = new SkillAssetRepository(db)
  stages = new StageSkillRepository(db)
  await skills.createAsset({ assetId: asset.assetId, versionId: 'asset-v1', ownerUserId: 'original-owner',
    spaceId: team.id, spaceType: team.type, spaceName: team.name, botId: asset.botId, ocbSkillId: asset.skillId,
    displayName: asset.name, packageRef: 'fixture:skill', packageSha256: 'fixture-checksum' })
  const app = express()
  app.use(express.json())
  app.use('/api/evolve', createSkillTaskDefaultsRouter({ skills, stages,
    spaces: { listAccessibleSpaces: async ({ identity }) => identity.userId === 'team-reader' ? [team] : [] },
    policies: [{ spaceId: team.id, kind: 'skill_hardening', diagnosePreprocessStageSkillId: 'hardening-stage' }],
  }))
  submissions = []
  // Capture the real client's POST locally; never dispatch a live Bot task.
  app.post('/api/evolve/tasks', (req, res) => { submissions.push(req.body); res.json({ task_id: 'local-contract-task', status: 'pending' }) })
  server = await new Promise<ReturnType<typeof app.listen>>((resolve, reject) => {
    const instance = app.listen(0, '127.0.0.1', () => resolve(instance))
    instance.once('error', reject)
  })
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`
  // Model the authenticated host while using the actual client, HTTP router and repositories.
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers)
    headers.set('X-User-Id', 'team-reader')
    return transportFetch(new URL(url, baseUrl), { ...init, headers })
  })
})

afterEach(async () => {
  cleanup()
  vi.unstubAllGlobals()
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()))
  server = undefined
  await db?.close()
})

async function seedStage() {
  await stages.createImplementation({ implementationId: 'verified-97-v1', stageSkillId: 'hardening-stage',
    ownerUserId: 'publisher', spaceId: team.id, spaceType: 'TEAM', spaceName: team.name,
    displayName: 'Verified hardening', stage: 'diagnose', mode: 'preprocess', versionNo: 1,
    packageRef: 'fixture:stage', packageSha256: 'fixture-checksum', staticValidation: { status: 'passed' } })
  await stages.registerImplementation('verified-97-v1')
  await stages.updateIntegrationTest('verified-97-v1', 'test-1', 'test_passed')
}

describe('defaults HTTP router → launch dialog → task client contract', () => {
  it.each(['diagnose', 'optimize'] as const)('launches %s for a team reader with the original owner and exact verified binding', async (action) => {
    await seedStage()
    open(action)
    await screen.findByText('original-owner')
    expect(screen.queryByText('team-reader')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: action === 'diagnose' ? '确认诊断' : '确认优化' }))
    await waitFor(() => expect(submissions).toHaveLength(1))
    expect(submissions[0]).toMatchObject({ targetSkillAssetId: asset.assetId, userId: 'original-owner', botId: asset.botId,
      taskType: action === 'diagnose' ? 'diagnose' : 'full', model: 'GLM-5.2' })
    if (action === 'optimize') expect(submissions[0]).toMatchObject({ stageSelection: { diagnose: true, plan: true, optimize: true },
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: 'verified-97-v1' } } } })
    else expect(submissions[0]).not.toHaveProperty('stageExtensions')
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/runs/local-contract-task'))
  })

  it('uses the real server unavailable reason and sends no POST without an eligible Stage', async () => {
    open('optimize')
    await screen.findByText('不可启动优化：尚无可用的已通过集成测试的加固 Stage，请检查空间权限和 Stage 登记状态。')
    const confirm = screen.getByRole('button', { name: '确认优化' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.click(confirm)
    expect(submissions).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '自定义 / 高级' }))
    const query = new URLSearchParams(screen.getByTestId('location').textContent!.split('?')[1])
    expect(Object.fromEntries(query)).toEqual({ type: 'full', target: 'skill', assetId: asset.assetId, skillAction: 'optimize' })
  })
})
