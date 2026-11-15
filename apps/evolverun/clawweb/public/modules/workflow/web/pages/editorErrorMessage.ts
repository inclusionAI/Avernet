/** Extract a human-readable message from fetchJson ApiError strings ("API NNN: {json}"). */
export function extractApiErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  const jsonMatch = msg.match(/^API \d+:\s*(\{.*\})$/s)
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[1]) as { message?: string }
      if (parsed.message) return parsed.message
    } catch { /* not valid JSON — fall through */ }
  }
  return msg
}
