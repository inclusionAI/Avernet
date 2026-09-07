import { useState, useCallback } from 'react'

interface NodeOutputViewerProps {
  nodeId: string
  label: string
  data: string | null
  isTruncated?: boolean
  onLoadFull?: () => Promise<string>
}

export default function NodeOutputViewer({
  nodeId,
  label,
  data,
  isTruncated = false,
  onLoadFull,
}: NodeOutputViewerProps) {
  const [expanded, setExpanded] = useState(false)
  const [fullData, setFullData] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleExpand = useCallback(() => {
    setExpanded((prev) => !prev)
  }, [])

  const handleLoadFull = useCallback(async () => {
    if (!onLoadFull) return
    setLoading(true)
    try {
      const result = await onLoadFull()
      setFullData(result)
    } catch {
      // Keep showing truncated data
    } finally {
      setLoading(false)
    }
  }, [onLoadFull])

  if (!data) {
    return (
      <div className="rounded-md bg-gray-50 px-3 py-2 text-gray-400 text-xs italic">
        暂无{label}数据
      </div>
    )
  }

  const displayData = fullData ?? data
  const formatted = prettyPrint(displayData)

  return (
    <div className="rounded-md border border-gray-200">
      <button
        onClick={handleExpand}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
      >
        <span>{label}</span>
        <svg
          className={`h-4 w-4 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-gray-200">
          <div className="flex items-center justify-between bg-gray-50 px-3 py-1">
            <span className="text-gray-400 text-xs">
              {formatted.length > 10240
                ? `${(formatted.length / 1024).toFixed(1)} KB`
                : `${formatted.length} 字符`}
            </span>
            <div className="flex gap-2">
              {isTruncated && !fullData && onLoadFull && (
                <button
                  onClick={handleLoadFull}
                  disabled={loading}
                  className="text-xs text-blue-600 hover:text-blue-800 disabled:text-gray-400"
                >
                  {loading ? '加载中…' : '加载完整内容'}
                </button>
              )}
              <CopyButton text={formatted} />
            </div>
          </div>
          <pre className="max-h-80 overflow-auto bg-white p-3 font-mono text-xs leading-relaxed text-gray-800">
            {formatted}
          </pre>
        </div>
      )}
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API not available
    }
  }, [text])

  return (
    <button
      onClick={handleCopy}
      className="text-xs text-blue-600 hover:text-blue-800"
    >
      {copied ? '已复制!' : '复制'}
    </button>
  )
}

function prettyPrint(data: string): string {
  try {
    const parsed = JSON.parse(data)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return data
  }
}