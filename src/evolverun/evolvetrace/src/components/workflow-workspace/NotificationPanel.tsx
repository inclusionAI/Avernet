interface NotificationPanelProps {
  workflowId: string
}

export default function NotificationPanel({ workflowId }: NotificationPanelProps) {
  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
      <p>通知配置面板（占位）</p>
      <p className="mt-1 text-xs text-gray-400">workflowId: {workflowId}</p>
    </div>
  )
}
