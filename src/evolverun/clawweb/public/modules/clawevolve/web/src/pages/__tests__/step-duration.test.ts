import { expect, it, vi, afterEach } from 'vitest'
import { stepDuration } from '../evolve/helpers'
import type { EvolveStep } from '../../api/client'
afterEach(() => vi.useRealTimers())
const duration = (startedAt: unknown, completedAt: unknown, gmtCreate: unknown = null) =>
  stepDuration({ startedAt, completedAt, gmtCreate } as EvolveStep)
it.each([
  [1790384400, 1790384490, '1m'],
  [1790384400000, 1790384490000, '1m'],
  ['2026-09-26T01:00:00.000Z', '2026-09-26T01:01:30.000Z', '1m'],
  ['2026-09-26T01:00:00.000Z', 1790384490, '1m'],
  ['bad', 'bad', '等待启动'],
  [1790384400, 'bad', '—'],
  [1790384400, 1790384300, '0s'],
])('normalizes timestamps %s to %s', (start, end, expected) => {
  expect(duration(start, end)).toBe(expected)
})
it('uses creation time while start is absent and current time while unfinished', () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-26T01:00:15.000Z'))
  expect(duration(null, null, '2026-09-26T01:00:00.000Z')).toBe('15s')
})
