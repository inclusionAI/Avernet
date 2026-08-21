interface HttpCallbackPanelProps {
  workflowId: string
}

export default function HttpCallbackPanel({ workflowId }: HttpCallbackPanelProps) {
  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
      <p>HTTP 回调面板（占位）</p>
      <p className="mt-1 text-xs text-gray-400">workflowId: {workflowId}</p>
    </div>
  )
}
