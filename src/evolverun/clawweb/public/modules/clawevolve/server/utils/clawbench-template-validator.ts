import YAML from "yaml";

export type ClawBenchTemplateValidationResult = {
  valid: boolean;
  validator_error_message?: string;
};

const STANDARD_SECTIONS = [
  "Prompt",
  "Expected Behavior",
  "Grading Criteria",
  "Automated Checks",
  "LLM Judge Rubric",
  "Workspace Files",
  "Additional Notes",
] as const;

const NORMALIZED_STANDARD_SECTIONS = new Map(
  STANDARD_SECTIONS.map((section) => [normalizeHeading(section), section]),
);

export function validateClawBenchRuntimeTemplate(contentMd: string): ClawBenchTemplateValidationResult {
  const frontmatterMatch = contentMd.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*\r?\n([\s\S]*)$/);
  if (!frontmatterMatch) {
    return {
      valid: false,
      validator_error_message: '模板缺少合法 YAML frontmatter，ClawBench 无法加载 task。请在文件开头补充 "--- ... ---" 元信息。',
    };
  }

  const frontmatter = frontmatterMatch[1];
  const body = frontmatterMatch[2];
  let parsedFrontmatter: unknown;
  try {
    parsedFrontmatter = YAML.parse(frontmatter);
  } catch {
    return {
      valid: false,
      validator_error_message: "模板 YAML frontmatter 解析失败，ClawBench 无法加载 task。请检查文件开头的 YAML 元信息格式。",
    };
  }
  const id = isRecord(parsedFrontmatter) ? parsedFrontmatter.id : null;
  if (typeof id !== "string" || !id.trim()) {
    return {
      valid: false,
      validator_error_message: "模板 frontmatter 缺少非空 id，ClawBench 无法稳定生成 taskId 和 sessionId。请补充 id。",
    };
  }

  const runtimeSections = parseRuntimeSections(body);
  const prompt = runtimeSections.get("Prompt")?.trim() ?? "";
  if (!prompt) {
    const invalidPrompt = findNonRuntimeSectionHeading(body, "Prompt");
    if (invalidPrompt) {
      return {
        valid: false,
        validator_error_message: `检测到 "${invalidPrompt.text}"，但当前 ClawBench 运行时只识别 "## Prompt"。请将 "${invalidPrompt.text}" 改为 "## Prompt"，否则运行时发送给 OpenClaw 的 message 会为空。`,
      };
    }
    return {
      valid: false,
      validator_error_message: '模板缺少非空 "## Prompt"，ClawBench 运行时发送给 OpenClaw 的 message 会为空。请补充 "## Prompt"。',
    };
  }

  const invalidHeading = findInvalidStandardSectionHeading(body);
  if (invalidHeading) {
      return {
        valid: false,
        validator_error_message: `检测到 "${invalidHeading.text}"，但当前 ClawBench 运行时只识别 "## ${invalidHeading.expected}"。请将该标题改为 "## ${invalidHeading.expected}"，否则运行时无法解析 ${invalidHeading.expected}。`,
      };
  }

  return { valid: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function parseRuntimeSections(body: string): Map<string, string> {
  const sections = new Map<string, string>();
  let currentSection: string | null = null;
  let currentContent: string[] = [];

  const flush = () => {
    if (currentSection) {
      sections.set(currentSection, currentContent.join("\n").trim());
    }
  };

  for (const line of body.split(/\r?\n/)) {
    const match = line.match(/^##\s+(.+)$/);
    if (match) {
      flush();
      currentSection = match[1].trim();
      currentContent = [];
    } else if (currentSection) {
      currentContent.push(line);
    }
  }
  flush();

  return sections;
}

function findNonRuntimeSectionHeading(body: string, section: string): { text: string } | null {
  const invalid = findInvalidStandardSectionHeading(body);
  return invalid?.expected === section ? { text: invalid.text } : null;
}

function findInvalidStandardSectionHeading(body: string): { text: string; expected: string } | null {
  for (const line of body.split(/\r?\n/)) {
    const match = line.match(/^(#{1,6})\s+(.+?)\s*$/);
    if (!match) continue;

    const level = match[1].length;
    const rawHeading = match[2].trim();
    const expected = NORMALIZED_STANDARD_SECTIONS.get(normalizeHeading(rawHeading));
    if (!expected) continue;

    const exactRuntimeHeading = level === 2 && rawHeading === expected;
    if (!exactRuntimeHeading) {
      return { text: line.trim(), expected };
    }
  }
  return null;
}

function normalizeHeading(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}
