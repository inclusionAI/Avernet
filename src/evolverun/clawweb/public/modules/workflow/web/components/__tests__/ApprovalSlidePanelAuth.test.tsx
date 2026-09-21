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

it('uses a fresh DingTalk auth code instead of the browser user id', async () => {
  const requests: Array<{ url: string; body?: Record<string, unknown> }> = []
  window.dd = {
    ready: (callback) => callback(),
    requestAuthCode: ({ success }) => success({ code: 'panel-auth-code' }),
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    requests.push({ url, body })
    if (url.endsWith('/auth/dingtalk/config')) {
      return { ok: true, json: async () => ({ clientId: 'app-key', corpId: 'ding-corp' }) }
    }
    if (url.endsWith('/resolve')) {
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

  const resolveRequest = requests.find((request) => request.url.endsWith('/resolve'))
  expect(resolveRequest?.body).toEqual({ authCode: 'panel-auth-code', action: 'approve' })
  expect(requests.some((request) => request.url.includes('empId='))).toBe(false)
})
