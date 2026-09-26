// @vitest-environment jsdom
import React from 'react'
import express from 'express'
import Database from 'better-sqlite3'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db'
import { SkillAssetRepository } from '../../../../server/repositories/skill-asset-repository'
import { AppConfigRepository } from '../../../../server/repositories/app-config-repository'
import { StageSkillRepository } from '../../../../server/repositories/stage-skill-repository'
import { createSkillTaskDefaultsRouter } from '../../../../server/routes/skill-task-defaults'
import type { HostSpace } from '../../../../server/internal/module-api'
import type { EvolveHostExtension } from '../../../../server/services/evolve/host-extensions'
import SkillTaskLaunchDialog, { type SkillTaskAction } from '../SkillTaskLaunchDialog'

const transportFetch = globalThis.fetch
const team: HostSpace = { id: 'team-alpha', name: 'Renamed team', type: 'TEAM', role: 'MEMBER' }
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
    spaceId: team.id, spaceType: team.type, spaceName: team.name, botId: asset.botId, externalSkillId: asset.skillId,
    displayName: asset.name, packageRef: 'fixture:skill', packageSha256: 'fixture-checksum' })
  const app = express()
  app.use(express.json())
  const hostExtension: EvolveHostExtension = { id: 'host.contract', resolveSkillTaskPreset: (context) => {
    if (context.targetSkill.spaceId !== team.id) return null
    const implementation = context.availableStageImplementations.find((item) => item.stageSkillId === 'host-stage'
      && item.status === 'registered' && item.integrationTestStatus === 'test_passed')
    return implementation ? { stageExtensions: { diagnose: { preprocess: {
      enabled: true, implementationId: implementation.implementationId,
    } } } } : { unavailableReason: 'Host requirement is unavailable' }
  } }
  app.use('/api/evolve', createSkillTaskDefaultsRouter({ config: new AppConfigRepository(db), skills, stages,
    spaces: { listAccessibleSpaces: async ({ identity }) => identity.userId === 'team-reader' ? [team] : [] },
    hostExtensions: [hostExtension],
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
  await stages.createImplementation({ implementationId: 'verified-host-v1', stageSkillId: 'host-stage',
    ownerUserId: 'publisher', spaceId: team.id, spaceType: 'TEAM', spaceName: team.name,
    displayName: 'Verified hardening', stage: 'diagnose', mode: 'preprocess', versionNo: 1,
    packageRef: 'fixture:stage', packageSha256: 'fixture-checksum', staticValidation: { status: 'passed' } })
  await stages.registerImplementation('verified-host-v1')
  await stages.updateIntegrationTest('verified-host-v1', 'test-1', 'test_passed')
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
    expect(submissions[0]).toMatchObject({ stageSelection: { diagnose: true, plan: true, optimize: action === 'optimize' },
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: 'verified-host-v1' } } } })
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/evolve/runs/local-contract-task'))
  })

  it('uses the real server unavailable reason and sends no POST without an eligible Stage', async () => {
    open('optimize')
    await screen.findByText('不可启动优化：Host requirement is unavailable')
    const confirm = screen.getByRole('button', { name: '确认优化' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.click(confirm)
    expect(submissions).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '自定义 / 高级' }))
    const query = new URLSearchParams(screen.getByTestId('location').textContent!.split('?')[1])
    expect(Object.fromEntries(query)).toEqual({ type: 'full', target: 'skill', assetId: asset.assetId, skillAction: 'optimize' })
  })
})
