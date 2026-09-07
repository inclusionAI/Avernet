/**
 * MVP markdown parser for AgentBench case files.
 * Extracts YAML frontmatter and common section headings.
 * Does NOT execute any code from the markdown.
 */

export type ParsedBenchMeta = {
  frontmatter?: Record<string, unknown>;
  prompt?: string;
  expectedBehavior?: string;
  gradingCriteria?: string;
  automatedChecks?: string;
  setup?: string;
  teardown?: string;
  tags?: string[];
};

/**
 * Parse an AgentBench markdown case content.
 */
export function parseBenchMarkdown(contentMd: string): ParsedBenchMeta {
  const result: ParsedBenchMeta = {};

  // Extract frontmatter
  const frontmatter = extractFrontmatter(contentMd);
  if (frontmatter) {
    result.frontmatter = frontmatter;
    if (frontmatter.tags && Array.isArray(frontmatter.tags)) {
      result.tags = frontmatter.tags.map(String);
    }
  }

  // Extract sections by heading
  const sections = extractSections(contentMd);

  // Map common section names (case-insensitive, support # / ## / ###)
  for (const [heading, body] of sections) {
    const normalized = heading.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (normalized.includes("prompt") || normalized.includes("task")) {
      result.prompt = mergeText(result.prompt, body);
    } else if (normalized.includes("expectedbehavior") || normalized.includes("expected")) {
      result.expectedBehavior = mergeText(result.expectedBehavior, body);
    } else if (normalized.includes("gradingcriteria") || normalized.includes("grading") || normalized.includes("criteria")) {
      result.gradingCriteria = mergeText(result.gradingCriteria, body);
    } else if (normalized.includes("automatedchecks") || normalized.includes("checks")) {
      result.automatedChecks = mergeText(result.automatedChecks, body);
    } else if (normalized.includes("setup")) {
      result.setup = mergeText(result.setup, body);
    } else if (normalized.includes("teardown")) {
      result.teardown = mergeText(result.teardown, body);
    }
  }

  return result;
}

function extractFrontmatter(md: string): Record<string, unknown> | null {
  // Support both \n and \r\n line endings
  const match = md.match(/^---\s*(?:\r?\n)([\s\S]*?)(?:\r?\n)---\s*(?:\r?\n)/);
  if (!match) return null;
  const yamlText = match[1];
  const result: Record<string, unknown> = {};

  // Simple key-value parser (MVP, no nested structures)
  for (const line of yamlText.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    // Handle simple list items under a key (e.g., tags:)
    if (trimmed.startsWith("- ")) continue; // skip list items for MVP

    const colonIdx = trimmed.indexOf(":");
    if (colonIdx === -1) continue;

    const key = trimmed.slice(0, colonIdx).trim();
    let value: string | boolean | number | null = trimmed.slice(colonIdx + 1).trim();

    // Remove surrounding quotes if present
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = (value as string).slice(1, -1);
    }

    // Try parse as number or boolean
    if (value === "true") value = true;
    else if (value === "false") value = false;
    else if (value === "null" || value === "~") value = null;
    else if (!Number.isNaN(Number(value)) && value !== "") value = Number(value);

    result[key] = value;
  }

  return result;
}

function extractSections(md: string): Array<[string, string]> {
  const sections: Array<[string, string]> = [];

  // Remove frontmatter before parsing headings
  const body = md.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, "");

  // Match markdown headings (# Heading)
  const headingRegex = /^(#{1,3})\s+(.+)\n/gm;
  let match: RegExpExecArray | null;
  const matches: Array<{ level: number; heading: string; index: number }> = [];

  while ((match = headingRegex.exec(body)) !== null) {
    matches.push({
      level: match[1].length,
      heading: match[2].trim(),
      index: match.index,
    });
  }

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index + matches[i].heading.length + matches[i].level + 1; // +1 for newline
    const end = i + 1 < matches.length ? matches[i + 1].index : body.length;
    const sectionBody = body.slice(start, end).trim();
    sections.push([matches[i].heading, sectionBody]);
  }

  return sections;
}

function mergeText(existing: string | undefined, next: string): string {
  if (!existing) return next;
  return `${existing}\n\n${next}`;
}
