import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FailureTaskDetail, FailureTaskIndex, TimelineBlockDetail, TimelineBlockSummary } from '../../../types/insight'
import FailureTaskDrawer from '../FailureTaskDrawer'

const mocks = vi.hoisted(() => ({
  failureTaskDetail: vi.fn(),
  timeline: vi.fn(),
}))

vi.mock('../../../api/insight', () => ({
  insightApi: mocks,
}))

const task: FailureTaskIndex = {
  sourceDt: '20260817',
  ownerUserId: 'dev_local',
  botId: 'bot-1',
  botName: '测试 Bot',
  sessionId: 'session-1',
  taskIndex: 0,
  taskDescription: '验证 Agent 回复原始消息展示',
  isComplete: 0,
  failureClass: 'TOOL_FAILURE',
  judgeReasonSummary: '工具调用失败',
  sessionStartTime: null,
  sessionEndTime: null,
  sessionDurationSeconds: null,
  isCron: false,
  dataAsOf: '2026-08-17T00:00:00Z',
}

const summary: TimelineBlockSummary = {
  blockId: 'message:147',
  kind: 'assistant_message',
  messageIndex: 147,
  role: 'assistant',
  timestamp: 1780971993741,
  visibility: 'visible',
  title: 'Agent 回复',
  preview: '查看原始消息字段',
  charCount: 180,
  expandable: true,
}

const raw = {
  content: [{ arguments: { action: 'poll', sessionId: 'clear-mist', timeout: 20000 }, name: 'process' }],
  idx: 156,
  model: 'Kimi-K2.5',
  role: 'assistant',
  stopReason: 'toolUse',
  timestamp: 1780971993741,
}

const block: TimelineBlockDetail = {
  ...summary,
  content: "[tool_call:process] {'action': 'poll', 'sessionId': 'clear-mist', 'timeout': 20000}",
  raw,
}

const detail: FailureTaskDetail = {
  contractVersion: 'insight/v1',
  dataAsOf: task.dataAsOf,
  sourceBatchId: 'batch-1',
  task,
  session: {
    sessionId: task.sessionId,
    userId: task.ownerUserId,
    botId: task.botId,
    botName: task.botName,
    startTime: null,
    endTime: null,
    durationSeconds: null,
    isCron: false,
    messageCount: 1,
  },
  sessionTasks: [{
    taskIndex: 0,
    taskDescription: task.taskDescription,
    messageRange: [147, 148],
    isComplete: 0,
    failureClass: task.failureClass,
  }],
  judge: {
    task_index: 0,
    task_description: task.taskDescription,
    message_range: [147, 148],
    is_complete: 0,
    reasoning: '工具调用失败',
    task_failure_class: task.failureClass,
  },
  evidence: {
    schemaVersion: 'session-evidence/v1',
    batchId: 'batch-1',
    generatedAt: task.dataAsOf,
    etag: 'etag-1',
    versionId: null,
  },
  timeline: { totalBlocks: 1, blocks: [summary] },
}

describe('FailureTaskDrawer', () => {
  it('shows Agent replies as raw JSON without an original-message disclosure', async () => {
    mocks.failureTaskDetail.mockResolvedValue(detail)
    mocks.timeline.mockImplementation(async (_sessionId: string, _taskIndex: number, params: { blockId?: string }) => ({
      contractVersion: 'insight/v1',
      dataAsOf: task.dataAsOf,
      sourceBatchId: 'batch-1',
      task: { sessionId: task.sessionId, taskIndex: 0, messageRange: [147, 148] },
      items: params.blockId ? [block] : [summary],
      nextCursor: null,
    }))

    render(<FailureTaskDrawer task={task} onClose={vi.fn()} />)

    const blockId = await screen.findByText('message:147')
    await userEvent.click(blockId.closest('button')!)

    await waitFor(() => expect(screen.getByText(/"sessionId": "clear-mist"/)).toBeInTheDocument())
    expect(screen.queryByText(/\[tool_call:process\]/)).not.toBeInTheDocument()
    expect(screen.queryByText(/查看原始消息/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /全屏阅读/ }))
    expect(screen.queryByText(/查看原始消息/)).not.toBeInTheDocument()
  })
})
