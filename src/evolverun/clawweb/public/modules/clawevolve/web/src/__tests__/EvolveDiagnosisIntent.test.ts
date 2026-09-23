import { describe, expect, it } from 'vitest'

import { buildDiagnoseIntent } from '../pages/Evolve'

const baseInput = {
  lookbackDays: 3,
  startDate: '2026-09-20',
  endDate: '2026-09-23',
  useDateRange: true,
  badCaseCount: 4,
  goodCaseCount: 1,
  focusIssue: '工具调用失败和任务未完成',
}

describe('buildDiagnoseIntent', () => {
  it('keeps time and case quotas in time-range mode', () => {
    expect(buildDiagnoseIntent({ ...baseInput, explicitSessions: false })).toBe(
      '扫描2026-09-20 至 2026-09-23的历史 session；抽取4个 bad case 和1个 good case；重点关注工具调用失败和任务未完成。',
    )
  })

  it('omits time and case quotas in explicit-session mode', () => {
    const intent = buildDiagnoseIntent({ ...baseInput, explicitSessions: true })

    expect(intent).toBe('诊断指定的历史 session；重点关注工具调用失败和任务未完成。')
    expect(intent).not.toContain('2026-09-20')
    expect(intent).not.toContain('bad case')
  })
})
