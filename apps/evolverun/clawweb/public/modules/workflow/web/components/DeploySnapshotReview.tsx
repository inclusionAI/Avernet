type ReviewSide = { mode: string; contentBase64: string } | null
export type DeployReview = { path: string; operation: '新增' | '更新' | '删除'; before: ReviewSide; after: ReviewSide }

function reviewContents(side: ReviewSide): string {
  if (!side) return '（不存在）'
  const bytes = Uint8Array.from(atob(side.contentBase64), c => c.charCodeAt(0))
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    if (!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(text)) return `${text}\n\n[原始字节 Base64] ${side.contentBase64}`
  } catch { /* Binary content is presented losslessly as base64. */ }
  return `二进制 Base64：${side.contentBase64}`
}

export default function DeploySnapshotReview({ changes, reviewed, onReviewedChange }: {
  changes: DeployReview[]; reviewed: boolean; onReviewedChange: (value: boolean) => void
}) {
  return <>
    <div className="mt-3 max-h-80 overflow-auto rounded border border-border p-2" aria-label="部署文件差异">
      {changes.length === 0 ? <p>与已捕获的当前生效 tag 文件完全一致。</p> : changes.map(change => (
        <section key={change.path} className="mb-3 border-b border-border pb-2">
          <h4 className="break-all font-semibold">{change.operation}：{change.path}</h4>
          <p>生效模式：{change.before?.mode ?? '无'} → 草稿模式：{change.after?.mode ?? '无'}</p>
          <p>生效内容：</p><pre className="whitespace-pre-wrap break-all">{reviewContents(change.before)}</pre>
          <p>保存版本内容：</p><pre className="whitespace-pre-wrap break-all">{reviewContents(change.after)}</pre>
        </section>
      ))}
    </div>
    <p className="mt-2 text-xs">删除、文件模式变化及 Pack 外变化可能被服务器安全保护拒绝。仅服务器证明数据库无 active history 时可按首次部署审阅；缺失生产 tag 不能当作首次部署。</p>
    <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" checked={reviewed} onChange={event => onReviewedChange(event.target.checked)} />我已核对当前生效 tag 与草稿(wf)的全部文件差异，并确认发布；部署不会改写草稿分支。</label>
  </>
}
