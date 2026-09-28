// @vitest-environment jsdom
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const beginApprovalLogin = vi.hoisted(() => vi.fn(() => true))

vi.mock('../approval-session-fetch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../approval-session-fetch')>()
  return { ...actual, beginApprovalLogin }
})

import Approval from '../Approval'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  beginApprovalLogin.mockClear()
})

describe('approval session login', () => {
  it('starts the IAM login handoff when the approval API returns USER_NOT_LOGIN', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ actionType: 'LOGIN', buserviceErrorCode: 'USER_NOT_LOGIN' }),
    } as Response)))

    render(
      <MemoryRouter initialEntries={['/approval/1?empId=legacy-reviewer&corpId=legacy-corp']}>
        <Routes><Route path='/approval/:id' element={<Approval />} /></Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(beginApprovalLogin).toHaveBeenCalledTimes(1))
  })
})
