import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import type { WorkflowSpec, VersionSnapshot, DeployHistoryItem } from '../types'

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text().catch(() => 'unknown')}`)
  return res.json() as Promise<T>
}

// Minimal api facade for migrated editor components from clawweb.
export const api = {
  workflows: {
    get: async (workflowId: string): Promise<WorkflowSpec> => {
      return fetchJson<WorkflowSpec>(`/api/workflows/${encodeURIComponent(workflowId)}`)
    },

    save: async (
      workflowId: string,
      spec: WorkflowSpec,
      opts?: { packId?: string; facade?: { command?: string; remark?: string }; originalWorkflowId?: string; botOwnerId?: string; botId?: string },
    ): Promise<WorkflowSpec> => {
      return fetchJson<WorkflowSpec>('/api/workflows/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workflowId,
          spec,
          packId: opts?.packId,
          facade: opts?.facade,
          originalWorkflowId: opts?.originalWorkflowId,
          botOwnerId: opts?.botOwnerId,
          botId: opts?.botId,
        }),
      })
    },

    validate: async (spec: WorkflowSpec): Promise<{ valid: boolean; errors: Array<{ message: string }> }> => {
      try {
        await fetchJson<unknown>('/api/workflows/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ spec }),
        })
        return { valid: true, errors: [] }
      } catch (err) {
        return { valid: false, errors: [{ message: err instanceof Error ? err.message : 'Validation failed' }] }
      }
    },

    getHistory: async (workflowId: string, limit = 20): Promise<{ history: DeployHistoryItem[] }> => {
      return fetchJson<{ history: DeployHistoryItem[] }>(
        `/api/workflows/${encodeURIComponent(workflowId)}/history?limit=${limit}`,
      )
    },

    getVersion: async (workflowId: string, version: number): Promise<VersionSnapshot> => {
      return fetchJson<VersionSnapshot>(
        `/api/workflows/${encodeURIComponent(workflowId)}/history/${version}`,
      )
    },

    export: async (workflowId: string): Promise<string> => {
      const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/export`)
      if (!res.ok) throw new Error(`Export failed: ${res.status}`)
      return res.text()
    },
  },

  dryRun: async (_params: unknown): Promise<unknown> => {
    throw new Error('Dry run is not implemented in evolvetrace standalone')
  },

  facades: {
    list: async (): Promise<Array<{ command: string; workflowId: string; remark?: string | null }>> => {
      const res = await fetchJson<{ data?: Array<{ command: string; workflowId: string; remark?: string | null }>; command?: string }[]>('/api/facades')
      if (Array.isArray(res)) return res as Array<{ command: string; workflowId: string; remark?: string | null }>
      return []
    },
  },

  knowledgeBases: {
    list: async (_enabledOnly = false) => [],
    get: async (_id: string) => null,
  },

  validationTemplates: {
    list: async (_enabledOnly = false) => [],
    get: async (_id: string) => null,
  },
}

export { parseYaml, stringifyYaml }
