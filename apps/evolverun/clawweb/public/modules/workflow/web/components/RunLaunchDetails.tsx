import { useId, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { RunLaunchMetadata } from '../../shared/run-launch-metadata'
import { getRunLaunchDetails, type LaunchField } from '../utils/run-launch-details'

type RunLaunchDetailsProps = {
  run: { flow_id: string } & Partial<RunLaunchMetadata>
}

export default function RunLaunchDetails({ run }: RunLaunchDetailsProps) {
  const [searchParams] = useSearchParams()
  const { input, params } = getRunLaunchDetails(run)
  const search = searchParams.get('from') === 'workspace' ? '?from=workspace' : ''

  return (
    <div className="mt-4 grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
      <section aria-label="用户启动输入" className="min-w-0 rounded-lg border border-border bg-muted/30 p-3.5">
        <h2 className="text-sm font-semibold text-foreground">用户启动输入</h2>
        <div className="mt-3 space-y-3">
          <LaunchValue key={`${run.flow_id}:input`} field={input} />
          <LaunchValue key={`${run.flow_id}:params`} field={params} />
        </div>
      </section>
      <aside aria-label="运行链路" className="min-w-0 rounded-lg border border-border bg-card p-3.5">
        <h2 className="text-sm font-semibold text-foreground">运行链路</h2>
        <div className="mt-3 space-y-4">
          <LineageField label="父运行" column="parent_flow_id" id={run.parent_flow_id} search={search} />
          <LineageField label="根运行" column="root_flow_id" id={run.root_flow_id} search={search} />
        </div>
      </aside>
    </div>
  )
}

function LaunchValue({ field }: { field: LaunchField }) {
  const contentId = useId()
  const [expanded, setExpanded] = useState(false)
  const expandable = field.text !== null && (field.text.length > 600 || field.text.split('\n').length > 5)

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <h3 className="text-xs font-medium text-foreground">{field.label}</h3>
          <p className="mt-0.5 break-words font-mono text-[10px] text-muted-foreground [overflow-wrap:anywhere]">{field.source}</p>
        </div>
        {expandable && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={contentId}
            aria-label={`${expanded ? '收起' : '展开'}${field.label}`}
            onClick={() => setExpanded(value => !value)}
            className="rounded px-1 py-0.5 text-xs text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {expanded ? '收起全文' : '展开全文'}
          </button>
        )}
      </div>
      {field.text !== null ? (
        <pre
          id={contentId}
          role="region"
          aria-label={`${field.label}内容`}
          tabIndex={0}
          className={`min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-card px-3 py-2 text-xs leading-relaxed text-foreground [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${field.json ? 'font-mono' : 'font-sans'} ${expanded ? 'max-h-96' : 'max-h-28'}`}
        >{field.text}</pre>
      ) : (
        <p className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">未记录</p>
      )}
      {field.note && <p className="mt-1 text-xs text-muted-foreground">{field.note}</p>}
    </div>
  )
}

function LineageField({ label, column, id, search }: {
  label: string
  column: 'parent_flow_id' | 'root_flow_id'
  id?: string | null
  search: string
}) {
  return (
    <div className="min-w-0">
      <h3 className="text-xs font-medium text-foreground">{label}</h3>
      <p className="mt-0.5 break-words font-mono text-[10px] text-muted-foreground [overflow-wrap:anywhere]">flow_runs.{column}</p>
      <div className="mt-1.5 min-w-0 text-xs">
        {id?.trim() ? (
          <Link
            to={`/runs/${encodeURIComponent(id)}${search}`}
            aria-label={`查看${label} ${id}`}
            className="block rounded font-mono leading-relaxed text-primary underline-offset-4 [overflow-wrap:anywhere] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >{id}</Link>
        ) : <span className="text-muted-foreground" aria-label={`${label}未记录`}>—</span>}
      </div>
    </div>
  )
}
