import type { RepairDiff } from '../../../../server/contracts/repair-workbench'
import { useRef, useState } from 'react'

export function patchRows(patch: string) {
  let before = 0, after = 0
  return patch.split('\n').flatMap((text, index) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text)
    if (hunk) { before = Number(hunk[1]); after = Number(hunk[2]); return [{ index, text, before: '', after: '', kind: 'hunk' }] }
    if (!before && !after || !text || text.startsWith('\\')) return []
    const kind = text.startsWith('+') ? 'added' : text.startsWith('-') ? 'removed' : 'context'
    return [{ index, text, before: kind === 'added' ? '' : String(before++), after: kind === 'removed' ? '' : String(after++), kind }]
  })
}

/** Render actual Git hunks: unchanged content is not painted as changed. */
export default function PackDiff({ file }: { file: RepairDiff['files'][number] }) {
  const container = useRef<HTMLDivElement>(null)
  const [change, setChange] = useState(0)
  const [foldWhitespace, setFoldWhitespace] = useState(false)
  if (file.binary) return <p className="p-4 text-sm text-muted-foreground">二进制变更，不能展示文本 Diff。请单独核对文件。</p>
  if (file.truncated) return <p role="alert" className="p-4 text-sm text-warning-foreground">文件超过审阅预算，Diff 不完整。不能确认此候选；请缩小修复范围后重新生成。</p>
  if (!file.patch) return <p role="alert" className="p-4 text-sm text-warning-foreground">当前服务未返回行级 Diff，请升级发布服务后重新读取；不能把全文对照当作变更审阅。</p>
  const rows = patchRows(file.patch), hunks = rows.filter(row => row.kind === 'hunk')
  const whitespaceRows = new Set<number>()
  for (let i = 0; i < rows.length;) {
    if (!['added', 'removed'].includes(rows[i].kind)) { i++; continue }
    const block: typeof rows = []
    while (i < rows.length && ['added', 'removed'].includes(rows[i].kind)) block.push(rows[i++])
    const removed = block.filter(row => row.kind === 'removed'), added = block.filter(row => row.kind === 'added')
    if (removed.length === added.length) removed.forEach((row, index) => {
      if (row.text.slice(1).replace(/[ \t]+$/, '') === added[index].text.slice(1).replace(/[ \t]+$/, '')) {
        whitespaceRows.add(row.index); whitespaceRows.add(added[index].index)
      }
    })
  }
  const metadata = file.patch.split('\n').filter(line => /^(old mode|new mode|new file mode|deleted file mode|rename from|rename to) /.test(line))
  function jump(value: number) {
    const next = Math.max(0, Math.min(hunks.length - 1, value)); setChange(next)
    container.current?.querySelectorAll('[data-diff-hunk]')[next]?.scrollIntoView({ block: 'nearest' })
  }
  return <div>{metadata.length > 0 && <pre aria-label="文件属性变更" className="border-b border-border bg-muted/50 px-3 py-2 text-xs">{metadata.join('\n')}</pre>}<div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs">
    <p><span className="text-success">+{rows.filter(row => row.kind === 'added').length}</span> / <span className="text-destructive">−{rows.filter(row => row.kind === 'removed').length}</span> · {hunks.length} 处变更 · 未修改区域已折叠</p>
    <div className="flex gap-3"><button aria-label="上一处变更" disabled={change === 0} onClick={() => jump(change - 1)} className="text-primary disabled:text-muted-foreground">↑ 上一处</button><button aria-label="下一处变更" disabled={change >= hunks.length - 1} onClick={() => jump(change + 1)} className="text-primary disabled:text-muted-foreground">↓ 下一处</button></div>
  </div><div className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
    <label className="flex items-center gap-2"><input type="checkbox" checked={foldWhitespace} onChange={event => setFoldWhitespace(event.target.checked)} />折叠仅尾随空白变更</label>
    <p className="mt-1">审阅辅助：仅折叠逐行对应且只差行尾空格/制表符的 {whitespaceRows.size} 行；保留缩进、原始行号与完整原始 Diff。不会修改候选。</p>
  </div><div ref={container} className="max-h-[36rem] overflow-auto"><table aria-label={`文件差异 ${file.path}`} className="w-full border-collapse font-mono text-xs leading-6"><tbody>
    {rows.filter(row => !foldWhitespace || !whitespaceRows.has(row.index)).map(row => <tr key={row.index} data-diff-hunk={row.kind === 'hunk' ? true : undefined} className={row.kind === 'added' ? 'bg-success/10' : row.kind === 'removed' ? 'bg-destructive/10' : row.kind === 'hunk' ? 'bg-primary/5 text-primary' : ''}>
      <td className="w-12 select-none border-r border-border px-2 text-right text-muted-foreground">{row.before}</td>
      <td className="w-12 select-none border-r border-border px-2 text-right text-muted-foreground">{row.after}</td>
      <td className="whitespace-pre px-3">{row.text}</td>
    </tr>)}
  </tbody></table></div></div>
}
