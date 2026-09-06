import { useState, useCallback, useRef } from 'react'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { ChatMessage } from '@avernet/clawweb-shared/web/types'

/** Polling interval for bot responses (ms) */
const POLL_INTERVAL = 2000
/** Max poll attempts before giving up */
const MAX_POLL_ATTEMPTS = 60
/** Max poll attempts for terminal states */
const POLL_DONE = 0

export function useChat(flowId: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** Stop any active polling */
  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  /** Poll for a bot response by messageId */
  const pollForResponse = useCallback(
    (messageId: string, attempt = 0) => {
      if (attempt >= MAX_POLL_ATTEMPTS) {
        // Give up polling — mark message as failed
        setMessages((prev) =>
          prev.map((m) =>
            m.messageId === messageId && m.role === 'bot' && m.pending
              ? { ...m, pending: false, content: '[等待回复超时，请重试]' }
              : m,
          ),
        )
        return
      }

      pollTimerRef.current = setTimeout(async () => {
        try {
          const result = await api.runs.pollMessage(flowId, messageId)

          if (!result.ok || !result.data) {
            // API error — retry
            pollForResponse(messageId, attempt + 1)
            return
          }

          const { messageStatus, result: msgResult } = result.data

          if (messageStatus === 'COMPLETED') {
            const content =
              msgResult?.content ?? '[机器人已完成回复，但无内容]'
            setMessages((prev) =>
              prev.map((m) =>
                m.messageId === messageId && m.role === 'bot' && m.pending
                  ? { ...m, pending: false, content }
                  : m,
              ),
            )
            return
          }

          if (messageStatus === 'FAILED') {
            setMessages((prev) =>
              prev.map((m) =>
                m.messageId === messageId && m.role === 'bot' && m.pending
                  ? { ...m, pending: false, content: '[机器人回复失败]' }
                  : m,
              ),
            )
            return
          }

          // PENDING / RUNNING — keep polling
          pollForResponse(messageId, attempt + 1)
        } catch {
          // Network error — retry
          pollForResponse(messageId, attempt + 1)
        }
      }, POLL_INTERVAL)
    },
    [flowId],
  )

  /** Send a message (free text) and start polling for bot response */
  const sendMessage = useCallback(
    async (content: string, actionLabel?: string) => {
      const trimmed = content.trim()
      if (!trimmed || sending) return

      stopPolling()
      setSending(true)

      // Add user message immediately
      const userMsgId = `user-${Date.now()}`
      const userMsg: ChatMessage = {
        id: userMsgId,
        role: 'user',
        content: trimmed,
        timestamp: Date.now(),
        actionLabel,
      }
      setMessages((prev) => [...prev, userMsg])

      try {
        const result = await api.runs.chat(flowId, trimmed)

        if (!result.ok || !result.messageId) {
          // Add error message from bot
          const errMsg: ChatMessage = {
            id: `bot-err-${Date.now()}`,
            role: 'bot',
            content: `发送失败: ${result.messageId ?? '未知错误'}`,
            timestamp: Date.now(),
          }
          setMessages((prev) => [...prev, errMsg])
          return
        }

        // Add pending bot message placeholder — will be filled by polling
        const botMsg: ChatMessage = {
          id: `bot-${Date.now()}`,
          role: 'bot',
          content: '',
          timestamp: Date.now(),
          messageId: result.messageId,
          pending: true,
        }
        setMessages((prev) => [...prev, botMsg])

        // Start polling for the bot response
        pollForResponse(result.messageId)
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        const errMsg: ChatMessage = {
          id: `bot-err-${Date.now()}`,
          role: 'bot',
          content: `发送失败: ${msg}`,
          timestamp: Date.now(),
        }
        setMessages((prev) => [...prev, errMsg])
      } finally {
        setSending(false)
      }
    },
    [flowId, sending, stopPolling, pollForResponse],
  )

  /** Clear chat history */
  const clearMessages = useCallback(() => {
    stopPolling()
    setMessages([])
  }, [stopPolling])

  return {
    messages,
    sending,
    sendMessage,
    clearMessages,
  }
}