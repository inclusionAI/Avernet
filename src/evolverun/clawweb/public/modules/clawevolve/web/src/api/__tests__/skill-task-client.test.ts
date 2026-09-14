import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../client'

const fetchJson = vi.hoisted(() => vi.fn())
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { evolve: {} }, fetchJson }))
afterEach(() => vi.resetAllMocks())

describe('Skill task launch API', () => {
  it('encodes the asset ID and does not hide defaults errors', async () => {
    fetchJson.mockResolvedValueOnce({ assetId: 'asset / 1' })
    expect(await api.evolve.getSkillTaskDefaults('asset / 1')).toEqual({ assetId: 'asset / 1' })
    expect(fetchJson).toHaveBeenCalledWith('/api/evolve/skill-assets/asset%20%2F%201/task-defaults')
    fetchJson.mockRejectedValueOnce(new Error('Access denied'))
    await expect(api.evolve.getSkillTaskDefaults('asset / 1')).rejects.toThrow('Access denied')
  })

  it('uses the existing tasks route for diagnosis with a fixed Skill and preserves the idempotency header', async () => {
    const input = { taskType: 'diagnose' as const, taskName: '诊断 Skill', botId: 'bot-1', userId: 'original-owner',
      targetSkillAssetId: 'asset-1', goal: '检查技能', diagnoseIntent: '检查技能', judgeBackend: 'subagent' as const,
      model: 'GLM-5.1', maxSessions: 10 }
    await api.evolve.createTask(input, 'request-1')
    expect(fetchJson).toHaveBeenCalledWith('/api/evolve/tasks', {
      method: 'POST', headers: { 'Idempotency-Key': 'request-1' }, body: JSON.stringify(input),
    })
  })
})
