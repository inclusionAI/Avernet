/**
 * Warning parsing utilities for node execution warnings.
 *
 * When embedded-agent nodes succeed but have tool/API errors in their
 * session, ClawMind stores a `[WARNINGS]`-prefixed string in the
 * `node_executions.error_text` column. These utilities detect and
 * parse that format so the frontend can display warnings distinctly
 * from genuine failures.
 */

export type ExecutionWarning = {
  code: string
  message: string
  detail?: Record<string, unknown>
}

/**
 * Check if an error_text value represents warnings (rather than a
 * genuine failure). ClawMind writes `[WARNINGS]` as the prefix.
 */
export function isWarningsErrorText(errorText: string | null | undefined): boolean {
  return typeof errorText === 'string' && errorText.startsWith('[WARNINGS]')
}

/**
 * Parse a `[WARNINGS]`-prefixed error_text back into structured warning
 * objects. Returns an empty array if the string is not warnings-prefixed.
 *
 * Format: `[WARNINGS][code1] message1 | detail1; [code2] message2`
 */
export function parseWarningsErrorText(errorText: string | null | undefined): ExecutionWarning[] {
  if (!errorText || !isWarningsErrorText(errorText)) return []

  const warnings: ExecutionWarning[] = []
  // Split on "; [WARNINGS][" boundaries
  const segments = errorText.split('; [WARNINGS][')

  for (let i = 0; i < segments.length; i++) {
    const raw = i === 0
      ? segments[i]!.replace(/^\[WARNINGS]\[/, '')
      : segments[i]!

    // Extract code: everything before the first "] "
    const codeEnd = raw.indexOf('] ')
    if (codeEnd < 0) continue

    const code = raw.slice(0, codeEnd)
    const rest = raw.slice(codeEnd + 2)

    // Split message from detail by " | {"
    const detailSplit = rest.indexOf(' | {')
    let message: string
    let detail: Record<string, unknown> | undefined

    if (detailSplit >= 0) {
      message = rest.slice(0, detailSplit)
      const detailJson = rest.slice(detailSplit + 3) // skip " | "
      try {
        const parsed = JSON.parse(detailJson)
        detail = typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
          ? parsed as Record<string, unknown>
          : undefined
      } catch {
        // If detail is not valid JSON, keep it as part of the message
        message = rest
      }
    } else {
      message = rest
    }

    warnings.push({ code, message, detail })
  }

  return warnings
}

/**
 * Get a human-friendly label for a warning code.
 */
export function getWarningCodeLabel(code: string): string {
  const labels: Record<string, string> = {
    tool_errors: '工具错误',
    partial_output: '部分输出',
    recovered_from_error: '错误恢复',
    json_repair_needed: 'JSON修复',
    session_errors: '会话错误',
  }
  return labels[code] ?? code
}