import { isMap, isScalar, parseDocument, visit } from 'yaml'

// Compare decimal values without rounding. Trailing zeros and exponent notation
// are spelling differences; different mathematical values are not.
function decimalValue(source: string): string | null {
  const text = source.replaceAll('_', '')
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i.exec(text)
  if (!match || (match[5]?.length ?? 0) > 32) return null
  const fraction = match[3] ?? match[4] ?? ''
  let digits = `${match[2] ?? ''}${fraction}`.replace(/^0+/, '')
  if (!digits) return match[1] === '-' ? '-0' : '0'
  let exponent = BigInt(match[5] ?? '0') - BigInt(fraction.length)
  let trailing = 0
  while (trailing < digits.length && digits.charCodeAt(digits.length - 1 - trailing) === 48) trailing++
  if (trailing) { digits = digits.slice(0, -trailing); exponent += BigInt(trailing) }
  return `${match[1] === '-' ? '-' : ''}${digits}e${exponent}`
}

/** Reject data JavaScript would change merely by parsing and serializing it.
 * The caller still performs its ordinary structural/identity validation. */
function checkedDocument(text: string) {
  const doc = parseDocument(text, { uniqueKeys: true, intAsBigInt: true })
  if (doc.errors.length) throw new Error('工作流内容格式无效')
  visit(doc, (_key, node) => {
    if (isMap(node)) {
      for (const pair of node.items) {
        if (!isScalar(pair.key) || typeof pair.key.value !== 'string') {
          throw new Error('工作流映射键必须是字符串；未替换草稿')
        }
      }
    }
    if (!isScalar(node)) return
    const value: unknown = node.value
    const source = node.source ?? ''
    if (typeof value === 'bigint') {
      if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER)
        || value === 0n && /^-/.test(source)) throw new Error('工作流数字无法无损读取；未替换草稿')
      node.value = Number(value)
    } else if (typeof value === 'number') {
      if (!Number.isFinite(value) || Object.is(value, -0)
        || Number.isInteger(value) && !Number.isSafeInteger(value)
        || decimalValue(source) === null || decimalValue(source) !== decimalValue(String(value))) {
        throw new Error('工作流数字无法无损读取；未替换草稿')
      }
    }
  })
  return doc
}

export function assertLosslessNumbers(text: string): void {
  checkedDocument(text)
}

/** Parse and validate in one AST pass for the interactive YAML editor. */
export function parseLosslessWorkflowText(text: string): unknown {
  return checkedDocument(text).toJS({ maxAliasCount: 100 })
}
