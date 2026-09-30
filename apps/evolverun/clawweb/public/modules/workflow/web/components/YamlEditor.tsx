import { useState, useCallback, useEffect, useRef } from 'react'
import { stringify as stringifyYaml, parse as parseYaml } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, WorkflowValidationResult, WorkflowValidationIssue } from '@avernet/clawweb-shared/web/types'

interface YamlEditorProps {
  spec: WorkflowSpec | null
  onImport: (spec: WorkflowSpec) => void
}

interface ApplyFeedback {
  type: 'success' | 'error'
  summary: string
  issues?: WorkflowValidationIssue[]
  normalizedSummary?: string
}

export default function YamlEditor({ spec, onImport }: YamlEditorProps) {
  const [yamlText, setYamlText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [applyFeedback, setApplyFeedback] = useState<ApplyFeedback | null>(null)
  const syncingFromSpec = useRef(false)
  const successTimerRef = useRef<ReturnType<typeof setTimeout>>(null)

  // Sync from spec to YAML text (when spec changes from visual editor)
  useEffect(() => {
    if (syncingFromSpec.current) {
      syncingFromSpec.current = false
      return
    }
    if (spec) {
      const yaml = stringifyYaml(spec, { lineWidth: 0 })
      setYamlText(yaml)
      setIsDirty(false)
    }
  }, [spec])

  // Clear feedback when user edits
  useEffect(() => {
    if (isDirty) {
      setApplyFeedback(null)
      if (successTimerRef.current) {
        clearTimeout(successTimerRef.current)
        successTimerRef.current = null
      }
    }
  }, [isDirty])

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value
      setYamlText(value)
      setIsDirty(true)
      setError(null)

      // Try parsing on every change to show live validation
      try {
        const parsed = parseYaml(value) as WorkflowSpec
        if (!parsed.id || !parsed.version || !parsed.title || !Array.isArray(parsed.nodes)) {
          setError('无效：必须包含 id、version、title 和 nodes 数组')
          return
        }
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'YAML 语法错误')
      }
    },
    [],
  )

  const handleApply = useCallback(async () => {
    // Step 1: Local YAML parse check
    let parsed: WorkflowSpec
    try {
      parsed = parseYaml(yamlText) as WorkflowSpec
      if (!parsed.id || !parsed.version || !parsed.title || !Array.isArray(parsed.nodes)) {
        setError('无效：必须包含 id、version、title 和 nodes 数组')
        setApplyFeedback({
          type: 'error',
          summary: '基础结构校验失败',
          issues: [
            { path: '', message: 'YAML 必须包含 id、version、title 和 nodes 数组', severity: 'error' },
          ],
        })
        return
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'YAML 解析错误'
      setError(msg)
      setApplyFeedback({
        type: 'error',
        summary: 'YAML 语法解析失败',
        issues: [{ path: '', message: msg, severity: 'error' }],
      })
      return
    }

    // Step 2: Call backend validate API for detailed validation
    setIsValidating(true)
    setApplyFeedback(null)
    try {
      const result: WorkflowValidationResult = await api.workflows.validate(parsed)

      if (result.valid) {
        // Success — import the normalized spec
        syncingFromSpec.current = true
        const specToImport = result.normalizedSpec ?? parsed
        onImport(specToImport)
        setIsDirty(false)
        setError(null)

        // Build success summary
        const ns = specToImport
        const nodeCount = ns.nodes?.length ?? 0
        const nodeIds = ns.nodes?.map((n) => n.id).join(', ') ?? ''
        const summary = nodeCount > 0
          ? `${nodeCount} 个节点${nodeCount <= 8 ? `：${nodeIds}` : ''}`
          : '0 个节点'

        setApplyFeedback({
          type: 'success',
          summary: `✓ 校验通过 — ${summary}`,
          normalizedSummary: `id: ${ns.id} | version: ${ns.version} | title: ${ns.title}`,
        })

        // Auto-dismiss success after 4 seconds
        if (successTimerRef.current) clearTimeout(successTimerRef.current)
        successTimerRef.current = setTimeout(() => {
          setApplyFeedback(null)
          successTimerRef.current = null
        }, 4000)
      } else {
        // Validation failed — show detailed issues
        const issues = result.issues ?? []
        setApplyFeedback({
          type: 'error',
          summary: `校验失败 — 发现 ${issues.length} 个问题`,
          issues,
          normalizedSummary: result.normalizedSpec
            ? `部分解析结果: id=${result.normalizedSpec.id}, title=${result.normalizedSpec.title}`
            : undefined,
        })
      }
    } catch (err) {
      // API error fallback
      const msg = err instanceof Error ? err.message : '校验请求失败'
      setApplyFeedback({
        type: 'error',
        summary: '校验请求失败',
        issues: [{ path: '', message: msg, severity: 'error' }],
      })
    } finally {
      setIsValidating(false)
    }
  }, [yamlText, onImport])

  const handleReset = useCallback(() => {
    if (spec) {
      const yaml = stringifyYaml(spec, { lineWidth: 0 })
      setYamlText(yaml)
      setIsDirty(false)
      setError(null)
      setApplyFeedback(null)
      if (successTimerRef.current) {
        clearTimeout(successTimerRef.current)
        successTimerRef.current = null
      }
    }
  }, [spec])

  const handleDismissFeedback = useCallback(() => {
    setApplyFeedback(null)
    if (successTimerRef.current) {
      clearTimeout(successTimerRef.current)
      successTimerRef.current = null
    }
  }, [])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-3 py-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-600">YAML</span>
          {isDirty && <span className="text-xs text-amber-500">已修改</span>}
          {error && <span className="max-w-xs truncate text-xs text-red-500">{error}</span>}
        </div>
        <div className="flex items-center gap-1.5">
          {isDirty && (
            <button
              onClick={handleReset}
              className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
            >
              Reset
            </button>
          )}
          <button
            onClick={() => void handleApply()}
            disabled={!!error || !isDirty || isValidating}
            className="rounded bg-blue-600 px-2.5 py-0.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            {isValidating ? '校验中…' : 'Apply'}
          </button>
        </div>
      </div>

      {/* Validation feedback banner */}
      {applyFeedback && (
        <div
          className={`border-b px-3 py-2 text-xs ${
            applyFeedback.type === 'success'
              ? 'border-green-200 bg-green-50 text-green-800'
              : 'border-red-200 bg-red-50 text-red-800'
          }`}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <p className="font-medium">{applyFeedback.summary}</p>
              {applyFeedback.normalizedSummary && (
                <p className="mt-0.5 text-[11px] opacity-70">{applyFeedback.normalizedSummary}</p>
              )}
              {applyFeedback.issues && applyFeedback.issues.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {applyFeedback.issues.map((issue, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="mt-0.5 shrink-0 text-[10px]">
                        {issue.severity === 'warning' ? '⚠' : '✗'}
                      </span>
                      <span className="min-w-0">
                        {issue.path ? (
                          <>
                            <span className="font-mono font-medium">{issue.path}</span>
                            <span className="mx-1">→</span>
                            <span>{issue.message}</span>
                          </>
                        ) : (
                          <span>{issue.message}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {applyFeedback.type === 'error' && (
              <button
                onClick={handleDismissFeedback}
                className="shrink-0 rounded p-0.5 text-xs opacity-50 hover:opacity-100"
                title="关闭"
              >
                ✕
              </button>
            )}
          </div>
        </div>
      )}

      <textarea
        value={yamlText}
        onChange={handleChange}
        onKeyDown={(e) => {
          // Ctrl/Cmd+Enter to apply
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault()
            void handleApply()
          }
          // Tab inserts 2 spaces
          if (e.key === 'Tab') {
            e.preventDefault()
            const start = e.currentTarget.selectionStart
            const end = e.currentTarget.selectionEnd
            const newText = yamlText.substring(0, start) + '  ' + yamlText.substring(end)
            setYamlText(newText)
            setIsDirty(true)
            // Restore cursor position after state update
            requestAnimationFrame(() => {
              e.currentTarget.selectionStart = start + 2
              e.currentTarget.selectionEnd = start + 2
            })
          }
        }}
        spellCheck={false}
        className="flex-1 resize-none bg-gray-900 p-3 font-mono text-xs leading-relaxed text-green-100 focus:outline-none"
      />
    </div>
  )
}