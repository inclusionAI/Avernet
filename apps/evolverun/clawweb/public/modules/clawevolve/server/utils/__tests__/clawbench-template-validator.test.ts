import { describe, expect, it } from "vitest";
import { validateClawBenchRuntimeTemplate } from "../clawbench-template-validator.js";

function template(body: string, frontmatter = "id: task_01_example\nname: Example"): string {
  return `---\n${frontmatter}\n---\n\n${body}`;
}

describe("validateClawBenchRuntimeTemplate", () => {
  it("accepts a template with non-empty ## Prompt", () => {
    const result = validateClawBenchRuntimeTemplate(template("## Prompt\n\n请只回复 OK。"));

    expect(result.valid).toBe(true);
  });

  it("rejects # Prompt because old ClawBench only parses ## Prompt", () => {
    const result = validateClawBenchRuntimeTemplate(template("# Prompt\n\n请只回复 OK。"));

    expect(result.valid).toBe(false);
    expect(result.validator_error_message).toContain("# Prompt");
    expect(result.validator_error_message).toContain("## Prompt");
  });

  it("rejects standard sections that use unsupported heading levels", () => {
    const result = validateClawBenchRuntimeTemplate(
      template("## Prompt\n\n请只回复 OK。\n\n# Automated Checks\n\n```python\ndef grade(transcript, workspace): return {}\n```"),
    );

    expect(result.valid).toBe(false);
    expect(result.validator_error_message).toContain("# Automated Checks");
    expect(result.validator_error_message).toContain("## Automated Checks");
  });

  it("rejects standard sections that are not exact runtime keys", () => {
    const result = validateClawBenchRuntimeTemplate(
      template("## Prompt\n\n请只回复 OK。\n\n## Automated checks\n\n```python\ndef grade(transcript, workspace): return {}\n```"),
    );

    expect(result.valid).toBe(false);
    expect(result.validator_error_message).toContain("## Automated checks");
    expect(result.validator_error_message).toContain("## Automated Checks");
  });

  it("rejects standard sections with markdown closing hashes", () => {
    const result = validateClawBenchRuntimeTemplate(
      template("## Prompt\n\n请只回复 OK。\n\n## LLM Judge Rubric ##\n\nScore strictly."),
    );

    expect(result.valid).toBe(false);
    expect(result.validator_error_message).toContain("## LLM Judge Rubric ##");
    expect(result.validator_error_message).toContain("## LLM Judge Rubric");
  });

  it("rejects missing id", () => {
    const result = validateClawBenchRuntimeTemplate(template("## Prompt\n\n请只回复 OK。", "name: Example"));

    expect(result.valid).toBe(false);
    expect(result.validator_error_message).toContain("id");
  });
});
