import { useState, type ReactNode } from 'react'
import { useIssueGroups } from '../issue-groups'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import IssueRepairSuggestions from './IssueRepairSuggestions'

type Props = { workflowId: string; selected: string[]; canEdit: boolean; onToggle: (id: string) => void;
  onResult: (result: RepairCandidatesResponse) => void; renderItem: (item: RepairInboxItem, canEdit: boolean) => ReactNode }
function ArchiveContents(props: Props) {
  const [page, setPage] = useState(1)
  const query = useIssueGroups(props.workflowId, { page, pageSize: 20, versionScope: 'historical', sort: 'recent' })
  if (query.isPending) return <p role="status">正在读取历史问题…</p>
  if (query.error) return <p role="alert">历史问题读取失败。<button onClick={() => void query.refetch()}>重试</button></p>
  return <div className="mt-3 space-y-3">
    <p className="text-xs text-slate-500">按问题汇总旧部署与保存测试记录。勾选的历史建议只作为当前目标的参考，不会重新应用旧补丁或改变历史处理状态。</p>
    {!query.data?.groups.length && <p className="text-sm text-slate-500">暂无历史问题。</p>}
    {query.data?.groups.map(group => <details key={group.signature} className="rounded-lg border border-slate-200 p-3">
      <summary className="cursor-pointer text-sm">{group.summary?.summary || group.sources[0]?.reasoning || group.signature}</summary>
      <IssueRepairSuggestions {...props} signature={group.signature} includeHistorical versionScope="historical" />
    </details>)}
    {query.data?.page && query.data.page.totalPages > 1 && <nav aria-label="历史问题分页" className="flex justify-between text-sm">
      <button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
      <span>{page} / {query.data.page.totalPages}</span>
      <button disabled={page >= query.data.page.totalPages} onClick={() => setPage(value => value + 1)}>下一页</button>
    </nav>}
  </div>
}
export default function HistoricalIssueArchive(props: Props) {
  const [open, setOpen] = useState(false)
  return <details className="rounded-xl border border-slate-200 bg-white p-4" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm font-semibold">历史版本的问题与建议</summary>
    {open && <ArchiveContents {...props} />}
  </details>
}
