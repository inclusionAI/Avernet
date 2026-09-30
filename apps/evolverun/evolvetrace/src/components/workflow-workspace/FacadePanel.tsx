import type { FacadeBinding } from '../../types'

interface FacadePanelProps {
  workflowId: string
  facades: FacadeBinding[]
  onFacadeChanged?: () => void
}

export default function FacadePanel({ workflowId, facades, onFacadeChanged }: FacadePanelProps) {
  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
      <p>命令面板（占位）</p>
      <p className="mt-1 text-xs text-gray-400">workflowId: {workflowId}</p>
      <p className="text-xs text-gray-400">已绑定命令: {facades.length}</p>
      {onFacadeChanged && <p className="text-xs text-gray-400">变更回调已注册</p>}
    </div>
  )
}
