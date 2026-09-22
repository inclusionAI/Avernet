// @vitest-environment jsdom
import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import ApprovalSlidePanel from '../ApprovalSlidePanel'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  delete window.dd
})

it('uses the server-verified ClawWeb session without requesting DingTalk auth', async () => {
  const requests: Array<{ url: string; body?: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    requests.push({ url, body })
    if (url.endsWith('/resolve/session')) {
      return { ok: true, json: async () => ({ ok: true, status: 'approved' }) }
    }
    return {
      ok: true,
      json: async () => ({
        id: 1,
        flowId: 'flow-1',
        nodeId: 'review',
        workflowId: 'test',
        workflowTitle: '审核流程',
        approvalType: 'HUMAN_CONFIRM',
        message: '人工确认',
        cardFields: [],
        approverIds: ['reviewer'],
        approverNames: ['审核人'],
        approvalPolicy: 'any',
        approvedBy: [],
        rejectedBy: [],
        status: 'pending',
        deliveryMode: 'card-web',
        createdAt: 100,
        resolvedAt: null,
      }),
    }
  }))

  render(<ApprovalSlidePanel
    card={{ id: 1 } as never}
    run={{ flow_id: 'flow-1', workflow_id: 'test', workflow_title: '审核流程', status: 'running' } as never}
    onClose={() => undefined}
  />)
  await userEvent.click(await screen.findByRole('button', { name: '通过' }))

  const resolveRequest = requests.find((request) => request.url.endsWith('/resolve/session'))
  expect(resolveRequest?.body).toEqual({ action: 'approve' })
  expect(requests.some((request) => request.url.endsWith('/auth/dingtalk/config'))).toBe(false)
  expect(requests.some((request) => request.url.includes('empId='))).toBe(false)
})
