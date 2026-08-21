interface EditorTabProps {
  workflowId: string
}

export default function EditorTab({ workflowId }: EditorTabProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm">
      <h3 className="text-lg font-medium text-gray-700">工作流编辑器</h3>
      <p className="mt-1 text-sm text-gray-400">
        当前工作流 ID: <span className="font-mono text-xs">{workflowId}</span>
      </p>
      <p className="mt-4 max-w-md text-xs text-gray-400">
        旧版 Editor 页面未迁移到当前项目。编辑器功能需要在目标仓库中实现。
      </p>
    </div>
  )
}
