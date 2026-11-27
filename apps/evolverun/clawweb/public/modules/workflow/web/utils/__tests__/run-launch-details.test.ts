import { describe, expect, it } from 'vitest'
import { getRunLaunchDetails } from '../run-launch-details'

describe('getRunLaunchDetails', () => {
  it('uses immutable launch params rather than retry-changed input params', () => {
    const details = getRunLaunchDetails({
      params_json: JSON.stringify({ target: 'original', attempt: 1 }),
      input_json: JSON.stringify({ message: '用户的要求', params: { target: 'retry', attempt: 4 } }),
    })
    expect(JSON.parse(details.params.text!)).toEqual({ target: 'original', attempt: 1 })
    expect(details.input.text).toBe('用户的要求')
    expect(details.input.source).toBe('flow_runs.input_json · message')
    expect(JSON.stringify(details)).not.toContain('retry')
  })

  it('never labels input.params as original even if params_json is absent', () => {
    const details = getRunLaunchDetails({ input_json: '{"params":{"target":"retry"}}' })
    expect(details.params.text).toBeNull()
    expect(details.input.text).toBeNull()
  })

  it('prefers message and falls back to a clearly labeled stored command', () => {
    const raw = { message: '原始需求', command: '/workflow start --id abc' }
    expect(getRunLaunchDetails({ input_json: JSON.stringify(raw) }).input.text).toBe('原始需求')
    expect(getRunLaunchDetails({ input_json: JSON.stringify({ ...raw, message: '  ' }) }).input).toMatchObject({
      label: '已存储命令', source: 'flow_runs.input_json · command', text: raw.command,
    })
  })

  it('does not turn internal bookkeeping into user input', () => {
    const details = getRunLaunchDetails({ input_json: JSON.stringify({
      digest: 'internal-digest', userId: 'internal-user', userName: 'internal-name',
      hasMessage: true, fileCount: 5, params: { credential: 'retry-value' },
    }) })
    expect(details.input.text).toBeNull()
    expect(JSON.stringify(details)).not.toMatch(/internal-|retry-value/)
  })

  it.each([undefined, null, '', '  ', 'null'])('treats %s as not recorded', raw => {
    const details = getRunLaunchDetails({ input_json: raw, params_json: raw })
    expect(details.input.text).toBeNull()
    expect(details.params.text).toBeNull()
  })

  it.each(['{"message":', 'plain non-JSON text', '{"params":{"secret":"value"}'])('handles malformed records without dumping %s', raw => {
    const details = getRunLaunchDetails({ input_json: raw, params_json: raw })
    expect(details.input.text).toBeNull()
    expect(details.input.note).toContain('格式异常')
    expect(details.params.text).toBeNull()
    expect(details.params.note).toContain('格式异常')
  })

  it('supports legacy JSON string input without claiming it is a message property', () => {
    expect(getRunLaunchDetails({ input_json: '"一条输入"' }).input).toMatchObject({
      source: 'flow_runs.input_json', text: '一条输入', note: '字符串格式的输入记录',
    })
  })

  it.each(['0', 'false', 'true', '[]', '[{"digest":"internal"}]'])('does not guess user text from input_json=%s', input_json => {
    expect(getRunLaunchDetails({ input_json }).input.text).toBeNull()
  })

  it.each(['0', 'false', '[]', '{}', '["a", "b"]'])('displays recorded JSON launch params %s, including falsy values', params_json => {
    const params = getRunLaunchDetails({ params_json }).params
    expect(JSON.parse(params.text!)).toEqual(JSON.parse(params_json))
    expect(params.json).toBe(true)
  })

  it('renders string launch params as text', () => {
    expect(getRunLaunchDetails({ params_json: '"one line"' }).params).toMatchObject({ text: 'one line', json: false })
  })

  it.each([
    '{"_clawmind_state":{"credentials":"never-show-this"}}',
    '{"business":"also-hidden","_clawmind_state":{"credentials":"never-show-this"}}',
    '{"nested":[{"_clawmind_state":{"credentials":"never-show-this"}}]}',
    '{"\\u005fclawmind_state":{"credentials":"never-show-this"}}',
    '{"_clawmind_state":{"credentials":"never-show-this"',
  ])('does not expose execution-state envelopes, including malformed or escaped ones', params_json => {
    const details = getRunLaunchDetails({ params_json, input_json: '{"params":{"credentials":"retry-secret"}}' })
    expect(details.params.text).toBeNull()
    expect(details.params.note).toContain('未记录原始启动参数')
    expect(JSON.stringify(details)).not.toMatch(/never-show-this|retry-secret|also-hidden|_clawmind_state/)
  })

  it('ignores non-text message/command properties without coercing objects into user text', () => {
    expect(getRunLaunchDetails({ input_json: '{"message":{"digest":"internal"},"command":false}' }).input.text).toBeNull()
    expect(getRunLaunchDetails({ input_json: '{"message":42,"command":"/stored"}' }).input.text).toBe('/stored')
  })
})
