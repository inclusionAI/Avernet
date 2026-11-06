import { describe, expect, it } from 'vitest'
import { assertLosslessNumbers } from '../losslessNumbers'

describe('numeric precision at workflow text boundaries', () => {
  it.each(['0', '9007199254740991', '-9007199254740991', '0.1', '1e3', '1.00', '.5', '0xFF', '"9007199254740993"'])('accepts a value that can round trip: %s', value => {
    expect(() => assertLosslessNumbers(`field: ${value}`)).not.toThrow()
  })
  it.each(['9007199254740992', '-9007199254740993', '0.10000000000000001', '1e-400', '.inf', '.nan', '-0', '-0.0'])('rejects a value that cannot safely round trip: %s', value => {
    expect(() => assertLosslessNumbers(`nested:\n  - value: ${value}`)).toThrow()
  })
})

it.each(['1: first\n  "1": second', 'true: first\n  "true": second', '? [a, b]\n  : first', '? &key name\n  : first\n  ? *key\n  : second'])('rejects non-string or ambiguous YAML mapping keys: %s', entries => {
  expect(() => assertLosslessNumbers(`custom:\n  ${entries}\n`)).toThrow()
})

it('rejects a long inexact decimal without quadratic tail scanning', () => {
  const started = performance.now()
  expect(() => assertLosslessNumbers(`amount: 0.1${'0'.repeat(128_000)}1`)).toThrow()
  expect(performance.now() - started).toBeLessThan(3000)
})
