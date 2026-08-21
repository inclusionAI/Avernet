/**
 * Individual hallucination detection rules.
 *
 * Each function is a pure check against NodeStepTraceRow[] data.
 * Returns a HallucinationCheck with pass/fail + evidence.
 */

import type { NodeStepTraceRow } from "../db/repositories/types.js";
import type {
  HallucinationCheck,
  HallucinationCheckType,
  HallucinationSeverity,
} from "./hallucination-types.js";

// ── Helpers ────────────────────────────────────────────────────────

function makeCheck(
  checkType: HallucinationCheckType,
  severity: HallucinationSeverity,
  passed: boolean,
  description: string,
  evidence: string | null,
): HallucinationCheck {
  return { checkType, severity, passed, description, evidence };
}

/** Get all tool_result steps. */
function toolResults(steps: NodeStepTraceRow[]): NodeStepTraceRow[] {
  return steps.filter((s) => s.step_type === "tool_result");
}

/** Get all tool_call steps. */
function toolCalls(steps: NodeStepTraceRow[]): NodeStepTraceRow[] {
  return steps.filter((s) => s.step_type === "tool_call");
}

/** Get all assistant_text steps. */
function assistantTexts(steps: NodeStepTraceRow[]): NodeStepTraceRow[] {
  return steps.filter((s) => s.step_type === "assistant_text");
}

/** Concatenate all tool_result outputs into one string. */
function allToolOutputText(steps: NodeStepTraceRow[]): string {
  return toolResults(steps)
    .map((s) => s.tool_output_text ?? "")
    .join("\n");
}

/** Concatenate all assistant_text content into one string. */
function allAssistantText(steps: NodeStepTraceRow[]): string {
  return assistantTexts(steps)
    .map((s) => s.text_content ?? "")
    .join("\n");
}

/** Get assistant_text steps that appear after a given step_seq. */
function assistantTextsAfterSeq(
  steps: NodeStepTraceRow[],
  seq: number,
): NodeStepTraceRow[] {
  return steps.filter((s) => s.step_type === "assistant_text" && s.step_seq > seq);
}

/** Check if text contains any of the keywords (case-insensitive). */
function containsAny(text: string, keywords: string[]): boolean {
  const lower = text.toLowerCase();
  return keywords.some((k) => lower.includes(k.toLowerCase()));
}

// ── Rule 1: Error Ignoring ────────────────────────────────────────

/**
 * Detects when a tool_result reports an error but the subsequent
 * assistant_text does not acknowledge the error.
 */
export function checkErrorIgnoring(steps: NodeStepTraceRow[]): HallucinationCheck {
  const errorResults = toolResults(steps).filter((s) => s.is_error === 1);
  const ERROR_KEYWORDS = [
    "error",
    "错误",
    "失败",
    "err",
    "exception",
    "fail",
    "unable",
    "无法",
    "未成功",
    "出错",
  ];

  for (const errResult of errorResults) {
    const followingTexts = assistantTextsAfterSeq(steps, errResult.step_seq);
    if (followingTexts.length === 0) continue;

    // Check if ANY following assistant text acknowledges the error
    const acknowledges = followingTexts.some((t) => {
      const content = (t.text_content ?? "").toLowerCase();
      return ERROR_KEYWORDS.some((k) => content.includes(k.toLowerCase()));
    });

    if (!acknowledges) {
      const toolName = errResult.tool_name ?? "unknown";
      const snippet = followingTexts[0].text_content?.slice(0, 200) ?? "";
      return makeCheck(
        "error_ignoring",
        "high",
        false,
        `工具 ${toolName} 返回错误，但后续 AI 回复未提及该错误`,
        `工具错误: ${toolName} (seq=${errResult.step_seq}); AI 回复: "${snippet}..."`,
      );
    }
  }

  return makeCheck(
    "error_ignoring",
    "high",
    true,
    "所有工具错误都在后续 AI 回复中被提及",
    null,
  );
}

// ── Rule 2: Ungrounded Claim ──────────────────────────────────────

/**
 * Detects claims in assistant_text that are not grounded in tool_result outputs.
 * Checks for quoted strings and specific factual claims.
 */
export function checkUngroundedClaim(steps: NodeStepTraceRow[]): HallucinationCheck {
  const texts = assistantTexts(steps);
  const toolOutput = allToolOutputText(steps);

  if (texts.length === 0 || !toolOutput.trim()) {
    return makeCheck(
      "ungrounded_claim",
      "medium",
      true,
      "无 AI 文本或无工具输出，跳过来源依据检查",
      null,
    );
  }

  // Extract quoted strings from assistant text (e.g. "风险等级为高风险")
  const quotedPattern = /[「"']([^"」'{2,}]+)[」"']/g;
  const toolOutputLower = toolOutput.toLowerCase();

  for (const textStep of texts) {
    const content = textStep.text_content ?? "";
    let match: RegExpExecArray | null;

    while ((match = quotedPattern.exec(content)) !== null) {
      const quoted = match[1].trim();
      if (quoted.length < 3) continue; // Skip very short quotes

      // Check if this quoted content appears (even partially) in tool output
      const quotedLower = quoted.toLowerCase();
      // Check substring overlap: at least 40% of quoted text words appear in tool output
      const words = quotedLower.split(/\s+/).filter((w) => w.length > 1);
      if (words.length === 0) continue;

      const matchedWords = words.filter((w) => toolOutputLower.includes(w));
      const overlap = matchedWords.length / words.length;

      if (overlap < 0.4) {
        return makeCheck(
          "ungrounded_claim",
          "medium",
          false,
          `AI 回复中的引述内容未在工具输出中找到依据`,
          `引述: "${quoted}"; 工具输出中未发现匹配`,
        );
      }
    }
  }

  return makeCheck(
    "ungrounded_claim",
    "medium",
    true,
    "AI 回复中的引述内容均可在工具输出中找到依据",
    null,
  );
}

// ── Rule 3: Fabricated Output ─────────────────────────────────────

/**
 * Detects specific numeric values in assistant_text that don't appear
 * in any tool_result output — a strong hallucination signal.
 */
export function checkFabricatedOutput(steps: NodeStepTraceRow[]): HallucinationCheck {
  const texts = assistantTexts(steps);
  const toolOutput = allToolOutputText(steps);
  const toolOutputLower = toolOutput.toLowerCase();

  if (texts.length === 0 || !toolOutput.trim()) {
    return makeCheck(
      "fabricated_output",
      "high",
      true,
      "无 AI 文本或无工具输出，跳过数据编造检查",
      null,
    );
  }

  // Extract specific numeric values with units or Chinese quantifiers
  // Matches: "3.2%", "42条", "128个", "1,234条记录", "0.5倍", etc.
  const numericPattern = /(\d[\d,]*\.?\d*)\s*([%％倍个条项次篇章]|%|条记录|个记录)/g;

  for (const textStep of texts) {
    const content = textStep.text_content ?? "";
    let match: RegExpExecArray | null;

    while ((match = numericPattern.exec(content)) !== null) {
      const value = match[1].replace(/,/g, ""); // Remove thousands separator
      const unit = match[2];

      // Skip zero values — "0条" typically means "none found" which doesn't need grounding
      if (value === "0" || value === "0.0") continue;

      // Check if this specific numeric value appears in tool output
      if (!toolOutputLower.includes(value) && !toolOutput.includes(value)) {
        return makeCheck(
          "fabricated_output",
          "high",
          false,
          `AI 回复引用了工具输出中不存在的数值`,
          `数值: "${match[0]}" (值=${value}, 单位=${unit}); 工具输出中未找到该数值`,
        );
      }
    }
  }

  return makeCheck(
    "fabricated_output",
    "high",
    true,
    "AI 回复中引用的数值均可在工具输出中找到",
    null,
  );
}

// ── Rule 4: Hallucinated Tool ─────────────────────────────────────

/**
 * Detects when assistant_text claims to have used a tool that
 * doesn't exist in the tool_call steps.
 */
export function checkHallucinatedTool(steps: NodeStepTraceRow[]): HallucinationCheck {
  const texts = assistantTexts(steps);
  const calls = toolCalls(steps);

  if (texts.length === 0 || calls.length === 0) {
    return makeCheck(
      "hallucinated_tool",
      "medium",
      true,
      "无 AI 文本或无工具调用，跳过工具编造检查",
      null,
    );
  }

  // Build set of actual tool names used
  const actualTools = new Set(
    calls.map((c) => (c.tool_name ?? "").toLowerCase()),
  );

  // Patterns that suggest tool usage in assistant text
  // Chinese: "查询了X", "调用了X", "读取了X", "使用了X工具", "通过X"
  // English: "called X", "used X", "queried X", "read X"
  // NOTE: Capture group uses alternation to avoid greedy Chinese char matching.
  // English tool names: [a-zA-Z_][a-zA-Z0-9_]* (snake_case identifiers)
  // Chinese tool names: [一-鿿]{1,6} (limited length to prevent over-capture)
  const toolActionPattern =
    /(?:查询了|调用了|读取了|使用了|通过|用)([a-zA-Z_][a-zA-Z0-9_]*|[一-鿿]{1,6})/g;

  for (const textStep of texts) {
    const content = textStep.text_content ?? "";
    let match: RegExpExecArray | null;

    while ((match = toolActionPattern.exec(content)) !== null) {
      const mentionedTool = match[1].toLowerCase().trim();

      // Skip generic terms that aren't tool names
      // Also skip if the captured string starts with a generic term
      // (Chinese regex can over-capture: "使用了工具进行查询" → "工具进行查询")
      const genericTerms = [
        "工具",
        "方式",
        "方法",
        "该",
        "其",
        "这个",
        "系统",
        "模型",
        "api",
      ];
      if (genericTerms.some((g) => mentionedTool === g || mentionedTool.startsWith(g))) continue;

      // Check if any actual tool name contains or is contained by mentioned tool
      const hasMatch = [...actualTools].some(
        (t) =>
          t.includes(mentionedTool) ||
          mentionedTool.includes(t) ||
          levenshteinClose(t, mentionedTool),
      );

      if (!hasMatch) {
        return makeCheck(
          "hallucinated_tool",
          "medium",
          false,
          `AI 回复声称使用了未实际调用的工具`,
          `提及: "${mentionedTool}"; 实际调用: [${[...actualTools].join(", ")}]`,
        );
      }
    }
  }

  return makeCheck(
    "hallucinated_tool",
    "medium",
    true,
    "AI 回复中提及的工具均有对应的实际调用",
    null,
  );
}

// ── Rule 5: Contradiction ─────────────────────────────────────────

/**
 * Detects when assistant_text makes assertions that contradict
 * tool_result outputs (e.g., "没有找到" when tool_result has data).
 */
export function checkContradiction(steps: NodeStepTraceRow[]): HallucinationCheck {
  const texts = assistantTexts(steps);
  const results = toolResults(steps);

  if (texts.length === 0 || results.length === 0) {
    return makeCheck(
      "contradiction",
      "high",
      true,
      "无 AI 文本或无工具结果，跳过矛盾检查",
      null,
    );
  }

  // Negation patterns that claim nothing was found / no results exist
  const negationPatterns = [
    /没有找到/,
    /未找到/,
    /不存在/,
    /没有.*记录/,
    /0\s*条记录/,
    /没有.*结果/,
    /无数据/,
    /no\s+(?:results?|records?|data|matches?|entries?)/i,
    /not\s+found/i,
    /does\s+not\s+exist/i,
    /没有/,
  ];

  for (const textStep of texts) {
    const content = textStep.text_content ?? "";

    for (const pattern of negationPatterns) {
      if (!pattern.test(content)) continue;

      // This assistant text claims nothing was found.
      // Check if any tool_result before it has non-empty, non-error output.
      const precedingResults = results.filter(
        (r) => r.step_seq < textStep.step_seq && r.is_error === 0,
      );
      const hasNonEmptyResult = precedingResults.some(
        (r) => (r.tool_output_text ?? "").trim().length > 10,
      );

      if (hasNonEmptyResult) {
        return makeCheck(
          "contradiction",
          "high",
          false,
          `AI 回复声称未找到结果，但工具实际返回了数据`,
          `AI 说: "${content.slice(0, 150)}"; 但之前有 ${precedingResults.length} 个非空工具结果`,
        );
      }
    }
  }

  return makeCheck(
    "contradiction",
    "high",
    true,
    "AI 回复中的断言与工具结果无矛盾",
    null,
  );
}

// ── Utility ───────────────────────────────────────────────────────

/** Simple check: are two strings close enough (edit distance ≤ 2)? */
function levenshteinClose(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 3) return false;
  const dist = levenshtein(a, b);
  return dist <= 2;
}

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array(n + 1).fill(0),
  );

  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1]
          : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
    }
  }

  return dp[m][n];
}