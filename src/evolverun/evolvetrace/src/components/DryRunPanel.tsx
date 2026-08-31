import { useState } from 'react'
import { useDryRun } from '../api/hooks'
import type { WorkflowSpec, DryRunResult } from '../types'

interface DryRunPanelProps {
  spec: WorkflowSpec | null
  onResult?: (result: DryRunResult) => void
}

export default function DryRunPanel({ spec, onResult }: DryRunPanelProps) {
  const [params, setParams] = useState<Record<string, string>>({})
  const [paramKey, setParamKey] = useState('')
  const [mocks, setMocks] = useState<Record<string, unknown>>({})
  const [mockNodeId, setMockNodeId] = useState('')
  const [mockOutput, setMockOutput] = useState('')

  const dryRunMutation = useDryRun()

  const addParam = () => {
    if (paramKey.trim()) {
      setParams((prev) => ({ ...prev, [paramKey.trim()]: '' }))
      setParamKey('')
    }
  }

  const removeParam = (key: string) => {
    setParams((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const updateParamValue = (key: string, value: string) => {
    setParams((prev) => ({ ...prev, [key]: value }))
  }

  const addMock = () => {
    if (!mockNodeId.trim()) return
    try {
      const parsed = mockOutput.trim() ? JSON.parse(mockOutput) : {}
      setMocks((prev) => ({ ...prev, [mockNodeId.trim()]: { output: parsed } }))
      setMockNodeId('')
      setMockOutput('')
    } catch {
      // Invalid JSON, don't add
    }
  }

  const removeMock = (nodeId: string) => {
    setMocks((prev) => {
      const next = { ...prev }
      delete next[nodeId]
      return next
    })
  }

  const handleDryRun = () => {
    if (!spec) return
    dryRunMutation.mutate(
      {
        spec,
        params,
        mocks: Object.keys(mocks).length > 0 ? mocks : undefined,
      },
      {
        onSuccess: (data) => {
          onResult?.(data)
        },
      },
    )
  }

  const nodes = spec?.nodes ?? []

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">

      {/* Parameters */}
      <div>
        <h4 className="mb-1 text-xs font-medium text-gray-500">参数</h4>
        <div className="space-y-1">
          {Object.entries(params).map(([key, value]) => (
            <div key={key} className="flex items-center gap-1">
              <span className="font-mono text-xs text-gray-600">{key}=</span>
              <input
                type="text"
                value={value}
                onChange={(e) => updateParamValue(key, e.target.value)}
                className="flex-1 rounded border border-gray-300 px-2 py-0.5 text-xs"
              />
              <button
                onClick={() => removeParam(key)}
                className="text-gray-400 hover:text-red-500"
              >
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
          <div className="flex items-center gap-1">
            <input
              type="text"
              value={paramKey}
              onChange={(e) => setParamKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addParam()}
              placeholder="参数名"
              className="flex-1 rounded border border-gray-300 px-2 py-0.5 text-xs"
            />
            <button
              onClick={addParam}
              className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
            >
              添加
            </button>
          </div>
        </div>
      </div>

      {/* Mock Output */}
      <div>
        <h4 className="mb-1 text-xs font-medium text-gray-500">模拟输出</h4>
        <div className="space-y-1">
          {Object.keys(mocks).map((nodeId) => (
            <div key={nodeId} className="flex items-center gap-1 text-xs">
              <span className="font-mono text-blue-600">{nodeId}</span>
              <span className="text-gray-400">= mock</span>
              <button
                onClick={() => removeMock(nodeId)}
                className="text-gray-400 hover:text-red-500"
              >
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
          <div className="space-y-1">
            <select
              value={mockNodeId}
              onChange={(e) => setMockNodeId(e.target.value)}
              className="w-full rounded border border-gray-300 px-2 py-0.5 text-xs"
            >
              <option value="">选择节点…</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.title || n.id}
                </option>
              ))}
            </select>
            {mockNodeId && (
              <textarea
                value={mockOutput}
                onChange={(e) => setMockOutput(e.target.value)}
                rows={3}
                placeholder='{"key": "value"}'
                className="w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
              />
            )}
            {mockNodeId && (
              <button
                onClick={addMock}
                className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
              >
                添加模拟
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={handleDryRun}
        disabled={!spec || dryRunMutation.isPending}
        className="w-full rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-green-700 disabled:opacity-40"
      >
        {dryRunMutation.isPending ? '运行中…' : '试运行'}
      </button>

      {/* Error */}
      {dryRunMutation.isError && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-red-700 text-xs">
          {dryRunMutation.error instanceof Error ? dryRunMutation.error.message : '试运行失败'}
        </div>
      )}
    </div>
  )
}