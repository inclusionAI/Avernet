import type { Session } from '../editor/packVersionSession'

type Props = {
  reconciliation: NonNullable<Session['state']['reconciliation']>
  busy: boolean
  downloadError: boolean
  onDownload: () => void
  onObserveHead: () => void
}

const actionClassName = 'inline-flex min-h-[31px] max-w-full cursor-pointer items-center justify-center rounded-[7px] border border-input bg-card px-3 py-1.5 text-[11px] font-semibold text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-40'

/** Presentation only: observing HEAD never resolves a save or replaces its candidate. */
export default function PackReconciliationNotice({ reconciliation, busy, downloadError, onDownload, onObserveHead }: Props) {
  return <section aria-label="保存结果对账" className="mb-3 min-w-0 overflow-hidden rounded-lg border border-warning/30 bg-card text-[11px] leading-relaxed text-foreground">
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-warning/20 bg-warning/10 px-4 py-3">
      <h3 className="text-[13px] font-semibold">保存结果对账</h3>
      <span className="rounded-full border border-warning/30 bg-card px-2 py-0.5 text-[10px] font-semibold text-warning-foreground">结果待核对 · 文件只读</span>
    </header>
    <div className="space-y-3 p-4">
      <p>保存结果仍未知；查看当前 HEAD 不能证明候选是否曾经写入或已被后续提交覆盖。</p>
      <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-3 text-muted-foreground">
        {reconciliation.identity
          ? <p className="break-all">服务返回的待对账身份：分支 {reconciliation.identity.branch} · commit {reconciliation.identity.commit}。这不代表保存成功。</p>
          : <p>没有可信的待对账分支和 commit，请勿推测候选身份。</p>}
        {reconciliation.observation?.kind === 'head' && <p className="break-all">读取时的远端草稿 HEAD：{reconciliation.observation.commit}。{reconciliation.identity && (reconciliation.identity.commit === reconciliation.observation.commit ? '与待对账 commit 相同。' : '与待对账 commit 不同，远端可能已再次移动。')}</p>}
        {reconciliation.observation?.kind === 'snapshot-not-found' && <p>服务报告未找到草稿快照或 Pack 清单。这不证明之前的写入失败或未曾发生，请在外部核对历史。</p>}
        {reconciliation.checkError && <p className="text-destructive">无法读取远端 HEAD，未清除对账状态或本地草稿。</p>}
        {reconciliation.operationState && <p>保存操作状态：{reconciliation.operationState}。只有 completed 结果才能确认保存。</p>}
        <p className="break-all font-mono text-[10px]">requestId：{reconciliation.attempt.requestId}</p>
      </div>
      <p className="text-muted-foreground">原始候选可导出为 JSON（包含路径、模式和 Base64 原始字节）；文件可能含敏感信息，请妥善保管。</p>
      <div role="group" aria-label="对账操作" className="flex flex-wrap items-center gap-2">
        <button type="button" className={actionClassName} onClick={onDownload}>下载原始尝试候选（JSON）</button>
        <button type="button" className={actionClassName} disabled={reconciliation.checking || busy} onClick={onObserveHead}>{reconciliation.checking ? '正在读取远端 HEAD…' : '查看远端草稿 HEAD'}</button>
      </div>
      {downloadError && <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-destructive">下载失败；候选仍保留在当前页面会话中。</p>}
      <p role="alert" className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-warning-foreground">系统不保存操作日志，无法自动恢复或安全重试。请先人工核对 Git 的 草稿(wf)分支与候选，再重新打开页面。</p>
    </div>
  </section>
}
