/**
 * Baseline markdown template parser.
 *
 * Parses the standard baseline markdown format used for task validation templates:
 * - YAML frontmatter between `---` delimiters
 * - Body sections keyed by `## SectionName` headings
 *
 * No external markdown AST dependency — uses regex and the `yaml` package
 * (already installed in clawweb) for frontmatter parsing.
 */
import YAML from 'yaml'
import type { GradingWeights } from '../types'

export interface ParsedBaselineMarkdown {
  // Frontmatter fields
  id?: string
  category?: string
  grading_type?: string
  timeout_seconds?: number
  workspace_files?: string[]
  grading_weights?: GradingWeights
  // Body sections keyed by H2 heading name
  sections: Record<string, string>
}

/** Section name mapping: markdown H2 heading → content field key */
const SECTION_FIELD_MAP: Record<string, string> = {
  'Applicability': 'applicability',
  'Prompt': 'prompt',
  'Expected Behavior': 'expectedBehavior',
  'Grading Criteria': 'gradingCriteria',
  'Automated Checks': 'automatedChecks',
  'LLM Judge Rubric': 'llmJudgeRubric',
}

/**
 * Parse a baseline markdown string into structured frontmatter + body sections.
 *
 * @param markdown - Raw markdown content (may include frontmatter)
 * @returns Parsed frontmatter fields and body sections
 */
export function parseBaselineMarkdown(markdown: string): ParsedBaselineMarkdown {
  const trimmed = markdown.trim()

  // 1. Extract frontmatter
  let frontmatter: Record<string, unknown> = {}
  let body = trimmed

  const fmMatch = trimmed.match(/^---\s*\n([\s\S]*?)\n---\s*\n/)
  if (fmMatch) {
    try {
      frontmatter = YAML.parse(fmMatch[1]) as Record<string, unknown> ?? {}
    } catch {
      // If YAML parsing fails, treat entire input as body
      frontmatter = {}
    }
    body = trimmed.slice(fmMatch[0].length)
  }

  // 2. Extract H2 sections from body
  const sections = extractH2Sections(body)

  // 3. Build result
  const result: ParsedBaselineMarkdown = {
    sections,
  }

  // Map frontmatter fields
  if (typeof frontmatter.id === 'string') result.id = frontmatter.id
  if (typeof frontmatter.category === 'string') result.category = frontmatter.category
  if (typeof frontmatter.grading_type === 'string') result.grading_type = frontmatter.grading_type
  if (typeof frontmatter.timeout_seconds === 'number') result.timeout_seconds = frontmatter.timeout_seconds

  if (Array.isArray(frontmatter.workspace_files)) {
    result.workspace_files = (frontmatter.workspace_files as unknown[]).filter(
      (f): f is string => typeof f === 'string'
    )
  }

  if (frontmatter.grading_weights && typeof frontmatter.grading_weights === 'object') {
    const gw = frontmatter.grading_weights as Record<string, unknown>
    result.grading_weights = {
      automated: typeof gw.automated === 'number' ? gw.automated : undefined,
      llm_judge: typeof gw.llm_judge === 'number' ? gw.llm_judge : undefined,
    }
  }

  return result
}

/**
 * Extract top-level (H2) sections from markdown body text.
 * Returns a map of heading name → section content (trimmed).
 *
 * For the "Automated Checks" section, fenced code block markers are stripped
 * so only the raw code content remains.
 */
function extractH2Sections(body: string): Record<string, string> {
  const sections: Record<string, string> = {}

  // Find all H2 heading positions first, then slice content between them
  const headingRegex = /^## (.+)$/gm
  const headings: Array<{ name: string; start: number; contentStart: number }> = []

  let hMatch: RegExpExecArray | null
  while ((hMatch = headingRegex.exec(body)) !== null) {
    headings.push({
      name: hMatch[1].trim(),
      start: hMatch.index,
      contentStart: hMatch.index + hMatch[0].length + 1, // +1 for the trailing newline
    })
  }

  // Extract content between each pair of consecutive headings
  for (let i = 0; i < headings.length; i++) {
    const contentEnd = i + 1 < headings.length ? headings[i + 1].start : body.length
    const content = body.slice(headings[i].contentStart, contentEnd).trim()
    if (headings[i].name && content) {
      sections[headings[i].name] = content
    }
  }

  // Special handling: strip fenced code block markers from Automated Checks
  if (sections['Automated Checks']) {
    sections['Automated Checks'] = stripCodeFences(sections['Automated Checks'])
  }

  return sections
}

/**
 * Strip markdown fenced code block delimiters (```python ... ```).
 * Returns the inner code content with the fences removed.
 */
function stripCodeFences(text: string): string {
  // Match opening fence (```python, ```bash, ```json, or plain ```) and closing fence
  const fenceRegex = /^```[\w]*\s*\n([\s\S]*?)\n```\s*$/m
  const match = text.match(fenceRegex)
  if (match) {
    return match[1].trim()
  }
  return text.trim()
}

/**
 * Convert parsed markdown result into form field values.
 * Maps frontmatter and sections to the flat EditForm shape used by the UI.
 */
export function parsedMarkdownToFormFields(parsed: ParsedBaselineMarkdown): Record<string, string> {
  const fields: Record<string, string> = {}

  // Frontmatter → form fields
  if (parsed.id) fields.template_id = parsed.id
  if (parsed.category) fields.category = parsed.category
  if (parsed.grading_type) fields.gradingType = parsed.grading_type
  if (parsed.timeout_seconds != null) fields.timeoutSeconds = String(parsed.timeout_seconds)
  if (parsed.workspace_files && parsed.workspace_files.length > 0) {
    fields.workspaceFiles = parsed.workspace_files.join(', ')
  }
  if (parsed.grading_weights) {
    if (parsed.grading_weights.automated != null) {
      fields.gradingWeightsAutomated = String(parsed.grading_weights.automated)
    }
    if (parsed.grading_weights.llm_judge != null) {
      fields.gradingWeightsLlmJudge = String(parsed.grading_weights.llm_judge)
    }
  }

  // Body sections → form fields (using the SECTION_FIELD_MAP)
  for (const [sectionName, fieldKey] of Object.entries(SECTION_FIELD_MAP)) {
    const content = parsed.sections[sectionName]
    if (content) {
      fields[fieldKey] = content
    }
  }

  return fields
}