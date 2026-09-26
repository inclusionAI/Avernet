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

const EXECUTOR_ACCENTS: Record<ExecutorType, string> = {
  'embedded-agent': 'bg-blue-500', action: 'bg-blue-500', human: 'bg-amber-500',
  'loop-group': 'bg-slate-500', collaboration: 'bg-blue-500', done: 'bg-slate-400',
  subagent: 'bg-blue-500', 'bcs-route': 'bg-slate-500', 'baas-call': 'bg-blue-500',
  'mcp-call': 'bg-blue-500', 'cli-script': 'bg-slate-500', subworkflow: 'bg-slate-500', approval: 'bg-amber-500',
}

export default function NodePalette() {
  const handleDragStart = (e: React.DragEvent, executorType: ExecutorType) => {
    e.dataTransfer.setData('application/clawflow-node-type', executorType)
    e.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <div className="w-48 shrink-0 overflow-y-auto border-l border-slate-200 bg-slate-50/70 p-3">
      <h3 className="mb-3 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">
        节点类型
      </h3>
      <div className="space-y-1.5">
        {EXECUTOR_TYPES.map((type) => (
          <div
            key={type}
            draggable
            onDragStart={(e) => handleDragStart(e, type)}
            className="cursor-grab rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-slate-700 transition hover:border-blue-300 hover:shadow-sm active:cursor-grabbing"
          >
            <div className="flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${EXECUTOR_ACCENTS[type]}`} /><span className="text-xs font-medium">{type}</span></div>
            <div className="mt-0.5 pl-3.5 text-[10px] text-slate-400">{EXECUTOR_DESCRIPTIONS[type]}</div>
          </div>
        ))}
      </div>
      <div className="mt-4 border-t border-gray-100 pt-3">
        <p className="text-[10px] leading-4 text-slate-400">拖拽节点类型到画布上添加节点</p>
      </div>
    </div>
  )
}
