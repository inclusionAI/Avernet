import { useMemo } from 'react'
import AutoHealReviewDiff from './AutoHealReviewDiff'
import { normalizationChanges } from '../editor/yamlDraft'
import type { AutoHealUnifiedBaseline } from '../api/autoHealUnifiedBaseline'

/** Display complete documents; the diagnosis's own diff has no trusted source. */
export default function AutoHealSaveReview({ baseline, candidateJson, onConfirm, onCancel }: {
  baseline: AutoHealUnifiedBaseline; candidateJson: string; onConfirm: () => void; onCancel: () => void
}) {
  const paths = useMemo(() => normalizationChanges(JSON.parse(baseline.originalSpecJson), JSON.parse(candidateJson)),
    [baseline.originalSpecJson, candidateJson])
  return <section className="min-w-0 rounded border border-input bg-card p-4 text-sm" aria-label="保存前核对">
    <p>诊断没有可验证的源草稿身份。请对照当前数据库原始草稿与拟修复内容，确认这些差异可以覆盖当前草稿。保存时将使用本次读取的草稿 token 和 Git 基线；基线变化时由服务端拒绝。</p>
    <p className="mt-2 font-medium">差异路径（{paths.length}）</p>
    <pre className="max-h-40 overflow-auto whitespace-pre-wrap">{paths.length ? paths.join('\n') : '无结构差异'}</pre>
    <AutoHealReviewDiff originalJson={baseline.originalSpecJson} candidateJson={candidateJson} />
    <div className="mt-3 flex gap-2">
      <button type="button" onClick={onConfirm} className="rounded bg-primary px-3 py-2 text-primary-foreground">确认差异并保存</button>
      <button type="button" onClick={onCancel} className="rounded border border-input px-3 py-2">取消核对</button>
    </div>
  </section>
}
