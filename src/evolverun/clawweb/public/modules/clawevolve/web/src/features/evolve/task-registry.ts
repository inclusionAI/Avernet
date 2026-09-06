import type { EvolveTaskType } from '@avernet/clawweb-shared/web/types'

export const evolveTaskTypes = [
  'diagnose', 'optimize', 'apply', 'full', 'bench', 'bench_optimize', 'pack', 'pack_restore', 'runtime_cleanup', 'repair',
] as const

export type { EvolveTaskType } from '@avernet/clawweb-shared/web/types'

export const evolveTaskRegistry: Record<EvolveTaskType, { type: EvolveTaskType; label: string }> = {
  diagnose: { type: 'diagnose', label: 'Bot诊断' },
  optimize: { type: 'optimize', label: '诊断后优化' },
  apply: { type: 'apply', label: '应用' },
  full: { type: 'full', label: 'Bot自进化' },
  bench: { type: 'bench', label: 'Bench诊断' },
  bench_optimize: { type: 'bench_optimize', label: 'Bench优化' },
  pack: { type: 'pack', label: '创建Pack' },
  pack_restore: { type: 'pack_restore', label: '应用Pack' },
  runtime_cleanup: { type: 'runtime_cleanup', label: '任务清理' },
  repair: { type: 'repair', label: 'Bot修复' },
}

export const evolveBranches = [
  { key: 'diagnosis', label: '诊断后优化', taskType: 'optimize', status: 'available' },
  { key: 'bench', label: 'Bench优化', taskType: 'bench_optimize', status: 'available' },
  { key: 'governance', label: '治理优化', taskType: 'full', status: 'available' },
] as const

export function isEvolveTaskType(value: unknown): value is EvolveTaskType {
  return typeof value === 'string' && value in evolveTaskRegistry
}
