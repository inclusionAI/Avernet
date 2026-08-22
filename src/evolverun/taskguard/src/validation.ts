/**
 * Runtime validation engine for LLM-based output quality checks.
 *
 * After an AI node completes, if it references a validationTemplateId,
 * this module calls an LLM evaluator with the template's prompt/criteria
 * and scores the output. If the score falls below validationMinScore and
 * the node has alerting configured, a DingTalk alert is dispatched.
 */

export interface ValidationTemplateContent {
  prompt: string;
  expectedBehavior?: string;
  gradingCriteria?: string;
  automatedChecks?: string;
}

export interface ValidationResult {
  passed: boolean;
  score: number;
  feedback: string;
  details: Record<string, number>;
}

export interface ValidationTemplateRecord {
  templateId: string;
  name: string;
  content: ValidationTemplateContent;
  enabled: boolean;
}

// ── LLM-based validation ──

const LLM_BASE_URL = process.env.LLM_BASE_URL ?? "";
const LLM_API_KEY = process.env.LLM_API_KEY ?? "";
const LLM_MODEL = process.env.LLM_MODEL ?? "gpt-4o";

function buildValidationPrompt(template: ValidationTemplateContent): string {
  const parts: string[] = [
    "You are a quality assurance evaluator for AI-generated outputs.",
    "Evaluate the following AI output based on the criteria below.",
    "Provide a score from 0 to 100 and detailed feedback.",
    "",
    "## Task Prompt",
    template.prompt,
  ];

  if (template.expectedBehavior) {
    parts.push("", "## Expected Behavior", template.expectedBehavior);
  }
  if (template.gradingCriteria) {
    parts.push("", "## Grading Criteria", template.gradingCriteria);
  }
  if (template.automatedChecks) {
    parts.push("", "## Automated Checks (reference)", template.automatedChecks);
  }

  parts.push(
    "",
    "## Output Format",
    "Respond with a JSON object with exactly these keys:",
    '- "score": number (0-100)',
    '- "passed": boolean (true if score >= the minimum threshold)',
    '- "feedback": string (detailed evaluation feedback)',
    '- "details": object (breakdown scores by criteria, e.g. {"relevance": 85, "accuracy": 70})',
  );

  return parts.join("\n");
}

async function invokeLlmEvaluator(
  systemPrompt: string,
  sampleOutput: string,
): Promise<ValidationResult> {
  if (!LLM_BASE_URL || !LLM_API_KEY) {
    throw new Error("LLM_BASE_URL and LLM_API_KEY environment variables are required for validation");
  }

  const url = `${LLM_BASE_URL.replace(/\/$/, "")}/v1/chat/completions`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${LLM_API_KEY}`,
    },
    body: JSON.stringify({
      model: LLM_MODEL,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: `## AI Output to Evaluate\n\n${sampleOutput}` },
      ],
      temperature: 0.1,
      response_format: { type: "json_object" },
    }),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => response.statusText);
    throw new Error(`LLM API ${response.status}: ${body}`);
  }

  const data = (await response.json()) as {
    choices: Array<{ message: { content: string } }>;
  };

  const content = data.choices?.[0]?.message?.content ?? "";
  return parseValidationResponse(content);
}

function parseValidationResponse(raw: string): ValidationResult {
  try {
    const parsed = JSON.parse(raw);
    const score = typeof parsed.score === "number" ? Math.max(0, Math.min(100, parsed.score)) : 0;
    const passed = typeof parsed.passed === "boolean" ? parsed.passed : score >= 60;
    const feedback = typeof parsed.feedback === "string" ? parsed.feedback : "";
    const details: Record<string, number> = {};
    if (parsed.details && typeof parsed.details === "object") {
      for (const [key, val] of Object.entries(parsed.details)) {
        if (typeof val === "number") details[key] = val;
      }
    }
    return { passed, score, feedback, details };
  } catch {
    return {
      passed: false,
      score: 0,
      feedback: `Failed to parse validation response: ${raw.slice(0, 200)}`,
      details: {},
    };
  }
}

/**
 * Validate node output against a validation template using LLM evaluation.
 * Returns null if validation is not configured for this node.
 */
export async function validateNodeOutput(
  template: ValidationTemplateContent,
  sampleOutput: string,
  minScore: number,
): Promise<ValidationResult> {
  const systemPrompt = buildValidationPrompt(template);
  const result = await invokeLlmEvaluator(systemPrompt, sampleOutput);
  // Override passed based on minScore threshold
  result.passed = result.score >= minScore;
  return result;
}

/**
 * Check if a node should be validated (has validationTemplateId set).
 */
export function shouldValidateNode(node: { validationTemplateId?: string }): boolean {
  return !!node.validationTemplateId;
}