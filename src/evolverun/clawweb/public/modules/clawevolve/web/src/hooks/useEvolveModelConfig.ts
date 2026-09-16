import { useQuery } from '@tanstack/react-query'

export type EvolveModelConfig = {
  defaultModel: string
  models: string[]
  repairModels?: string[]
}

const EMPTY_MODEL_CONFIG: EvolveModelConfig = { defaultModel: '', models: [] }

export function useEvolveModelConfig(): EvolveModelConfig {
  const query = useQuery({
    queryKey: ['evolve-model-options'],
    queryFn: async () => {
      const response = await fetch('/api/evolve/model-options')
      if (!response.ok) throw new Error('Failed to load model options')
      return response.json() as Promise<EvolveModelConfig>
    },
    staleTime: Number.POSITIVE_INFINITY,
  })
  return query.data ?? EMPTY_MODEL_CONFIG
}
