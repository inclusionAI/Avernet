import { EXECUTOR_TYPES, type ExecutorType } from '../editor/store'

const EXECUTOR_DESCRIPTIONS: Record<ExecutorType, string> = {
  'embedded-agent': '带提示词的AI代理',
  action: '外部动作执行',
  human: '人工审批/输入节点',
  'loop-group': '循环节点组',
  collaboration: '多代理协作',
  done: '终止/完成节点',
  subagent: '委托给子代理技能',
  'bcs-route': 'BCS服务路由',
  'baas-call': 'BaaS Bot API调用',
  'mcp-call': 'MCP工具调用',
  'cli-script': '运行CLI脚本/命令',
  subworkflow: '调用其他工作流',
  approval: '审批门控节点',
}

const EXECUTOR_COLORS: Record<ExecutorType, string> = {
  'embedded-agent': 'border-blue-300 bg-blue-50 text-blue-700',
  action: 'border-purple-300 bg-purple-50 text-purple-700',
  human: 'border-amber-300 bg-amber-50 text-amber-700',
  'loop-group': 'border-teal-300 bg-teal-50 text-teal-700',
  collaboration: 'border-rose-300 bg-rose-50 text-rose-700',
  done: 'border-gray-300 bg-gray-50 text-gray-700',
  subagent: 'border-indigo-300 bg-indigo-50 text-indigo-700',
  'bcs-route': 'border-cyan-300 bg-cyan-50 text-cyan-700',
  'baas-call': 'border-emerald-300 bg-emerald-50 text-emerald-700',
  'mcp-call': 'border-violet-300 bg-violet-50 text-violet-700',
  'cli-script': 'border-lime-300 bg-lime-50 text-lime-700',
  subworkflow: 'border-orange-300 bg-orange-50 text-orange-700',
  approval: 'border-pink-300 bg-pink-50 text-pink-700',
}

export default function NodePalette() {
  const handleDragStart = (e: React.DragEvent, executorType: ExecutorType) => {
    e.dataTransfer.setData('application/clawflow-node-type', executorType)
    e.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <div className="w-52 shrink-0 border-l border-gray-200 bg-white p-3">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
        节点类型
      </h3>
      <div className="space-y-2">
        {EXECUTOR_TYPES.map((type) => (
          <div
            key={type}
            draggable
            onDragStart={(e) => handleDragStart(e, type)}
            className={`cursor-grab rounded-md border px-3 py-2 transition-shadow hover:shadow-sm active:cursor-grabbing ${EXECUTOR_COLORS[type]}`}
          >
            <div className="text-sm font-medium">{type}</div>
            <div className="text-xs opacity-70">{EXECUTOR_DESCRIPTIONS[type]}</div>
          </div>
        ))}
      </div>
      <div className="mt-4 border-t border-gray-100 pt-3">
        <p className="text-gray-400 text-xs">拖拽节点类型到画布上以添加节点</p>
      </div>
    </div>
  )
}