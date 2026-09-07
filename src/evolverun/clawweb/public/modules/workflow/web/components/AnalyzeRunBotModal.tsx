import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useEligibleBotsForAnalyze } from '@avernet/workflow/web/api/hooks'

interface AnalyzeRunBotModalProps {
  flowId: string
  workflowId: string
  originBotId?: string | null
  analyzeMutation: {
    isPending: boolean
    variables?: { flowId?: string; botId?: string; botEnv?: string } | null
    mutate: (input: { flowId: string; botId: string; botEnv?: string }, options?: { onSuccess?: () => void }) => void
  }
  isOpen: boolean
  onClose: () => void
}

export default function AnalyzeRunBotModal({
  flowId,
  workflowId,
  originBotId,
  analyzeMutation,
  isOpen,
  onClose,
}: AnalyzeRunBotModalProps) {
  const { data, isLoading, error } = useEligibleBotsForAnalyze(workflowId)
  const [selectedBotId, setSelectedBotId] = useState<string>('')

  const bots = data?.bots ?? []
  const selectedBot = bots.find((b) => b.botId === selectedBotId)

  useEffect(() => {
    if (isOpen) {
      if (bots.length > 0) {
        const origin = originBotId ? bots.find((b) => b.botId === originBotId) : undefined
        setSelectedBotId(origin?.botId ?? bots[0].botId)
      } else {
        setSelectedBotId('')
      }
    }
  }, [isOpen, bots, originBotId])

  const handleConfirm = () => {
    if (!selectedBotId) return
    analyzeMutation.mutate(
      {
        flowId,
        botId: selectedBotId,
        botEnv: selectedBot?.env ?? undefined,
      },
      { onSuccess: onClose },
    )
  }

  if (!isOpen) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-3 text-sm font-semibold text-gray-900">选择 Bot 分析运行</h3>
        <p className="mb-3 text-xs text-amber-700">
          选择有权限的 Bot 对该运行进行进化分析。
        </p>

        {isLoading && <div className="py-4 text-xs text-gray-500">加载可用 Bot 中...</div>}

        {!isLoading && error && (
          <div className="py-3 text-xs text-red-600">
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}

        {!isLoading && bots.length === 0 && (
          <div className="py-3 text-xs text-gray-500">
            没有可用的分析 Bot。
          </div>
        )}

        {!isLoading && bots.length > 0 && (
          <div className="mb-4 space-y-2">
            {bots.map((bot) => (
              <label
                key={bot.botId}
                className="flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 p-2 hover:bg-gray-50"
              >
                <input
                  type="radio"
                  name="analyze-bot"
                  value={bot.botId}
                  checked={selectedBotId === bot.botId}
                  onChange={() => setSelectedBotId(bot.botId)}
                  disabled={analyzeMutation.isPending}
                  className="text-blue-600"
                />
                <div className="text-xs">
                  <div className="font-medium text-gray-900">
                    {bot.botName ?? bot.botId}
                    {originBotId && bot.botId === originBotId && (
                      <span className="ml-2 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] text-blue-600">
                        发起 Bot
                      </span>
                    )}
                  </div>
                  <div className="text-gray-500">
                    {bot.botId}
                    {bot.env ? ` · ${bot.env}` : ''}
                  </div>
                </div>
              </label>
            ))}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={analyzeMutation.isPending}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!selectedBotId || analyzeMutation.isPending || bots.length === 0}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {analyzeMutation.isPending ? '分析中...' : '确认分析'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
