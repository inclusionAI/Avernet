import StatusBadge from './StatusBadge'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { formatDuration } from '../utils/time'

export type AutoHealPhase = 'idle' | 'diagnosing' | 'diagnosed' | 'saving' | 'saved' | 'deploying' | 'deployed' | 'running' | 'run-error' | 'error'

export function PanelHeader({ run, onClose }: { run: FlowRun; onClose: () => void }) {
  return (
    <div className="flex items-center justify-between border-b border-gray-200 bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <svg className="h-5 w-5 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
          </svg>
          <h2 className="font-bold text-white text-lg">自动修复</h2>
        </div>
        <div className="mt-1 flex items-center gap-3">
          <span className="font-mono text-white/70 text-xs">{run.flow_id}</span>
          <StatusBadge status={run.status} />
        </div>
        <div className="mt-1 flex items-center gap-4 text-white/60 text-xs">
          <span>{run.workflow_title || run.workflow_id}</span>
          {run.total_duration_ms != null && <span>{formatDuration(run.total_duration_ms)}</span>}
          <span>{run.node_count} 节点</span>
          {run.failed_count > 0 && <span className="text-red-300">{run.failed_count} 失败</span>}
        </div>
      </div>
      <button
        onClick={onClose}
        className="ml-4 rounded-md p-1.5 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
        title="关闭"
      >
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  )
}

export function PhaseIndicator({ phase }: { phase: AutoHealPhase }) {
  const steps = [
    { key: 'diagnose', label: '诊断', active: phase === 'diagnosing', done: ['diagnosed', 'saving', 'saved', 'deploying', 'deployed', 'running', 'run-error'].includes(phase) },
    { key: 'save', label: '保存', active: phase === 'saving', done: ['saved', 'deploying', 'deployed', 'running', 'run-error'].includes(phase) },
    { key: 'deploy', label: '部署', active: phase === 'deploying', done: ['deployed', 'running', 'run-error'].includes(phase) },
    { key: 'rerun', label: '重跑', active: phase === 'running', done: phase === 'running' },
  ]

  const activeLabel =
    phase === 'saving' ? '保存中…' :
    phase === 'saved' ? '等待部署…' :
    phase === 'deploying' ? '部署中…' :
    phase === 'deployed' ? '部署完成' :
    phase === 'running' ? '运行中…' : null

  return (
    <div className="flex items-center gap-3 border-b border-gray-100 bg-gray-50 px-6 py-2.5 text-xs">
      {steps.map((step, i) => (
        <span key={step.key} className="flex items-center gap-1">
          <StepDot active={step.active} done={step.done} />
          <span className={step.active ? 'font-medium text-blue-600' : step.done ? 'text-green-600' : 'text-gray-400'}>
            {step.label}
          </span>
          {i < steps.length - 1 && <span className="mx-1 text-gray-300">→</span>}
        </span>
      ))}
      {(phase === 'error' || phase === 'run-error') && <span className="ml-auto font-medium text-red-500">{phase === 'run-error' ? '运行失败' : '失败'}</span>}
      {activeLabel && <span className="ml-auto font-medium text-blue-600">{activeLabel}</span>}
    </div>
  )
}

function StepDot({ active, done }: { active: boolean; done: boolean }) {
  if (done) return <span className="text-green-600">✓</span>
  if (active) return (
    <svg className="h-3 w-3 animate-spin text-blue-600" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12c0-6.627 5.373-12 12-12v4c-4.418 0-8 3.582-8 8H4z" />
    </svg>
  )
  return <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
}
