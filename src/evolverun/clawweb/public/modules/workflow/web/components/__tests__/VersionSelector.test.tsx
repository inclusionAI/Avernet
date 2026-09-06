import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import VersionSelector from '../VersionSelector'

const mocks = vi.hoisted(() => ({
  listVersions: vi.fn(),
}))

vi.mock('@avernet/clawweb-shared/web/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@avernet/clawweb-shared/web/api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflows: {
        ...actual.api.workflows,
        listVersions: mocks.listVersions,
      },
    },
  }
})

describe('VersionSelector', () => {
  beforeEach(() => {
    mocks.listVersions.mockResolvedValue({
      workflowId: 'wf-1',
      versions: [
        { version: 1, deployNumber: 1, tagName: null, isActive: true, gmtCreate: 1700000000 },
        { version: 2, deployNumber: 2, tagName: 'v1.0.0', isActive: false, gmtCreate: 1700000100 },
      ],
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('loads and lists versions with active badge', async () => {
    render(<VersionSelector workflowId="wf-1" />)

    await waitFor(() => {
      expect(mocks.listVersions).toHaveBeenCalledWith('wf-1')
    })

    const select = screen.getByRole('combobox')
    await userEvent.click(select)

    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThanOrEqual(3) // placeholder + 2 versions
    expect(options.some((o) => o.textContent?.includes('v1 #1 — 默认'))).toBe(true)
    expect(options.some((o) => o.textContent?.includes('v2 #2 (v1.0.0)'))).toBe(true)
  })

  it('filters to active version when includeInactive is false', async () => {
    render(<VersionSelector workflowId="wf-1" includeInactive={false} />)

    await waitFor(() => {
      expect(mocks.listVersions).toHaveBeenCalledWith('wf-1')
    })

    const select = screen.getByRole('combobox')
    await userEvent.click(select)

    const options = screen.getAllByRole('option')
    expect(options.some((o) => o.textContent?.includes('v2'))).toBe(false)
    expect(options.some((o) => o.textContent?.includes('v1'))).toBe(true)
  })

  it('calls onChange when a version is selected', async () => {
    const onChange = vi.fn()
    render(<VersionSelector workflowId="wf-1" onChange={onChange} />)

    await waitFor(() => {
      expect(mocks.listVersions).toHaveBeenCalled()
    })

    const select = screen.getByRole('combobox')
    await userEvent.selectOptions(select, '1')
    expect(onChange).toHaveBeenCalledWith(1)
  })

  it('displays an error message when loading fails', async () => {
    mocks.listVersions.mockRejectedValue(new Error('network error'))
    render(<VersionSelector workflowId="wf-1" />)

    await waitFor(() => {
      expect(screen.getByText('network error')).toBeInTheDocument()
    })
  })
})
