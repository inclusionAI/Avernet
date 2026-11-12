import type { FlowRun } from '@avernet/clawweb-shared/web/types'

interface RunMarkersProps {
  run: Pick<FlowRun, 'env' | 'tag'>
  variant?: 'compact' | 'details'
}

const unavailable = '未记录'
const ENV_LABEL = '流量标识'
const TAG_LABEL = '标签'

export default function RunMarkers({ run, variant = 'compact' }: RunMarkersProps) {
  const env = run.env ?? null
  const tag = run.tag ?? null
  const detailTag = tag === null ? unavailable : tag || '—'

  if (variant === 'details') {
    return (
      <>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">流量标识</dt>
          <dd className="mt-0.5 truncate font-mono text-sm font-medium text-foreground" title={env ?? unavailable}>{env ?? unavailable}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">标签</dt>
          <dd className="mt-0.5 truncate text-sm font-medium text-foreground" title={tag ?? unavailable}>{detailTag}</dd>
        </div>
      </>
    )
  }

  const displayTag = tag === null ? unavailable : tag || '—'

  return (
    <div className="mt-1 flex max-w-72 flex-wrap items-center gap-1 text-[10px]">
      <span className="inline-flex min-w-0 max-w-36 shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
        <span className="shrink-0">{ENV_LABEL}</span>
        <span className="min-w-0 truncate font-mono" title={`${ENV_LABEL}: ${env ?? unavailable}`}>{env ?? unavailable}</span>
      </span>
      <span className="inline-flex min-w-0 max-w-48 shrink-0 items-center gap-1 rounded-full border border-border px-2 py-0.5 text-muted-foreground">
        <span className="shrink-0">{TAG_LABEL}</span>
        <span className="min-w-0 truncate font-mono" title={`${TAG_LABEL}: ${displayTag}`}>{displayTag}</span>
      </span>
    </div>
  )
}
