import { useState } from 'react'
import { useEditorStore } from '../editor/store'
import { NODE_CATALOG, createEditorNode } from '../editor/nodeCatalog'
import { NODE_DRAG_TYPE } from './editor/CanvasPresentation'
import FlowNodeIcon from './editor/FlowNodeIcon'

export interface NodePaletteProps {
  onAdd?: (type: string) => void
}

export default function NodePalette({ onAdd }: NodePaletteProps) {
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(false)
  const { spec, addNode, selectNode } = useEditorStore()
  const search = query.trim().toLocaleLowerCase()
  const matches = NODE_CATALOG.filter((item) => `${item.label} ${item.type} ${item.description}`.toLocaleLowerCase().includes(search))
  const common = matches.filter((item) => item.group === 'common')
  const advanced = matches.filter((item) => item.group === 'advanced')
  const disabled = !onAdd && !spec

  const add = (type: string) => {
    if (onAdd) { onAdd(type); return }
    const node = createEditorNode(type)
    addNode(node)
    // The store may reject additions while an unapplied draft is active.
    if (useEditorStore.getState().spec?.nodes.some((item) => item.id === node.id)) selectNode(node.id)
  }
  const renderCards = (items: typeof matches) => items.map((item) => (
    <button key={item.type} type="button" draggable={!disabled} disabled={disabled} onClick={() => add(item.type)}
      onDragStart={(event) => { event.dataTransfer.setData(NODE_DRAG_TYPE, item.type); event.dataTransfer.effectAllowed = 'copy' }}
      aria-label={`添加${item.label}`} title={`${item.label} · ${item.type}`}
      className="group flex w-full cursor-grab items-center gap-2.5 rounded-lg border border-input bg-card p-2.5 text-left transition hover:border-primary/50 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-50">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><FlowNodeIcon type={item.type} /></span>
      <span className="min-w-0 flex-1"><span className="block text-xs font-medium text-foreground">{item.label}</span><span className="mt-1 block text-[10px] leading-4 text-muted-foreground">{item.description}</span></span>
      <span aria-hidden="true" className="text-muted-foreground/60 group-hover:text-primary">⠿</span>
    </button>
  ))

  return (
    <aside aria-label="节点库" className="flex h-full w-52 shrink-0 flex-col overflow-y-auto border-r border-input bg-card p-3 text-foreground">
      <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold">添加节点</h2><span className="text-[10px] text-muted-foreground">点击或拖入</span></div>
      <input aria-label="搜索节点类型" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或类型…"
        className="w-full rounded-lg border border-input bg-background px-3 py-2 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring" />
      {common.length > 0 && <section aria-label="常用节点" className="mt-5"><h3 className="mb-2 text-[10px] font-medium text-muted-foreground">常用节点</h3><div className="space-y-2">{renderCards(common)}</div></section>}
      {!search && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="my-3 flex w-full items-center justify-between rounded px-1 py-2 text-xs text-muted-foreground hover:text-primary focus-visible:ring-2 focus-visible:ring-ring">更多节点 <span aria-hidden="true">{expanded ? '−' : '+'}</span></button>}
      {(expanded || search) && advanced.length > 0 && <section aria-label="更多节点" className={search ? 'mt-4' : ''}><h3 className="mb-2 text-[10px] font-medium text-muted-foreground">更多节点</h3><div className="space-y-2">{renderCards(advanced)}</div></section>}
      {matches.length === 0 && <p role="status" className="py-6 text-center text-xs text-muted-foreground">没有匹配的节点类型</p>}
      <div className="mt-5 border-t border-input py-4 text-[11px] leading-5 text-muted-foreground"><p className="font-medium text-foreground">连起来，就是工作流</p><p className="mt-1">从输出端口连到输入端口。<br />结果分支在节点属性中配置。</p></div>
      <p className="mt-auto rounded-lg bg-muted/60 p-3 text-[10px] leading-5 text-muted-foreground">节点按钮支持键盘添加。<br />复杂类型保留完整原始配置。</p>
    </aside>
  )
}
