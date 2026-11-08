import { useMemo, useRef, type UIEvent } from 'react'
import { tokenizeYamlLine, type YamlTokenType } from './YamlCodeViewer'
import { compareReviewText, formatReviewSnapshotWithStatus } from './autoHealReviewDiffModel'

const TOKEN_CLASSES: Record<YamlTokenType, string> = {
  plain: 'text-foreground', key: 'text-primary', punctuation: 'text-muted-foreground',
  string: 'text-success', number: 'text-primary', boolean: 'text-primary font-semibold',
  null: 'text-muted-foreground italic', comment: 'text-muted-foreground italic',
  anchor: 'text-primary', tag: 'text-primary',
}

/** A document-only comparison: no fabricated version or deployment metadata. */
export default function AutoHealReviewDiff({ originalJson, candidateJson }: {
  originalJson: string; candidateJson: string
}) {
  const { rows, additions, deletions, positional, rawLeft, rawRight } = useMemo(() => {
    const original = formatReviewSnapshotWithStatus(originalJson)
    const candidate = formatReviewSnapshotWithStatus(candidateJson)
    return { ...compareReviewText(original.text, candidate.text), rawLeft: original.oversized, rawRight: candidate.oversized }
  }, [originalJson, candidateJson])
  const leftRef = useRef<HTMLDivElement>(null), rightRef = useRef<HTMLDivElement>(null)
  const syncScroll = (event: UIEvent<HTMLDivElement>, other: HTMLDivElement | null) => {
    // Equality also breaks the scroll-event feedback loop. Horizontal positions
    // intentionally remain independent; fixed-height rows keep both sides aligned.
    if (other && other.scrollTop !== event.currentTarget.scrollTop) other.scrollTop = event.currentTarget.scrollTop
  }
  const sides = [
    { side: 'left' as const, label: '当前数据库原始草稿', raw: rawLeft, ref: leftRef, otherRef: rightRef },
    { side: 'right' as const, label: '拟保存的完整修复内容', raw: rawRight, ref: rightRef, otherRef: leftRef },
  ]

  return <div className="mt-3 overflow-hidden rounded border border-input">
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-input bg-muted px-3 py-2 text-xs">
      <span className="text-success">+{additions} 行新增</span>
      <span className="text-destructive">-{deletions} 行删除</span>
      <span className="text-muted-foreground">完整内容 · 纵向同步 · 横向独立 · 空行用于对齐</span>
    </div>
    {(rawLeft || rawRight) && <p role="status" className="border-b border-input bg-muted px-3 py-2 text-xs text-muted-foreground">
      {sides.filter(side => side.raw).map(side => side.label).join('、')}超过格式化预算（64 KiB 字符），已回退为完整原始 JSON；跳过 YAML 解析和语法着色，不截断内容。
    </p>}
    {positional && <p role="status" className="border-b border-input bg-muted px-3 py-2 text-xs text-muted-foreground">
      文档较大，已限制差异计算复杂度并降级为同位置逐行对比；插入或删除可能使后续行标为变更，两侧完整内容仍全部保留。
    </p>}
    {additions === 0 && deletions === 0 && <p className="border-b border-input px-3 py-2 text-xs text-muted-foreground">两侧内容完全相同</p>}
    <div className="overflow-x-auto">
      <div className="grid min-w-[720px] grid-cols-2">
        {sides.map(({ side, label, raw, ref, otherRef }) => <div key={side} className="min-w-0 border-r border-input last:border-r-0">
          <h3 className="border-b border-input bg-muted px-3 py-2 text-xs font-medium">{label}</h3>
          <div ref={ref} role="region" aria-label={label} tabIndex={0}
            onScroll={event => syncScroll(event, otherRef.current)}
            className="h-96 overflow-scroll bg-background font-mono text-xs leading-6 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
            <div className="w-max min-w-full py-2">
              {rows.map((row, index) => {
                const line = row[side]
                const number = side === 'left' ? line?.fromLine : line?.toLine
                const marker = line?.type === 'del' ? '-' : line?.type === 'add' ? '+' : ''
                const background = line?.type === 'del' ? 'bg-destructive/10' : line?.type === 'add' ? 'bg-success/10' : line ? '' : 'bg-muted'
                return <div key={index} data-diff-row={index} data-line-kind={line?.type ?? 'empty'}
                  aria-label={line ? undefined : '对齐空行'} className={`flex h-6 items-center ${background}`}>
                  {line && <span className="sr-only">第 {number} 行，{line.type === 'del' ? '删除' : line.type === 'add' ? '新增' : '未变更'}</span>}
                  <span data-line-number aria-hidden="true" className="sticky left-0 z-10 w-12 shrink-0 select-none border-r border-input bg-muted px-1 text-right tabular-nums text-muted-foreground">{number ?? ''}</span>
                  <span data-line-marker aria-hidden="true" className={`w-5 shrink-0 select-none text-center ${line?.type === 'del' ? 'text-destructive' : 'text-success'}`}>{marker}</span>
                  {line && <code className="whitespace-pre pr-6">{raw ? line.text : tokenizeYamlLine(line.text).map((token, tokenIndex) =>
                    <span key={tokenIndex} data-syntax-kind={token.type} className={TOKEN_CLASSES[token.type]}>{token.text}</span>)}</code>}
                </div>
              })}
            </div>
          </div>
        </div>)}
      </div>
    </div>
    <p className="border-t border-input px-3 py-2 text-xs text-muted-foreground">仅格式化显示（超预算时保留原文），不修改待保存 JSON 或数据库 / Git 基线；小屏可横向滚动查看并列内容。</p>
  </div>
}
