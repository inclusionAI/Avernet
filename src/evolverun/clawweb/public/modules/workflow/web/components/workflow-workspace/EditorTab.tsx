import Editor from '../../pages/Editor'

interface EditorTabProps {
  workflowId: string
}

export default function EditorTab({ workflowId }: EditorTabProps) {
  return (
    <div className="h-full min-h-0 overflow-hidden bg-white">
      <Editor embedded initialWorkflowId={workflowId} />
    </div>
  )
}
