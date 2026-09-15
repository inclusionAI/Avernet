import React, { type ReactNode } from 'react'
import { evolveTaskRegistry, isEvolveTaskType } from './task-registry'

/** Presentation only: the Singlebox server still validates every operation. */
export default function SingleboxTaskEntry({ version, taskType, governance = false, children }: {
  version: 'openversion' | 'internalversion'
  governance?: boolean
  taskType: string | null
  children: ReactNode
}) {
  if (version !== 'openversion' || (!governance && (taskType === null
    || ['diagnose', 'bench', 'runtime_cleanup', 'pack', 'pack_restore', 'optimize', 'bench_optimize', 'full'].includes(taskType)))) return <>{children}</>
  const label = isEvolveTaskType(taskType) ? evolveTaskRegistry[taskType].label : '此任务类型'
  return <section className="mx-auto max-w-5xl px-6 py-10" role="status">
    <h1 className="text-xl font-semibold">{label}</h1>
    <p className="mt-4 text-sm text-amber-800">该入口暂未在开源版开放；不会替换为其他任务。</p>
    <p className="mt-2 text-sm text-gray-500">会话诊断、Bot修复、治理优化本期不开放，当前不可提交。</p>
    <a className="mt-5 inline-block text-sm text-blue-600" href={taskType === 'pack' || taskType === 'pack_restore' ? '/evolve/packs' : '/evolve'}>返回</a>
  </section>
}
