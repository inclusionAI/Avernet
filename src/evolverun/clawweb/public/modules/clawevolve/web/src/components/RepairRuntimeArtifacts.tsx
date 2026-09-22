const labels: Record<string, string> = {
  artifactBundle: '诊断产物',
  runtimeBundle: '运行日志与现场',
  openclawSessions: 'Agent 会话',
}

export default function RepairRuntimeArtifacts({ taskId, stepId, output, canDownload }: {
  taskId: string
  stepId: string
  output?: Record<string, unknown> | null
  canDownload: boolean
}) {
  const names = output?.runtimeArtifactNames
  if (!canDownload || !Array.isArray(names)) return null
  const available = names.filter((name): name is string => typeof name === 'string' && Object.hasOwn(labels, name))
  if (!available.length) return null
  return <div className="mt-2 flex flex-wrap gap-3" aria-label="步骤运行归档">
    {available.map(name => <a key={name} className="text-xs font-medium text-blue-600 hover:text-blue-700"
      href={'/api/repair/v1/tasks/' + encodeURIComponent(taskId) + '/steps/' + encodeURIComponent(stepId)
        + '/runtime-artifacts/' + encodeURIComponent(name)}
      target="_blank" rel="noopener noreferrer">{labels[name]}</a>)}
  </div>
}
