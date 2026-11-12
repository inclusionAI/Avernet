import type { FlowRun } from '@avernet/clawweb-shared/web/types'

interface RunMarkersProps {
  run: Pick<FlowRun, 'env' | 'tag'>
  variant?: 'compact' | 'details'
}

const unavailable = '未记录'

export default function RunMarkers({ run, variant = 'compact' }: RunMarkersProps) {
  const env = run.env ?? null
  const tag = run.tag ?? null
  const detailTag = tag === null ? unavailable : tag || '—'

  if (variant === 'details') {
    return (
      <>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">环境</dt>
          <dd className="mt-0.5 truncate font-mono text-sm font-medium text-foreground" title={env ?? unavailable}>{env ?? unavailable}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Tag</dt>
          <dd className="mt-0.5 truncate text-sm font-medium text-foreground" title={tag ?? unavailable}>{detailTag}</dd>
        </div>
      </>
    )
  }

  return (
    <div className="mt-1 flex min-w-0 max-w-64 items-center gap-1 text-[10px]">
      <span className="max-w-24 shrink-0 truncate rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground" title={env ?? unavailable}>
        {env ?? unavailable}
      </span>
      {tag === null ? (
        <span className="min-w-0 max-w-40 truncate rounded-full border border-border px-2 py-0.5 text-muted-foreground" title={unavailable}>
          {unavailable}
        </span>
      ) : tag !== '' && (
        <span className="min-w-0 max-w-40 truncate rounded-full border border-border px-2 py-0.5 text-muted-foreground" title={tag}>
          {tag}
        </span>
      )}
    </div>
  )
}
