// @vitest-environment jsdom
import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { EvolveStep } from '../../api/client'
import StageExtensionResultDetail from '../StageExtensionResultDetail'

vi.stubGlobal('React', React)
afterEach(cleanup)

it('renders every custom Stage summary as Markdown with generic identity metadata', () => {
  const step = {
    output: {
      summary: '## Result\nCompleted safely.',
      changed_files: ['SKILL.md', 'references/rules.md'],
    },
  } as EvolveStep
  render(<StageExtensionResultDetail step={step} title="质量检查" stageLabel="Skill 加固" modeLabel="整体替换" />)
  expect(screen.getByRole('heading', { name: '质量检查' })).toBeTruthy()
  expect(screen.getByText('自定义 Stage')).toBeTruthy()
  expect(screen.getByText('Skill 加固 · 整体替换')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Result' })).toBeTruthy()
  expect(screen.getByText('SKILL.md')).toBeTruthy()
  expect(screen.getByText('references/rules.md')).toBeTruthy()
})

it('does not repeat changed files when the Markdown summary already contains that section', () => {
  const step = {
    output: {
      summary: '## 变更文件\n- `SKILL.md`',
      changed_files: ['SKILL.md'],
    },
  } as EvolveStep
  render(<StageExtensionResultDetail step={step} title="97 Skill 加固" />)
  expect(screen.getAllByRole('heading', { name: '变更文件' })).toHaveLength(1)
})
