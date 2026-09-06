import { describe, it, expect } from 'vitest'
import {
  isWarningsErrorText,
  parseWarningsErrorText,
  getWarningCodeLabel,
} from '../warnings'

describe('isWarningsErrorText', () => {
  it('returns true for [WARNINGS]-prefixed string', () => {
    expect(isWarningsErrorText('[WARNINGS][tool_errors] something')).toBe(true)
  })

  it('returns false for null', () => {
    expect(isWarningsErrorText(null)).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isWarningsErrorText(undefined)).toBe(false)
  })

  it('returns false for empty string', () => {
    expect(isWarningsErrorText('')).toBe(false)
  })

  it('returns false for regular error message', () => {
    expect(isWarningsErrorText('Executor failed with timeout')).toBe(false)
  })

  it('returns false when [WARNINGS] appears but not at start', () => {
    expect(isWarningsErrorText('Error: [WARNINGS] found')).toBe(false)
  })
})

describe('parseWarningsErrorText', () => {
  it('returns empty array for null', () => {
    expect(parseWarningsErrorText(null)).toEqual([])
  })

  it('returns empty array for non-warnings string', () => {
    expect(parseWarningsErrorText('Some normal error')).toEqual([])
  })

  it('parses a single warning without detail', () => {
    const result = parseWarningsErrorText('[WARNINGS][tool_errors] 2 tool calls failed')
    expect(result).toHaveLength(1)
    expect(result[0].code).toBe('tool_errors')
    expect(result[0].message).toBe('2 tool calls failed')
    expect(result[0].detail).toBeUndefined()
  })

  it('parses a single warning with detail', () => {
    const text = '[WARNINGS][session_errors] 3 errors | {"toolErrors":2,"apiErrors":1}'
    const result = parseWarningsErrorText(text)
    expect(result).toHaveLength(1)
    expect(result[0].code).toBe('session_errors')
    expect(result[0].message).toBe('3 errors')
    expect(result[0].detail).toEqual({ toolErrors: 2, apiErrors: 1 })
  })

  it('parses multiple warnings', () => {
    const text = '[WARNINGS][tool_errors] Tool X failed; [WARNINGS][json_repair_needed] JSON was repaired'
    const result = parseWarningsErrorText(text)
    expect(result).toHaveLength(2)
    expect(result[0].code).toBe('tool_errors')
    expect(result[0].message).toBe('Tool X failed')
    expect(result[1].code).toBe('json_repair_needed')
    expect(result[1].message).toBe('JSON was repaired')
  })
})

describe('getWarningCodeLabel', () => {
  it('returns Chinese labels for known codes', () => {
    expect(getWarningCodeLabel('tool_errors')).toBe('工具错误')
    expect(getWarningCodeLabel('partial_output')).toBe('部分输出')
    expect(getWarningCodeLabel('recovered_from_error')).toBe('错误恢复')
    expect(getWarningCodeLabel('json_repair_needed')).toBe('JSON修复')
    expect(getWarningCodeLabel('session_errors')).toBe('会话错误')
  })

  it('returns the code itself for unknown codes', () => {
    expect(getWarningCodeLabel('unknown_code')).toBe('unknown_code')
  })
})