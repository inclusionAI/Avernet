import { createHash } from "node:crypto";
import JSZip from "jszip";
import fixtureV1 from "../../resources/evolve/stage-test-fixtures/skill-description-v1.json" with { type: "json" };
import fixtureV2 from "../../resources/evolve/stage-test-fixtures/skill-description-v2.json" with { type: "json" };
import fixtureV3 from "../../resources/evolve/stage-test-fixtures/skill-description-v3.json" with { type: "json" };
import type { StageExtensionMode, StageKey } from "./stage-catalog.js";
import type { FrozenDiagnosePlanSource } from "./plan-business.js";
import { getArtifactBucket, type ObjectStore } from "../object-storage/oss-object-store.js";

const FIXTURES = {
  1: { id: "skill-description-v1", name: "stage-test-text-summary", files: fixtureV1 },
  2: { id: "skill-description-v2", name: "stage-test-text-summary", files: fixtureV2 },
  3: { id: "skill-description-v3", name: "daily-report-zh", files: fixtureV3 },
} as const;
type FixtureVersion = keyof typeof FIXTURES;
const CURRENT_FIXTURE_VERSION: FixtureVersion = 2;

export type FrozenStageTestFixture = {
  kind: "stage_test_fixture";
  fixtureId: (typeof FIXTURES)[FixtureVersion]["id"];
  version: FixtureVersion;
  taskId: string;
  sha256: string;
  ref: string;
};

export function usesStageTestFixture(flow: string | undefined, stage: StageKey, mode: StageExtensionMode): boolean {
  return flow === "skill_evolution"
    && ((stage === "diagnose" && mode === "preprocess") || (stage === "plan" && mode === "replace"));
}

/** Only the explicit Plan business Source path gets v3; never infer a Skill from prose/history. */
export function selectStageTestFixtureVersion(
  flow: string | undefined, stage: StageKey, mode: StageExtensionMode,
  planSource?: FrozenDiagnosePlanSource,
): FixtureVersion | undefined {
  if (!usesStageTestFixture(flow, stage, mode)) return undefined;
  if (stage !== "plan" || !planSource) return CURRENT_FIXTURE_VERSION;
  const cases = planSource.delivery.content.cases;
  const matchesSkill = (query: unknown) => typeof query === "string"
    && /^\s*\/daily-report-zh(?:\s|$)/u.test(query)
    && Array.from(query.matchAll(/(?:^|\s)\/([A-Za-z0-9][A-Za-z0-9_-]*)(?=\s|$)/gu))
      .every((call) => call[1] === FIXTURES[3].name);
  if (!cases.length || cases.some((item) => !matchesSkill(item.query)
    || (item.context?.original_query !== undefined && !matchesSkill(item.context.original_query)))) {
    throw new Error("固定测试 Skill daily-report-zh 需要每个真实 Source 案例明确以 /daily-report-zh 调用；来源不匹配或无法证实，不能创建测试");
  }
  return 3;
}

function fixtureObjectKey(taskId: string, version: FixtureVersion): string {
  if (!/^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$/.test(taskId) || taskId.includes("..")) {
    throw new Error("Stage 测试 Task 标识不安全");
  }
  if (!Object.hasOwn(FIXTURES, version)) throw new Error("Stage 测试 fixture 版本不一致");
  return `evolve/stage-tests/${taskId}/fixtures/${FIXTURES[version].id}/package.zip`;
}

/** A real test input package, never a precomputed Stage result or an OCB asset. */
export async function createStageTestFixturePackage(version: FixtureVersion = CURRENT_FIXTURE_VERSION) {
  // JSON import is copied by tsc; a raw Markdown read would be missing in dist.
  const content = Buffer.from(FIXTURES[version].files["SKILL.md"], "utf8");
  const zip = new JSZip();
  zip.file("SKILL.md", content, {
    date: new Date("1980-01-01T00:00:00.000Z"),
    unixPermissions: 0o100644,
    createFolders: false,
  });
  const packageBytes = await zip.generateAsync({
    type: "nodebuffer", platform: "UNIX", compression: "DEFLATE", compressionOptions: { level: 6 },
  });
  return {
    fixtureId: FIXTURES[version].id,
    version,
    packageBytes,
    sha256: createHash("sha256").update(packageBytes).digest("hex"),
  };
}

export async function freezeStageTestFixture(
  taskId: string,
  putObject: NonNullable<ObjectStore["putObject"]>,
  version: FixtureVersion = CURRENT_FIXTURE_VERSION,
): Promise<FrozenStageTestFixture> {
  const objectKey = fixtureObjectKey(taskId, version);
  const fixture = await createStageTestFixturePackage(version);
  await putObject(objectKey, fixture.packageBytes, "application/zip");
  return {
    kind: "stage_test_fixture", fixtureId: fixture.fixtureId, version: fixture.version,
    taskId, sha256: fixture.sha256, ref: `oss://${getArtifactBucket()}/${objectKey}`,
  };
}

/** Resolve only the frozen task resource, never a request path or a live OCB Skill. */
export function stageTestFixtureInput(taskId: string, fixture: FrozenStageTestFixture) {
  const objectKey = fixtureObjectKey(taskId, fixture.version);
  if (fixture.kind !== "stage_test_fixture" || fixture.fixtureId !== FIXTURES[fixture.version].id
    || fixture.taskId !== taskId
    || !/^[a-f0-9]{64}$/.test(fixture.sha256)
    || fixture.ref !== `oss://${getArtifactBucket()}/${objectKey}`) {
    throw new Error("Stage 测试 fixture 与冻结的任务资源不一致");
  }
  const workspace = `/home/admin/.openclaw/clawevolve_workspaces/${taskId}/workspace`;
  const skillName = FIXTURES[fixture.version].name;
  return {
    kind: "stage_test_fixture" as const,
    fixture_id: fixture.fixtureId,
    asset_id: `fixture:${taskId}:${fixture.fixtureId}`,
    skill_id: `fixture:${fixture.fixtureId}`,
    name: skillName,
    workspace,
    path: `${workspace}/skills/skills-local/${skillName}`,
    baseline_sha256: fixture.sha256,
  };
}
