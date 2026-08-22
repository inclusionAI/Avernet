import type { ResolvedRequiredSkill } from "./skill-resolver.js";

type SnapshotRecord = Record<string, unknown>;

function isRecord(value: unknown): value is SnapshotRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cloneSnapshot<T extends SnapshotRecord>(snapshot: T): T {
  return structuredClone(snapshot);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeSkillRecord(skill: SnapshotRecord, requiredSkill: ResolvedRequiredSkill): boolean {
  if (skill.name !== requiredSkill.name) return false;

  skill.baseDir = requiredSkill.skillDir;
  skill.filePath = requiredSkill.skillFile;

  if (isRecord(skill.sourceInfo)) {
    if ("baseDir" in skill.sourceInfo) {
      skill.sourceInfo.baseDir = requiredSkill.skillDir;
    }
    if ("filePath" in skill.sourceInfo) {
      skill.sourceInfo.filePath = requiredSkill.skillFile;
    }
  }

  return true;
}

function normalizePromptLocation(prompt: string, requiredSkill: ResolvedRequiredSkill): {
  prompt: string;
  matched: boolean;
} {
  const skillBlockPattern = /<skill>[\s\S]*?<\/skill>/g;
  const namePattern = new RegExp(`<name>\\s*${escapeRegExp(requiredSkill.name)}\\s*<\\/name>`);
  const locationPattern = /<location>[\s\S]*?<\/location>/;
  let matched = false;

  const normalizedPrompt = prompt.replace(skillBlockPattern, (skillBlock) => {
    if (!namePattern.test(skillBlock) || !locationPattern.test(skillBlock)) {
      return skillBlock;
    }

    matched = true;
    return skillBlock.replace(
      locationPattern,
      () => `<location>${requiredSkill.skillFile}</location>`,
    );
  });

  return { prompt: normalizedPrompt, matched };
}

function filterPromptToSkill(prompt: string, skillName: string): string {
  const escapedName = escapeRegExp(skillName);
  const allBlocksPattern = /<skill>[\s\S]*?<\/skill>/g;
  const namePattern = new RegExp(`<name>\\s*${escapedName}\\s*</name>`);
  let matched: string | undefined;
  for (const m of prompt.matchAll(allBlocksPattern)) {
    if (namePattern.test(m[0])) {
      matched = m[0].trim();
      break;
    }
  }
  if (!matched) {
    return "<available_skills>\n</available_skills>";
  }
  return `<available_skills>\n  ${matched}\n</available_skills>`;
}

export function filterSnapshotToRequiredSkill<T extends SnapshotRecord>(
  snapshot: T | undefined,
  requiredSkillName: string,
): T | undefined {
  if (snapshot === undefined) return undefined;

  const cloned = cloneSnapshot(snapshot) as SnapshotRecord;

  if (Array.isArray(cloned.resolvedSkills)) {
    cloned.resolvedSkills = cloned.resolvedSkills.filter(
      (skill) => isRecord(skill) && skill.name === requiredSkillName,
    );
  }

  if (Array.isArray(cloned.skills)) {
    cloned.skills = cloned.skills.filter(
      (skill) => isRecord(skill) && skill.name === requiredSkillName,
    );
  }

  cloned.skillFilter = [requiredSkillName];

  if (typeof cloned.prompt === "string") {
    cloned.prompt = filterPromptToSkill(cloned.prompt, requiredSkillName);
  }

  return cloned as T;
}

/**
 * Build a minimal skill snapshot from a resolved required skill when the
 * session's skillsSnapshot does not contain it.
 *
 * This fallback is used when `resolveRequiredSkill` succeeds (the skill
 * exists on disk) but `normalizeRequiredSkillSnapshotPaths` fails (the
 * session snapshot doesn't include the skill).  Two common scenarios:
 *
 *   1. The session was started before the skill was installed/linked, so
 *      the snapshot never included it.
 *   2. A human-node confirm triggered a snapshot rebuild that excluded
 *      workflow-pack skills.
 *
 * The fallback snapshot provides enough information for the OpenClaw
 * runtime to locate and inject the skill prompt and tools.
 */
export function buildFallbackSkillSnapshot(requiredSkill: ResolvedRequiredSkill): Record<string, unknown> {
  const skillName = requiredSkill.name;
  const skillPromptBlock = [
    "<available_skills>",
    "  <skill>",
    `    <name>${skillName}</name>`,
    `    <location>${requiredSkill.skillFile}</location>`,
    "  </skill>",
    "</available_skills>",
  ].join("\n");

  return {
    resolvedSkills: [
      {
        name: skillName,
        baseDir: requiredSkill.skillDir,
        filePath: requiredSkill.skillFile,
        source: requiredSkill.source,
        sourceInfo: {
          baseDir: requiredSkill.skillDir,
          filePath: requiredSkill.skillFile,
        },
      },
    ],
    skills: [
      { name: skillName },
    ],
    skillFilter: [skillName],
    prompt: skillPromptBlock,
  };
}

export function normalizeRequiredSkillSnapshotPaths<T extends SnapshotRecord>(
  snapshot: T | undefined,
  requiredSkill: ResolvedRequiredSkill,
): T | undefined {
  if (snapshot === undefined) return undefined;

  const cloned = cloneSnapshot(snapshot) as SnapshotRecord;
  let resolvedSkillMatched = false;

  if (Array.isArray(cloned.resolvedSkills)) {
    for (const skill of cloned.resolvedSkills) {
      if (isRecord(skill) && normalizeSkillRecord(skill, requiredSkill)) {
        resolvedSkillMatched = true;
      }
    }
  }

  if (typeof cloned.prompt === "string") {
    const normalizedPrompt = normalizePromptLocation(cloned.prompt, requiredSkill);
    cloned.prompt = normalizedPrompt.prompt;
  }

  if (!resolvedSkillMatched) {
    throw new Error(
      `当前会话未加载 skill：${requiredSkill.name}。请确认 skill 已链接后重启或刷新 OpenClaw 会话。`,
    );
  }

  return cloned as T;
}
