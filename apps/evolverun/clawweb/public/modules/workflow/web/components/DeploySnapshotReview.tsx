import type { ReactNode } from 'react'
import { SYNTAX_CLASSES, syntaxLanguageForPath, syntaxTokens } from './syntaxHighlighting'

type ReviewSide = { mode: string; contentBase64: string } | null
export type DeployReview = { path: string; operation: '新增' | '更新' | '删除'; before: ReviewSide; after: ReviewSide }

/** Keep the original bytes visible even when their text representation is safe. */
function reviewContents(side: Exclude<ReviewSide, null>): { text: string | null; base64: string } {
  const bytes = Uint8Array.from(atob(side.contentBase64), c => c.charCodeAt(0))
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    if (!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(text)) return { text, base64: side.contentBase64 }
  } catch { /* Binary content is presented losslessly as base64. */ }
  return { text: null, base64: side.contentBase64 }
}

// Bound both highlighted and plain tokens: a long run of empty YAML lines can
// otherwise create one DOM node per newline. The remainder stays visible verbatim.
function ReviewCode({ text, path }: { text: string; path: string }) {
  const children: ReactNode[] = []
  let plainStart = 0
  let tokenCount = 0
  for (const token of syntaxTokens(text, syntaxLanguageForPath(path))) {
    if (tokenCount++ >= 2048) break
    if (token.kind === 'plain') continue
    if (token.start > plainStart) children.push(text.slice(plainStart, token.start))
    children.push(<span key={token.start} data-syntax-kind={token.kind} className={SYNTAX_CLASSES[token.kind]}>{text.slice(token.start, token.end)}</span>)
    plainStart = token.end
  }
  if (plainStart < text.length) children.push(text.slice(plainStart))
  return <code>{children}</code>
}

function ReviewContents({ label, path, side }: { label: string; path: string; side: ReviewSide }) {
  if (!side) return <section aria-label={label} className="rounded-lg border border-border bg-background px-3 py-2.5">
    <h5 className="text-[11px] font-semibold text-muted-foreground">{label}</h5>
    <p className="mt-2 text-xs text-muted-foreground">（不存在）</p>
  </section>

  const { text, base64 } = reviewContents(side)
  return <section aria-label={label} className="min-w-0 overflow-hidden rounded-lg border border-border bg-background">
    <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2">
      <h5 className="text-[11px] font-semibold text-foreground">{label}</h5>
      <span className="font-mono text-[10px] text-muted-foreground">{text === null ? 'Base64' : syntaxLanguageForPath(path).toUpperCase()}</span>
    </div>
    {text !== null && <pre role="region" aria-label={`${label}代码内容`} tabIndex={0} className="m-0 overflow-x-auto px-3 py-2.5 font-mono text-xs leading-5 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"><ReviewCode text={text} path={path} /></pre>}
    <div className={`${text === null ? '' : 'border-t border-border'} bg-muted/20 px-3 py-2`}>
      <span className="text-[10px] font-medium text-muted-foreground">{text === null ? '二进制 Base64' : '[原始字节 Base64]'}</span>
      <pre className="m-0 mt-1 whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-muted-foreground">{base64}</pre>
    </div>
  </section>
}

export default function DeploySnapshotReview({ changes, reviewed, onReviewedChange }: {
  changes: DeployReview[]; reviewed: boolean; onReviewedChange: (value: boolean) => void
}) {
  return <>
    <div className="mt-3 max-h-[min(40vh,28rem)] space-y-3 overflow-auto rounded-xl border border-border bg-muted/30 p-3" aria-label="部署文件差异" tabIndex={0}>
      {changes.length === 0 ? <p className="px-1 py-2 text-xs text-muted-foreground">与已捕获的当前生效 tag 文件完全一致。</p> : changes.map(change => (
        <section key={change.path} className="min-w-0 overflow-hidden rounded-lg border border-border bg-card">
          <div className="flex flex-wrap items-start gap-x-2 gap-y-1 border-b border-border bg-muted/30 px-3 py-2.5">
            <h4 className="min-w-0 flex-1 break-all font-mono text-xs font-semibold text-foreground">{change.operation}：{change.path}</h4>
            <p className="w-full text-[11px] text-muted-foreground">生效模式：<span className="font-mono">{change.before?.mode ?? '无'}</span><span aria-hidden="true"> → </span><span className="sr-only">，</span>草稿模式：<span className="font-mono">{change.after?.mode ?? '无'}</span></p>
          </div>
          <div className="grid min-w-0 gap-2 p-2">
            <ReviewContents label="生效内容" path={change.path} side={change.before} />
            <ReviewContents label="保存版本内容" path={change.path} side={change.after} />
          </div>
        </section>
      ))}
    </div>
    <p className="mt-2 text-xs">删除、文件模式变化及 Pack 外变化可能被服务器安全保护拒绝。仅服务器证明数据库无 active history 时可按首次部署审阅；缺失生产 tag 不能当作首次部署。</p>
    <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => onReviewedChange(event.target.checked)} />我已核对当前生效 tag 与草稿(wf)的全部文件差异，并确认发布；部署不会改写草稿分支。</label>
  </>
}
