import { useState, type ReactNode } from 'react'
import { workflowSnapshotKey, type WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot'
import { snapshotLabel } from '../../../../server/contracts/repair-version-scope'
import { useIssueGroups } from '../issue-groups'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import IssueRepairSuggestions from './IssueRepairSuggestions'

type Props = { workflowId: string; selected: string[]; canEdit: boolean; onToggle: (id: string) => void;
  onResult: (result: RepairCandidatesResponse) => void; renderItem: (item: RepairInboxItem, canEdit: boolean) => ReactNode }
type Group = NonNullable<ReturnType<typeof useIssueGroups>['data']>['groups'][number]
function versionedGroups(groups: Group[]) {
  const versions = new Map<string, { key: string; label: string; groups: Group[] }>()
  for (const group of groups) {
    const sources = new Map<string, Group['sources']>()
    for (const source of group.sources) {
      const binding = source.analysisContext?.workflowSnapshot as WorkflowSnapshotBinding | undefined
      const key = binding?.status === 'resolved' ? workflowSnapshotKey(binding.snapshot) : 'unresolved'
      if (!versions.has(key)) versions.set(key, { key, label: binding?.status === 'resolved' ? snapshotLabel(binding.snapshot) : '版本未确认', groups: [] })
      sources.set(key, [...(sources.get(key) ?? []), source])
    }
    for (const [key, members] of sources) versions.get(key)!.groups.push({ ...group, sources: members })
  }
  return [...versions.values()]
}
function ArchivedIssue({ group, snapshotKey, ...props }: Props & { group: Group; snapshotKey: string }) {
  const [open, setOpen] = useState(false)
  return <details className="border-t border-slate-200 px-4 py-3" onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open) }}>
    <summary className="cursor-pointer text-sm">{group.summary?.summary || group.sources[0]?.reasoning || group.signature}</summary>
    {open && <IssueRepairSuggestions {...props} signature={group.signature} snapshotKey={snapshotKey} includeHistorical versionScope="historical" />}
  </details>
}
function ArchivedVersion({ version, ...props }: Props & { version: ReturnType<typeof versionedGroups>[number] }) {
  const [open,setOpen] = useState(false)
  return <details className="overflow-hidden rounded-lg border border-slate-200" onToggle={event => { if(event.target === event.currentTarget) setOpen(event.currentTarget.open) }}>
    <summary className="cursor-pointer bg-slate-50 px-4 py-3 text-sm font-medium">{version.label} · 本页 {version.groups.length} 个问题</summary>
    {open && version.groups.map(group => <ArchivedIssue key={group.signature} {...props} group={group} snapshotKey={version.key} />)}
  </details>
}
function ArchiveContents(props: Props) {
  const [page, setPage] = useState(1)
  const query = useIssueGroups(props.workflowId, { page, pageSize: 20, versionScope: 'historical', sort: 'recent' })
  if (query.isPending) return <p role="status">正在读取历史问题…</p>
  if (query.error) return <p role="alert">历史问题读取失败。<button onClick={() => void query.refetch()}>重试</button></p>
  return <div className="mt-3 space-y-3">
    <p className="text-xs text-slate-500">按工作流运行版本区分旧部署、保存测试与版本未确认的记录。展开问题后读取建议；历史建议仅作当前目标的参考。</p>
    {!query.data?.groups.length && <p className="text-sm text-slate-500">暂无历史问题。</p>}
    {versionedGroups(query.data?.groups ?? []).map(version => <ArchivedVersion key={version.key} {...props} version={version} />)}
    {query.data?.page && query.data.page.totalPages > 1 && <nav aria-label="历史问题分页" className="flex justify-between text-sm">
      <button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
      <span>{page} / {query.data.page.totalPages}</span>
      <button disabled={page >= query.data.page.totalPages} onClick={() => setPage(value => value + 1)}>下一页</button>
    </nav>}
  </div>
}
export default function HistoricalIssueArchive(props: Props) {
  const [open, setOpen] = useState(false)
  return <details className="rounded-xl border border-slate-200 bg-white p-4" onToggle={event => { if(event.target === event.currentTarget) setOpen(event.currentTarget.open) }}>
    <summary className="cursor-pointer text-sm font-semibold">历史版本的问题与建议</summary>
    {open && <ArchiveContents {...props} />}
  </details>
}
