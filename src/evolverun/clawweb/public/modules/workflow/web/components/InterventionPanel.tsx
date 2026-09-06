import { useState, useCallback, useEffect, useRef } from 'react'
import { useInterventions, useIntervene, useUpdateSession } from '@avernet/workflow/web/api/hooks'
import { useChat } from '../hooks/useChat'
import type { InterventionAction, NodeExecution, ChatMessage } from '@avernet/clawweb-shared/web/types'

interface InterventionPanelProps {
  flowId: string
  runStatus: string
  nodes: NodeExecution[]
}

const ACTION_LABELS: Record<InterventionAction, string> = {
  retry: '重试',
  skip: '跳过',
  revise: '修正',
  confirm: '确认',
}

const ACTION_ICONS: Record<InterventionAction, string> = {
  retry: '↻',
  skip: '⏭',
  revise: '✎',
  confirm: '✓',
}

const ACTION_STYLES: Record<InterventionAction, string> = {
  retry: 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100',
  skip: 'border-gray-300 bg-gray-50 text-gray-600 hover:bg-gray-100',
  revise: 'border-blue-300 bg-blue-50 text-blue-700 hover:bg-blue-100',
  confirm: 'border-green-300 bg-green-50 text-green-700 hover:bg-green-100',
}

/** Nodes that are intervenable — failed, blocked, or waiting */
function isIntervenableNode(node: NodeExecution): boolean {
  return node.status === 'failed' || node.status === 'blocked' || node.status === 'waiting'
}

const INTERVENABLE_STATUSES = new Set(['failed', 'blocked', 'waiting'])

export default function InterventionPanel({ flowId, runStatus, nodes }: InterventionPanelProps) {
  const { data: interventionInfo, isLoading } = useInterventions(flowId)
  const interveneMutation = useIntervene(flowId)
  const updateSessionMutation = useUpdateSession(flowId)
  const { messages, sending, sendMessage, clearMessages } = useChat(flowId)

  // Only show intervention panel for intervenable statuses
  if (!INTERVENABLE_STATUSES.has(runStatus)) {
    return null
  }

  return (
    <ChatInterventionPanel
      flowId={flowId}
      runStatus={runStatus}
      nodes={nodes}
      interventionInfo={interventionInfo}
      isLoading={isLoading}
      interveneMutation={interveneMutation}
      updateSessionMutation={updateSessionMutation}
      messages={messages}
      sending={sending}
      sendMessage={sendMessage}
      clearMessages={clearMessages}
    />
  )
}

/** Inner panel that has all the data loaded */
interface ChatPanelProps {
  flowId: string
  runStatus: string
  nodes: NodeExecution[]
  interventionInfo: {
    originBotId?: string | null
    originSessionKey?: string | null
    originSessionId?: string | null
    availableInterventions?: InterventionAction[]
    interventionReady?: boolean
  } | undefined
  isLoading: boolean
  interveneMutation: ReturnType<typeof useIntervene>
  updateSessionMutation: ReturnType<typeof useUpdateSession>
  messages: ChatMessage[]
  sending: boolean
  sendMessage: (content: string, actionLabel?: string) => Promise<void>
  clearMessages: () => void
}

function ChatInterventionPanel({
  flowId,
  runStatus,
  nodes,
  interventionInfo,
  isLoading,
  interveneMutation,
  updateSessionMutation,
  messages,
  sending,
  sendMessage,
  clearMessages,
}: ChatPanelProps) {
  // Session info form state
  const [editingSession, setEditingSession] = useState(false)
  const [botId, setBotId] = useState('')
  const [sessionKey, setSessionKey] = useState('')
  const [sessionId, setSessionId] = useState('')

  // Node selector
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  // Chat input
  const [inputText, setInputText] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)

  // Sync session info from API
  useEffect(() => {
    if (interventionInfo && !editingSession) {
      setBotId(interventionInfo.originBotId ?? '')
      setSessionKey(interventionInfo.originSessionKey ?? '')
      setSessionId(interventionInfo.originSessionId ?? '')
    }
  }, [interventionInfo, editingSession])

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const intervenableNodes = nodes.filter(isIntervenableNode)
  const availableActions = interventionInfo?.availableInterventions ?? []
  const hasBotId = !!botId.trim()
  const hasSessionKey = !!sessionKey.trim()
  const sessionReady = hasBotId && hasSessionKey

  // Save session info
  const handleSaveSession = useCallback(async () => {
    try {
      await updateSessionMutation.mutateAsync({
        originBotId: botId.trim() || null,
        originSessionKey: sessionKey.trim() || null,
        originSessionId: sessionId.trim() || null,
      })
      setEditingSession(false)
    } catch {
      // Error handled by mutation
    }
  }, [botId, sessionKey, sessionId, updateSessionMutation])

  // Execute intervention action via chat
  const handleActionClick = useCallback(
    async (action: InterventionAction) => {
      const selectedNode = selectedNodeId
        ? nodes.find((n) => n.node_id === selectedNodeId)
        : undefined

      // For skip/revise with no reason, prompt inline
      if (action === 'skip' || action === 'revise') {
        const prefix = buildActionCommand(action, selectedNode?.node_id)
        setInputText(prefix)
        return
      }

      // For retry/confirm, send directly
      const command = buildActionCommand(action, selectedNode?.node_id)
      await sendMessage(command, ACTION_LABELS[action])
    },
    [selectedNodeId, nodes, sendMessage],
  )

  // Send chat message (Enter key or send button)
  const handleSend = useCallback(async () => {
    const text = inputText.trim()
    if (!text || sending) return
    setInputText('')
    await sendMessage(text)
  }, [inputText, sending, sendMessage])

  // Handle Enter key
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        void handleSend()
      }
    },
    [handleSend],
  )

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-center gap-2 text-gray-500 text-sm">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          加载干预信息...
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-base">🛠</span>
          <h3 className="font-medium text-gray-900 text-sm">人工干预对话</h3>
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700 text-xs font-medium">
            {runStatus}
          </span>
          {sessionReady ? (
            <span className="rounded-full bg-green-100 px-1.5 py-0.5 text-green-700 text-[10px] font-medium">会话就绪</span>
          ) : (
            <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-red-700 text-[10px] font-medium">会话未配置</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              onClick={clearMessages}
              className="text-xs text-gray-400 hover:text-gray-600"
              title="清空对话"
            >
              清空
            </button>
          )}
          <button
            onClick={() => setEditingSession(!editingSession)}
            className="text-xs text-blue-600 hover:text-blue-800"
          >
            {editingSession ? '收起' : '会话设置'}
          </button>
        </div>
      </div>

      {/* BaaS Session Info — collapsible */}
      {editingSession && (
        <div className="border-b border-gray-100 bg-gray-50 px-4 py-3">
          <div className="mb-2 text-xs font-medium text-gray-600">
            📡 BaaS 会话信息
          </div>
          <div className="space-y-2">
            <div>
              <label className="mb-0.5 block text-[11px] text-gray-500">originBotId</label>
              <input
                type="text"
                value={botId}
                onChange={(e) => setBotId(e.target.value)}
                placeholder="例如: default:151614"
                className="w-full rounded border border-gray-300 bg-white px-2 py-1 font-mono text-xs focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
            <div>
              <label className="mb-0.5 block text-[11px] text-gray-500">originSessionKey</label>
              <input
                type="text"
                value={sessionKey}
                onChange={(e) => setSessionKey(e.target.value)}
                placeholder="会话标识 key"
                className="w-full rounded border border-gray-300 bg-white px-2 py-1 font-mono text-xs focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
            <div>
              <label className="mb-0.5 block text-[11px] text-gray-500">originSessionId</label>
              <input
                type="text"
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                placeholder="会话 ID (可选)"
                className="w-full rounded border border-gray-300 bg-white px-2 py-1 font-mono text-xs focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => void handleSaveSession()}
                disabled={updateSessionMutation.isPending}
                className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {updateSessionMutation.isPending ? '保存中...' : '保存'}
              </button>
              <button
                onClick={() => {
                  setEditingSession(false)
                  setBotId(interventionInfo?.originBotId ?? '')
                  setSessionKey(interventionInfo?.originSessionKey ?? '')
                  setSessionId(interventionInfo?.originSessionId ?? '')
                }}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-xs text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Session not configured hint */}
      {!sessionReady && (
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="flex items-center gap-2 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-amber-700 text-xs">
            <span>⚠</span>
            <span>请先配置 BaaS 会话信息（点击「会话设置」设置 Bot ID + Session Key）后才能发送消息</span>
          </div>
        </div>
      )}

      {/* Node selector for multi-node scenarios */}
      {sessionReady && intervenableNodes.length > 1 && (
        <div className="border-b border-gray-100 px-4 py-2">
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-gray-500 shrink-0">目标节点:</label>
            <select
              value={selectedNodeId ?? ''}
              onChange={(e) => setSelectedNodeId(e.target.value || null)}
              className="flex-1 rounded border border-gray-200 bg-white px-2 py-1 text-xs focus:border-blue-400 focus:outline-none"
            >
              <option value="">流程级别</option>
              {intervenableNodes.map((node) => (
                <option key={node.node_id} value={node.node_id}>
                  {node.node_title ?? node.node_id} ({node.status})
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Chat messages area */}
      <div className="max-h-72 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && sessionReady && (
          <div className="text-center text-gray-400 text-xs py-6">
            点击下方操作按钮或输入消息开始对话
          </div>
        )}
        {messages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} />
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* Action buttons */}
      {sessionReady && availableActions.length > 0 && (
        <div className="border-t border-gray-100 px-4 py-2">
          <div className="flex flex-wrap gap-1.5">
            {availableActions.map((action) => (
              <button
                key={action}
                onClick={() => void handleActionClick(action)}
                disabled={sending}
                className={`inline-flex items-center gap-1 rounded border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${ACTION_STYLES[action]}`}
              >
                <span>{ACTION_ICONS[action]}</span>
                {ACTION_LABELS[action]}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      {sessionReady && (
        <div className="border-t border-gray-100 px-4 py-2.5">
          <div className="flex items-end gap-2">
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={sending ? '等待回复中...' : '输入消息，回车发送...'}
              disabled={sending}
              rows={1}
              className="flex-1 resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
            />
            <button
              onClick={() => void handleSend()}
              disabled={sending || !inputText.trim()}
              className="shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sending ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : (
                '发送'
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** Single chat message bubble */
function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
          isUser
            ? 'bg-blue-600 text-white'
            : message.pending
              ? 'bg-gray-100 text-gray-500 border border-gray-200'
              : 'bg-gray-100 text-gray-800 border border-gray-200'
        }`}
      >
        {/* Action label badge */}
        {message.actionLabel && (
          <span className="mb-1 inline-block rounded bg-white/20 px-1.5 py-0.5 text-[10px] font-medium">
            {message.actionLabel}
          </span>
        )}
        {/* Content */}
        {message.pending ? (
          <div className="flex items-center gap-2">
            <div className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
            <span>等待回复...</span>
          </div>
        ) : (
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        )}
        {/* Timestamp */}
        <div
          className={`mt-1 text-[10px] ${
            isUser ? 'text-blue-200' : 'text-gray-400'
          }`}
        >
          {formatTime(message.timestamp)}
        </div>
      </div>
    </div>
  )
}

/** Build a /workflow command string from action + optional nodeId */
function buildActionCommand(action: InterventionAction, nodeId?: string): string {
  switch (action) {
    case 'retry':
      return `/workflow retry${nodeId ? ` --node ${nodeId}` : ''}`
    case 'skip':
      return `/workflow skip${nodeId ? ` --node ${nodeId}` : ''} --reason "`
    case 'revise':
      return `/workflow revise${nodeId ? ` --node ${nodeId}` : ''} "`
    case 'confirm':
      return '/workflow confirm'
  }
}

/** Format timestamp to HH:MM */
function formatTime(ts: number): string {
  const d = new Date(ts)
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}