import type { EvolveScoreComparison } from '../api/client'

/** The same reported score view for versions and immutable operation events. */
export default function TestBenchComparison({ comparison, emptyLabel = '未评测' }: {
  comparison?: EvolveScoreComparison | null
  emptyLabel?: string
}) {
  if (!comparison) return <span className="text-xs text-gray-400">{emptyLabel}</span>
  const score = (value: number | null) => typeof value === 'number' && Number.isFinite(value) ? value : null
  const delta = score(comparison.delta)
  return <div className="text-xs">
    <div className="flex items-center gap-2 text-gray-600">
      <span>{score(comparison.baseline) ?? '—'}</span><span className="text-gray-300">→</span>
      <span className="font-semibold text-gray-900">{score(comparison.candidate) ?? '—'}</span>
      <span className={`font-medium ${delta != null && delta > 0 ? 'text-emerald-600' : delta != null && delta < 0 ? 'text-red-600' : 'text-gray-400'}`}>{delta == null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(4)}`}</span>
    </div>
    <p className="mt-1 text-[10px] text-gray-400">{comparison.name || 'test_score'}</p>
  </div>
}
