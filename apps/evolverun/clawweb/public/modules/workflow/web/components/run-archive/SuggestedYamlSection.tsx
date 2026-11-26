import { useEffect, useRef, useState } from 'react'
import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { ActionButton, Badge, buttonClass, CodeBlock, EmptyState, ViewButton } from './ArchivePrimitives'
import { text, type Navigate } from './archive-helpers'

type Feedback = { kind: 'success' | 'error'; message: string }

export default function SuggestedYamlSection({ data, navigate }: { data: RunArchiveData; navigate: Navigate }) {
  const [view, setView] = useState<'suggested' | 'patch'>('suggested')
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [copying, setCopying] = useState(false)
  const urls = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const yaml = data.suggestedYaml
  const content = yaml?.yamlContent ?? ''
  const hasYaml = content.trim().length > 0
  // Only this suggestion's recorded patch belongs in its comparison view.
  const proposals = yaml?.patchProposal ? [yaml.patchProposal] : []
  const operationCount = proposals.reduce((count, proposal) => count + proposal.operations.length, 0)

  useEffect(() => {
    const pending = urls.current
    return () => {
      pending.forEach((timer, url) => { clearTimeout(timer); URL.revokeObjectURL(url) })
      pending.clear()
    }
  }, [])

  async function copyYaml() {
    setCopying(true)
    setFeedback(null)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(content)
      setFeedback({ kind: 'success', message: 'YAML 已复制。' })
    } catch {
      setFeedback({ kind: 'error', message: '复制 YAML 失败，请检查浏览器权限，或手动选择内容复制。' })
    } finally { setCopying(false) }
  }

  function downloadYaml() {
    setFeedback(null)
    let url: string | undefined
    let anchor: HTMLAnchorElement | undefined
    try {
      url = URL.createObjectURL(new Blob([content], { type: 'text/yaml;charset=utf-8' }))
      anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'suggested-workflow.yaml'
      document.body.appendChild(anchor)
      anchor.click()
      // Release after the browser consumes the click, and on unmount.
      const objectUrl = url
      const timer = setTimeout(() => { URL.revokeObjectURL(objectUrl); urls.current.delete(objectUrl) }, 0)
      urls.current.set(objectUrl, timer)
      setFeedback({ kind: 'success', message: '已发起 YAML 下载：suggested-workflow.yaml。请在浏览器中确认保存。' })
    } catch {
      if (url) URL.revokeObjectURL(url)
      setFeedback({ kind: 'error', message: '下载 YAML 失败，请重试，或复制 YAML 内容。' })
    } finally { anchor?.remove() }
  }

  return (
    <div className="min-w-0 space-y-4">
      <p className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-6 text-muted-foreground">这里只展示档案中的建议，未应用、未部署；请根据真实工作流契约人工审阅和校验。建议不等于可直接运行的配置。</p>
      {yaml && <div className="flex min-w-0 flex-wrap items-center gap-2"><Badge>置信度：{yaml.confidence}</Badge><p className="min-w-0 text-sm text-foreground [overflow-wrap:anywhere]">{yaml.summary}</p></div>}
      <nav aria-label="建议 YAML 视图" className="flex min-w-0 flex-wrap gap-2">
        <ViewButton active={view === 'suggested'} onClick={() => setView('suggested')}>建议 YAML 内容</ViewButton>
        <ViewButton active={view === 'patch'} onClick={() => setView('patch')} label="Patch 操作对比">Patch 操作对比 <span className="text-xs tabular-nums">{operationCount}</span></ViewButton>
      </nav>
      {view === 'suggested' && (hasYaml ? <>
        <pre tabIndex={0} aria-label="建议 YAML 内容" className="max-h-[36rem] min-w-0 max-w-full overflow-auto rounded-lg border border-border bg-muted/50 p-4 font-mono text-xs leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">{content}</pre>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void copyYaml()} disabled={copying} className={`${buttonClass} border-primary bg-primary text-primary-foreground hover:bg-primary/90`}>复制 YAML</button>
          <button type="button" onClick={downloadYaml} className={buttonClass}>下载 YAML</button>
        </div>
      </> : <EmptyState title="暂无建议 YAML" description="档案未提供 YAML 内容。尚未分析、分析未完成或未产出建议，都不等于无需修改；不会凭空生成配置。">
        <ActionButton onClick={() => navigate('analysis')}>查看 AI 诊断</ActionButton>
        <ActionButton onClick={() => navigate('process', 'nodes')}>查看节点执行</ActionButton>
        {operationCount > 0 && <ActionButton onClick={() => setView('patch')}>查看已有 Patch 操作</ActionButton>}
      </EmptyState>)}
      {view === 'patch' && <>
        <p className="text-xs leading-6 text-muted-foreground">档案未提供原始 YAML，无法生成真实的逐行 diff。下方仅对照展示已记录的 Patch 路径、操作和建议值，不伪造原值，不代表已应用。</p>
        {!operationCount ? <EmptyState title="暂无 Patch 操作" description="档案没有提供可对照的 Patch 操作。即使已有建议 YAML，也不能据此推造补丁或原始内容。">
          {hasYaml && <ActionButton onClick={() => setView('suggested')}>查看建议 YAML 内容</ActionButton>}
          <ActionButton onClick={() => navigate('analysis')}>查看 AI 诊断</ActionButton>
        </EmptyState> : proposals.map((proposal, index) => <section key={index} className="min-w-0 space-y-3 rounded-lg border border-border p-3">
          <h3 className="text-sm font-semibold text-foreground">Patch #{index + 1}</h3>
          <p className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">workflowId：{text(proposal.workflowId)} · schemaVersion：{text(proposal.schemaVersion)}</p>
          <div className="min-w-0 max-w-full overflow-auto rounded-lg border border-border" tabIndex={0} aria-label={`Patch ${index + 1} 操作对比`}>
            <table className="w-full min-w-[32rem] text-left text-xs">
              <caption className="sr-only">已记录 Patch 操作与建议值，原值未提供</caption>
              <thead className="bg-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="p-3">操作 / 路径</th>
                  <th scope="col" className="p-3">原始值</th>
                  <th scope="col" className="p-3">建议值</th>
                </tr>
              </thead>
              <tbody>
                {proposal.operations.map((operation, opIndex) => (
                  <tr key={opIndex} className="border-t border-border align-top">
                    <td className="max-w-64 p-3">
                      <Badge>{operation.op}</Badge>
                      <p className="mt-2 font-mono text-foreground [overflow-wrap:anywhere]">{operation.path}</p>
                    </td>
                    <td className="p-3 text-muted-foreground">未提供</td>
                    <td className="max-w-96 p-3">
                      {operation.op === 'remove' ? (
                        <span className="text-destructive">移除此字段</span>
                      ) : (
                        <CodeBlock
                          value={Object.prototype.hasOwnProperty.call(operation, 'value')
                            ? JSON.stringify(operation.value, null, 2) : undefined}
                          label={`Patch ${index + 1} 操作 ${opIndex + 1} 建议值`}
                          empty="未提供建议值"
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <CodeBlock value={proposal} label={`Patch ${index + 1} 原始数据`} />
        </section>)}
      </>}
      {feedback && <p role={feedback.kind === 'error' ? 'alert' : 'status'} className={`rounded-lg p-3 text-sm [overflow-wrap:anywhere] ${feedback.kind === 'error' ? 'bg-destructive/10 text-destructive' : 'bg-success/10 text-success'}`}>{feedback.message}</p>}
    </div>
  )
}
