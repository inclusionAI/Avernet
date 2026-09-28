// @vitest-environment jsdom
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const beginApprovalLogin = vi.hoisted(() => vi.fn(() => true))
const clearApprovalLoginAttempt = vi.hoisted(() => vi.fn())

vi.mock('../approval-session-fetch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../approval-session-fetch')>()
  return { ...actual, beginApprovalLogin, clearApprovalLoginAttempt }
})

import Approval from '../Approval'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  beginApprovalLogin.mockClear()
  clearApprovalLoginAttempt.mockClear()
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

  it('keeps the reload sentinel after loading public approval details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        id: 1,
        status: 'pending',
        cardFields: [],
        approverIds: [],
        approverNames: [],
        approvedBy: [],
        rejectedBy: [],
      }),
    } as Response)))

    render(
      <MemoryRouter initialEntries={['/approval/1']}>
        <Routes><Route path='/approval/:id' element={<Approval />} /></Routes>
      </MemoryRouter>,
    )

    await waitFor(() => expect(screen.getByRole('button', { name: '同意' })).toBeInTheDocument())
    expect(clearApprovalLoginAttempt).not.toHaveBeenCalled()
  })

  it('clears the reload sentinel after the authenticated approval action succeeds', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/resolve/session')) {
        return {
          ok: true,
          status: 200,
          headers: new Headers({ 'content-type': 'application/json' }),
          json: async () => ({ ok: true, status: 'approved' }),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({
          id: 1,
          status: 'pending',
          cardFields: [],
          approverIds: [],
          approverNames: [],
          approvedBy: [],
          rejectedBy: [],
        }),
      } as Response
    }))

    render(
      <MemoryRouter initialEntries={['/approval/1']}>
        <Routes><Route path='/approval/:id' element={<Approval />} /></Routes>
      </MemoryRouter>,
    )

    const approveButton = await screen.findByRole('button', { name: '同意' })
    clearApprovalLoginAttempt.mockClear()
    fireEvent.click(approveButton)

    await waitFor(() => expect(clearApprovalLoginAttempt).toHaveBeenCalledTimes(1))
  })
})
