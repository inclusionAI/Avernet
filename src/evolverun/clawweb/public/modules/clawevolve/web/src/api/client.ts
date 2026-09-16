// Keep module-specific contracts here until the shared client adopts them.
export * from '@avernet/clawweb-shared/web/api/client'
import {
  api as sharedApi,
  fetchJson,
  type EvolveSkillAsset as SharedSkillAsset,
  type EvolveStageDevelopment as SharedStageDevelopment,
  type EvolveStageSkill as SharedStageSkill,
  type EvolveStageMode,
} from '@avernet/clawweb-shared/web/api/client'

export type EvolveSpace = {
  id: string
  name: string
  type: 'PERSONAL' | 'TEAM'
  role: 'ADMIN' | 'MEMBER'
}

export type EvolveSpaceOwnership = {
  spaceId?: string | null
  spaceType?: EvolveSpace['type'] | null
  spaceName?: string | null
}

export type EvolveSkillAsset = SharedSkillAsset & EvolveSpaceOwnership
export type EvolveStageDevelopment = SharedStageDevelopment & EvolveSpaceOwnership
export type EvolveStageSkill = SharedStageSkill & EvolveSpaceOwnership

export type EvolveStageExtensionSelection = {
  stage: 'diagnose' | 'hardening' | 'plan' | 'optimize'
  mode: EvolveStageMode
  binding: {
    enabled: boolean
    implementationId: string
    displayName?: string
    ownerUserId?: string
    spaceId?: string | null
  }
}

export type EvolveTaskStageExtensions = Partial<Record<
  EvolveStageExtensionSelection['stage'],
  Partial<Record<EvolveStageMode, EvolveStageExtensionSelection['binding']>>
>>

export type EvolveSkillTaskDefaults = {
  assetId: string
  botId: string
  userId: string
  diagnose: EvolveSkillTaskPreset & { taskType: 'diagnose' }
  hardening: EvolveSkillTaskPreset & { taskType: 'hardening' }
  optimize: EvolveSkillTaskPreset & { taskType: 'full' }
}

export type EvolveSkillTaskPreset = {
    goal: string
    stageExtensions: EvolveTaskStageExtensions | null
    unavailableReason: string | null
    launchDescription: string | null
}

export type EvolveCreateTaskInput = Parameters<typeof sharedApi.evolve.createTask>[0] | (
  Parameters<typeof sharedApi.evolve.createDiagnosis>[0] & {
    taskType: 'diagnose'
    targetSkillAssetId?: string
    goal?: string
  }
) | {
  taskType: 'hardening'
  taskName: string
  userId: string
  botId: string
  botEnv?: string
  targetSkillAssetId: string
  goal: string
  model: string
  runtimeMaintenance?: boolean
  forceMessage?: boolean
  nodeCommandYamls?: Record<string, string>
  stageExtensions?: EvolveTaskStageExtensions
  stageSelection?: { diagnose: boolean; hardening: boolean; plan: boolean; optimize: boolean }
}

export const api = {
  ...sharedApi,
  evolve: {
    ...sharedApi.evolve,
    getSkillTaskDefaults(assetId: string): Promise<EvolveSkillTaskDefaults> {
      return fetchJson(`/api/evolve/skill-assets/${encodeURIComponent(assetId)}/task-defaults`)
    },
    createTask(input: EvolveCreateTaskInput, idempotencyKey?: string): Promise<{ task_id: string; status: string }> {
      return fetchJson('/api/evolve/tasks', {
        method: 'POST',
        ...(idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : {}),
        body: JSON.stringify(input),
      })
    },
    listSpaces(): Promise<{ items: EvolveSpace[] }> {
      return fetchJson('/api/evolve/spaces')
    },
    listSkillAssets(): Promise<{ items: EvolveSkillAsset[] }> {
      return sharedApi.evolve.listSkillAssets()
    },
    getSkillAsset(id: string): Promise<EvolveSkillAsset> {
      return sharedApi.evolve.getSkillAsset(id)
    },
    listStageDevelopments(): Promise<{ items: EvolveStageDevelopment[] }> {
      return sharedApi.evolve.listStageDevelopments()
    },
    getStageDevelopment(id: string): Promise<EvolveStageDevelopment> {
      return sharedApi.evolve.getStageDevelopment(id)
    },
    getStageSkill(id: string): Promise<EvolveStageSkill> {
      return sharedApi.evolve.getStageSkill(id)
    },
    uploadStageSkill(input: Parameters<typeof sharedApi.evolve.uploadStageSkill>[0]): Promise<EvolveStageSkill> {
      return sharedApi.evolve.uploadStageSkill(input)
    },
    registerStageSkill(id: string): Promise<EvolveStageSkill> {
      return sharedApi.evolve.registerStageSkill(id)
    },
    listStageSkills(input?: { search?: string }): Promise<{ items: EvolveStageSkill[] }> {
      const query = new URLSearchParams()
      if (input?.search?.trim()) query.set('search', input.search.trim())
      return fetchJson(`/api/evolve/stage-skills${query.size ? `?${query}` : ''}`)
    },
    createStageDevelopment(input: Parameters<typeof sharedApi.evolve.createStageDevelopment>[0] & {
      spaceId?: string
      displayName?: string
    }): Promise<EvolveStageDevelopment> {
      return fetchJson('/api/evolve/stage-developments', { method: 'POST', body: JSON.stringify(input) })
    },
    registerSkillAsset(input: { botId: string; skillId: string; spaceId?: string }): Promise<EvolveSkillAsset> {
      return fetchJson('/api/evolve/skill-assets', { method: 'POST', body: JSON.stringify(input) })
    },
  },
}
