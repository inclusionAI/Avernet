// @vitest-environment jsdom
import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { EvolveStep } from '../../api/client'
import StageExtensionResultDetail from '../StageExtensionResultDetail'

vi.stubGlobal('React', React)
afterEach(cleanup)

it('renders a reusable structured Stage result without knowing host semantics', () => {
  const step = {
    output: {
      summary: '## Result\nCompleted safely.',
      changed_files: ['SKILL.md', 'references/rules.md'],
    },
  } as EvolveStep
  render(<StageExtensionResultDetail step={step} title="Host result" />)
  expect(screen.getByRole('heading', { name: 'Host result' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Result' })).toBeTruthy()
  expect(screen.getByText('SKILL.md')).toBeTruthy()
  expect(screen.getByText('references/rules.md')).toBeTruthy()
})
