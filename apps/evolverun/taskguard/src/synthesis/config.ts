/**
 * Synthesis configuration loader.
 *
 * Reads the `dynamicWorkflow.synthesis` section from application.yaml,
 * falling back to built-in defaults when keys are absent.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { parse as parseYaml } from "yaml";
import type { SynthesisConfig } from "../types.js";

// ── Built-in defaults ──

const SYNTHESIS_DEFAULTS: SynthesisConfig = {
  defaultModel: "",
  defaultTemperature: 0.3,
  defaultMaxTokens: 8192,
  maxGoalLength: 2000,
  defaultMaxCorrections: 3,
  allowedExecutors: [
    "embedded-agent",
    "done",
    "subagent",
    "dynamic-template",
    "goal-evaluator",
    "llm-orchestrator",
    "loop-group",
    "action",
    "goal-loop",
  ],
  extraAllowedExecutors: [],
  maxNodeCount: 50,
  secretPatterns: [
    "sk-[A-Za-z0-9]{32,}",
    "(?i)(password|passwd|token)\\s*[=:]\\s*['\"]?[^\\s'\"]{8,}",
  ],
  forbiddenTemplatePaths: ["process.env", "require", "eval"],
  humanApproval: {
    strategy: "on-warning",
    warningTriggers: {
      nodeCountExceeds: 20,
      budgetExceeds: 100000,
      usesDynamicExecutors: true,
      hasLoopGroup: true,
    },
  },
};

// ── YAML shape ──

type YamlSynthesisShape = {
  defaultModel?: string;
  defaultTemperature?: number;
  defaultMaxTokens?: number;
  maxGoalLength?: number;
  defaultMaxCorrections?: number;
  allowedExecutors?: string[];
  extraAllowedExecutors?: string[];
  maxNodeCount?: number;
  secretPatterns?: string[];
  forbiddenTemplatePaths?: string[];
  humanApproval?: {
    strategy?: "never" | "on-warning" | "always";
    warningTriggers?: {
      nodeCountExceeds?: number;
      budgetExceeds?: number;
      usesDynamicExecutors?: boolean;
      hasLoopGroup?: boolean;
    };
  };
};

type YamlDynamicWorkflow = {
  synthesis?: YamlSynthesisShape;
};

type YamlApp = {
  dynamicWorkflow?: YamlDynamicWorkflow;
};

// ── Config file resolution ──

function resolveConfigPath(): string {
  // Same search order as config/loader.ts
  const candidates = [
    process.env.CLF_CONFIG_FILE,
    join(process.cwd(), "configs", "application.yaml"),
    // Global config directory
  ];
  for (const c of candidates) {
    if (c && existsSync(c)) return c;
  }
  // Check well-known extension location
  const extPath = join(
    process.env.HOME ?? "",
    ".openclaw",
    "extensions",
    "clawmind",
    "configs",
    "application.yaml",
  );
  if (existsSync(extPath)) return extPath;
  return "";
}

// ── Public API ──

/** Load synthesis config from application.yaml, with built-in fallbacks. */
export function loadSynthesisConfig(): SynthesisConfig {
  const configPath = resolveConfigPath();
  if (!configPath) return { ...SYNTHESIS_DEFAULTS };

  try {
    const raw = readFileSync(configPath, "utf-8");
    const yaml = parseYaml(raw) as YamlApp | undefined;
    const s = yaml?.dynamicWorkflow?.synthesis ?? ({} as YamlSynthesisShape);
    const wt = s.humanApproval?.warningTriggers ?? {};
    const defaults = SYNTHESIS_DEFAULTS;

    return {
      defaultModel: s.defaultModel ?? defaults.defaultModel,
      defaultTemperature: s.defaultTemperature ?? defaults.defaultTemperature,
      defaultMaxTokens: s.defaultMaxTokens ?? defaults.defaultMaxTokens,
      maxGoalLength: s.maxGoalLength ?? defaults.maxGoalLength,
      defaultMaxCorrections: s.defaultMaxCorrections ?? defaults.defaultMaxCorrections,
      allowedExecutors: s.allowedExecutors ?? [...defaults.allowedExecutors],
      extraAllowedExecutors: s.extraAllowedExecutors ?? [],
      maxNodeCount: s.maxNodeCount ?? defaults.maxNodeCount,
      secretPatterns: s.secretPatterns ?? [...defaults.secretPatterns],
      forbiddenTemplatePaths: s.forbiddenTemplatePaths ?? [...defaults.forbiddenTemplatePaths],
      humanApproval: {
        strategy: s.humanApproval?.strategy ?? defaults.humanApproval.strategy,
        warningTriggers: {
          nodeCountExceeds: wt.nodeCountExceeds ?? defaults.humanApproval.warningTriggers.nodeCountExceeds,
          budgetExceeds: wt.budgetExceeds ?? defaults.humanApproval.warningTriggers.budgetExceeds,
          usesDynamicExecutors: wt.usesDynamicExecutors ?? defaults.humanApproval.warningTriggers.usesDynamicExecutors,
          hasLoopGroup: wt.hasLoopGroup ?? defaults.humanApproval.warningTriggers.hasLoopGroup,
        },
      },
    };
  } catch {
    return { ...SYNTHESIS_DEFAULTS };
  }
}

/** Built-in defaults for reference or direct use. */
export const SYNTHESIS_DEFAULTS_READONLY: Readonly<SynthesisConfig> = SYNTHESIS_DEFAULTS;