/**
 * Facade Command Generator — dynamically generates Claude Code plugin
 * command and skill files from the ClawMind facade registry.
 *
 * Used by `mcp-entry.ts --init` during SessionStart hook to keep
 * Claude Code's slash commands in sync with packs + DB facades.
 *
 * @module platform/facade-command-generator
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { FacadeRegistry, ResolvedWorkflowFacade } from "../facades/registry.js";

// ── Marker ──

/** Frontmatter marker injected into auto-generated files so we can clean them up on re-run. */
const GENERATED_MARKER = "<!-- @clawmind:generated-facade-command -->";

/** Static commands that should never be cleaned/overwritten by the generator. */
export const STATIC_COMMANDS = new Set(["workflow-help", "workflow-dispatch", "workflow"]);

// ── Command Markdown Generation ──

function generateCommandMarkdown(facade: ResolvedWorkflowFacade): string {
  const title = facade.help?.title ?? facade.command;
  const summary = facade.help?.summary ?? `ClawMind facade: /${facade.command}`;
  const description = `${title} — ${summary}`;

  const lines: string[] = [
    "---",
    `description: ${JSON.stringify(description)}`,
    "disable-model-invocation: false",
    "---",
    "",
    GENERATED_MARKER,
    "",
    `The user invoked the \`/${facade.command}\` facade command${facade.packId && facade.packId !== "__db__" ? ` from the \`${facade.packId}\` pack` : ""}.`,
    "",
    "Call the `mcp__clawmind__workflow_engine_dispatch` tool with:",
    '- command: "run ' + facade.defaultWorkflow + ' <user query>" — where <user query> is the user\'s full input text appended as positional message (NOT as --input flag). Example: "run ' + facade.defaultWorkflow + ' WebSocket连接失败"',
    "",
    "If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.",
  ];

  // Append examples from facade help if available
  if (facade.help?.examples?.length) {
    lines.push("");
    lines.push("## Examples");
    for (const ex of facade.help.examples) {
      lines.push(`- ${ex}`);
    }
  }

  return lines.join("\n") + "\n";
}

// ── Skill Markdown Generation ──

function generateSkillMarkdown(facade: ResolvedWorkflowFacade): string {
  const title = facade.help?.title ?? facade.command;
  const summary = facade.help?.summary ?? `ClawMind facade: /${facade.command}`;
  const description = `${title} — ${summary}`;

  const lines: string[] = [
    "---",
    `name: ${facade.command}`,
    `description: ${JSON.stringify(description)}`,
    "disable-model-invocation: false",
    "command-dispatch: tool",
    "command-tool: workflow_engine_dispatch",
    "command-arg-mode: raw",
    "---",
    "",
    GENERATED_MARKER,
    "",
    `# ${facade.command}`,
    "",
    `${title} — ${summary}`,
    "",
    "## Usage",
    "",
    "Call `workflow_engine_dispatch` with command:",
    '- "run ' + facade.defaultWorkflow + ' <user query>" — append user query as positional text (NOT --input flag)',
    "",
  ];

  if (facade.help?.examples?.length) {
    lines.push("## Examples");
    lines.push("");
    for (const ex of facade.help.examples) {
      lines.push(`- ${ex}`);
    }
    lines.push("");
  }

  return lines.join("\n") + "\n";
}

// ── Cleanup ──

/**
 * Remove previously generated facade command/skill files.
 * Identifies generated files by the {@link GENERATED_MARKER} in their content.
 * Static commands (workflow-help, workflow-dispatch, workflow) are never removed.
 */
function cleanGeneratedFiles(dir: string): void {
  if (!existsSync(dir)) return;

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      // Check if it's a skill directory with a generated SKILL.md
      const skillFile = join(dir, entry.name, "SKILL.md");
      if (existsSync(skillFile) && isGeneratedFile(skillFile)) {
        rmSync(join(dir, entry.name), { recursive: true, force: true });
      }
      continue;
    }

    if (!entry.isFile()) continue;
    const filePath = join(dir, entry.name);

    // Skip non-markdown files
    if (!entry.name.endsWith(".md")) continue;

    // Skip static commands
    const baseName = entry.name.replace(/\.md$/, "");
    if (STATIC_COMMANDS.has(baseName)) continue;

    if (isGeneratedFile(filePath)) {
      rmSync(filePath, { force: true });
    }
  }
}

function isGeneratedFile(filePath: string): boolean {
  try {
    const content = readFileSync(filePath, "utf-8");
    return content.includes(GENERATED_MARKER);
  } catch {
    return false;
  }
}

// ── Main Generator ──

export interface FacadeCommandGeneratorResult {
  /** Number of command files generated. */
  commandsGenerated: number;
  /** Number of skill files generated. */
  skillsGenerated: number;
  /** Number of stale files cleaned up. */
  cleaned: number;
  /** List of generated command names. */
  commandNames: string[];
}

/** Options for generateFacadeCommands. */
export interface GenerateFacadeCommandsOptions {
  /**
   * Override the directory where commands/*.md files are written.
   * When set (no-plugin mode), commands are written directly to this directory
   * instead of `pluginRoot/commands/`. Skills are still written to `pluginRoot/skills/`
   * (if pluginRoot is provided) since skills are only loaded by the Plugin system.
   */
  commandsDirOverride?: string;
}

/**
 * Generate Claude Code plugin command and skill files from the facade registry.
 *
 * This function:
 * 1. Cleans up previously generated facade files (identified by marker)
 * 2. Generates `commands/{facade}.md` for each non-static facade
 * 3. Generates `skills/{facade}/SKILL.md` for each non-static facade (plugin mode only)
 *
 * @param registry - The facade registry built from packs + DB bindings
 * @param pluginRoot - The plugin installation directory (e.g., CLAUDE_PLUGIN_ROOT),
 *                      or a fallback path when in no-plugin mode
 * @param options - Optional configuration for generation behavior
 * @returns Generation result summary
 */
export function generateFacadeCommands(
  registry: FacadeRegistry,
  pluginRoot: string,
  options?: GenerateFacadeCommandsOptions,
): FacadeCommandGeneratorResult {
  // Determine output directories based on mode:
  //   - No-plugin mode (commandsDirOverride set): commands go to the override dir,
  //     skills go to pluginRoot/skills/ (only useful if pluginRoot is a real plugin dir)
  //   - Plugin mode (default): both commands and skills live at pluginRoot level
  const commandsDir = options?.commandsDirOverride ?? join(pluginRoot, "commands");
  const skillsDir = join(pluginRoot, "skills");
  const isNoPluginMode = !!options?.commandsDirOverride;

  // Ensure directories exist
  mkdirSync(commandsDir, { recursive: true });
  if (!isNoPluginMode) {
    mkdirSync(skillsDir, { recursive: true });
  }

  // Count existing generated files before cleanup for reporting
  const oldCommandCount = countGeneratedFiles(commandsDir, "md");
  const oldSkillCount = isNoPluginMode ? 0 : countGeneratedFiles(skillsDir, "skillDir");

  // Clean up previously generated files
  cleanGeneratedFiles(commandsDir);
  if (!isNoPluginMode) {
    cleanGeneratedFiles(skillsDir);
  }

  const cleaned = oldCommandCount + oldSkillCount;

  // Generate new files
  let commandsGenerated = 0;
  let skillsGenerated = 0;
  const commandNames: string[] = [];

  for (const cmd of registry.commands()) {
    // Skip static commands that are maintained manually
    if (STATIC_COMMANDS.has(cmd)) continue;

    const facade = registry.resolve(cmd);
    if (!facade) continue;

    commandNames.push(cmd);

    // Generate commands/{cmd}.md
    const commandFile = join(commandsDir, `${cmd}.md`);
    writeFileSync(commandFile, generateCommandMarkdown(facade), "utf-8");
    commandsGenerated++;

    // Generate skills/{cmd}/SKILL.md (plugin mode only — skills are not loaded
    // without the Plugin system, so skip in no-plugin mode)
    if (!isNoPluginMode) {
      const skillDir = join(skillsDir, cmd);
      mkdirSync(skillDir, { recursive: true });
      const skillFile = join(skillDir, "SKILL.md");
      writeFileSync(skillFile, generateSkillMarkdown(facade), "utf-8");
      skillsGenerated++;
    }
  }

  return { commandsGenerated, skillsGenerated, cleaned, commandNames };
}

function countGeneratedFiles(dir: string, type: "md" | "skillDir"): number {
  if (!existsSync(dir)) return 0;
  let count = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (type === "md" && entry.isFile() && entry.name.endsWith(".md")) {
      if (isGeneratedFile(join(dir, entry.name))) count++;
    } else if (type === "skillDir" && entry.isDirectory()) {
      const skillFile = join(dir, entry.name, "SKILL.md");
      if (existsSync(skillFile) && isGeneratedFile(skillFile)) count++;
    }
  }
  return count;
}

// ── Facade Summary (for context injection) ──

/**
 * Format a facade registry summary for stdout injection.
 * Claude Code's SessionStart hook captures stdout as `<system-reminder>` context,
 * so the LLM knows which facades are available in the current session.
 */
export function formatFacadeSummary(registry: FacadeRegistry): string {
  const commands = registry.commands();

  if (commands.length === 0) {
    return "[clawmind:facades] No facades configured.\n";
  }

  const lines: string[] = [
    `[clawmind:facades] Available dynamic facades (${commands.length} commands):`,
  ];

  for (const cmd of commands) {
    const facade = registry.resolve(cmd)!;
    const source =
      facade.source === "db"
        ? `source: db${facade.remark ? `, remark: ${facade.remark}` : ""}`
        : `pack: ${facade.packId}`;
    lines.push(`  /${cmd} → ${facade.defaultWorkflow} (${source})`);
  }

  lines.push("");
  lines.push(
    "When user types /{facade-name} {args}, call workflow_engine_dispatch with command=\"run {workflowId} {args}\" — append user args as positional text after the workflowId, NOT as --input flag.",
  );

  return lines.join("\n");
}