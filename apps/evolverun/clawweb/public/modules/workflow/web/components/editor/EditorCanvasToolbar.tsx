import { useState } from 'react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { getNodeMeta } from '../../editor/nodeCatalog'

interface EditorCanvasToolbarProps {
  nodes: WorkflowNode[]
  canUndo: boolean
  canRedo: boolean
  hasPendingEdits: boolean
  selection: 'node' | 'edge' | null
  onUndo: () => void
  onRedo: () => void
  onFocus: (nodeId: string) => void
  onFit: () => void
  onReadableZoom: () => void
  onDelete: () => void
}
const buttonClass = 'shrink-0 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 disabled:cursor-not-allowed'

export default function EditorCanvasToolbar(props: EditorCanvasToolbarProps) {
  const [query, setQuery] = useState('')
  const search = query.trim().toLocaleLowerCase()
  const matches = search ? props.nodes.filter((node) => `${node.title} ${node.id} ${node.executor?.type} ${getNodeMeta(node.executor?.type)?.label ?? ''}`.toLocaleLowerCase().includes(search)) : []
  const focus = (nodeId: string) => { props.onFocus(nodeId); setQuery('') }
  return (
    <div role="toolbar" aria-label="画布工具栏" className="z-10 flex flex-wrap items-center gap-1 border-b border-input bg-card px-3 py-2 text-foreground">
      <button type="button" className={buttonClass} onClick={props.onUndo} disabled={!props.canUndo || props.hasPendingEdits} title="撤销 Ctrl/⌘ Z">撤销</button>
      <button type="button" className={buttonClass} onClick={props.onRedo} disabled={!props.canRedo || props.hasPendingEdits} title="重做 Ctrl/⌘ Shift Z">重做</button>
      <span aria-hidden="true" className="mx-1 h-4 border-l border-input" />
      <button type="button" className={buttonClass} onClick={props.onFit} disabled={!props.nodes.length}>总览</button>
      <button type="button" className={buttonClass} onClick={props.onReadableZoom} disabled={!props.nodes.length} title="以可读比例定位选中节点或首个节点">100%</button>
      {props.selection && <button type="button" className={`${buttonClass} !text-destructive`} onClick={props.onDelete} disabled={props.hasPendingEdits}>{props.selection === 'edge' ? '删除连线' : '删除节点'}</button>}
      <form className="relative ml-auto min-w-28 flex-1 max-w-52" role="search" onSubmit={(event) => { event.preventDefault(); if (matches[0]) focus(matches[0].id) }}>
        <input aria-label="查找画布节点" placeholder="查找节点…" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') setQuery('') }}
          className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring" />
        {search && <div className="absolute right-0 top-full z-20 mt-2 max-h-60 w-64 overflow-auto rounded-lg border border-input bg-card p-1 shadow-lg">
          {matches.length ? matches.map((node) => <button key={node.id} type="button" onClick={() => focus(node.id)} className="flex w-full flex-col rounded px-2 py-2 text-left hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">
            <span className="max-w-full truncate text-xs font-medium">{node.title || node.id}</span><span className="max-w-full truncate text-[10px] text-muted-foreground">{node.id} · {node.executor?.type}</span>
          </button>) : <p role="status" className="p-3 text-xs text-muted-foreground">没有匹配的画布节点</p>}
        </div>}
      </form>
    </div>
  )
}
