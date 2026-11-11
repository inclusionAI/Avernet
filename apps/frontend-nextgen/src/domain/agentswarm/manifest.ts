/**
 * The optional `config_manifest` a bot-kind agent may submit.
 *
 * The gateway takes this as a raw YAML **string** and passes it verbatim to the
 * platform's preflight, which stays authoritative for the grammar. So this module
 * does not attempt to be that validator: it catches what a person editing YAML by
 * hand actually gets wrong (syntax, wrong shape, the categories that must be
 * present) and lets the platform judge the rest.
 *
 * Blank is meaningful: an empty manifest means "let the platform synthesize the
 * identity-only persona document", which is the ordinary behaviour. That is why
 * this is opt-in per agent rather than a required field.
 */
import { load as parseYaml } from 'js-yaml';

/** The five content categories a five-category manifest carries. */
export const MANIFEST_CATEGORIES = ['identity', 'mcp', 'resources', 'skills', 'cli_tools'] as const;

export type ManifestCategory = (typeof MANIFEST_CATEGORIES)[number];

export const MANIFEST_TEMPLATE = `schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: |
        你是一个通用型助手 Agent。
        按任务目标执行工作，如实汇报结果，不臆测未提供的信息。
        遇到无法完成的要求时，说明原因并给出可行的替代方案。
  mcp: []
  resources: []
  skills: []
  cli_tools: []
script:
  body: echo manifest-provisioned`;


export interface ManifestIssue {
  /** Dotted path into the document, for pointing at the offending line. */
  path: string;
  message: string;
}

export interface ManifestValidation {
  ok: boolean;
  issues: ManifestIssue[];
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * Checks a manifest document. An empty document is valid — it means "no manifest",
 * not "an invalid manifest"; callers distinguish the two by `isBlankManifest`.
 */
export function validateManifest(source: string): ManifestValidation {
  const text = (source || '').trim();
  if (!text) return { ok: true, issues: [] };

  let parsed: unknown;
  try {
    parsed = parseYaml(text);
  } catch (reason) {
    return {
      ok: false,
      issues: [{ path: '', message: reason instanceof Error ? reason.message : 'YAML 语法错误' }],
    };
  }

  if (parsed === null || parsed === undefined) return { ok: true, issues: [] };
  if (!isRecord(parsed)) {
    return { ok: false, issues: [{ path: '', message: '顶层必须是键值映射（例如 schema_version / manifest）' }] };
  }

  const issues: ManifestIssue[] = [];

  // `schema_version` is the document's contract version; a missing one is the most
  // common hand-editing omission.
  if (parsed.schema_version === undefined) {
    issues.push({ path: 'schema_version', message: '缺少 schema_version（当前为 1）' });
  } else if (String(parsed.schema_version) !== '1') {
    issues.push({ path: 'schema_version', message: `不支持的 schema_version：${String(parsed.schema_version)}` });
  }

  const manifest = parsed.manifest;
  if (!isRecord(manifest)) {
    issues.push({ path: 'manifest', message: '缺少 manifest 块' });
    return { ok: issues.length === 0, issues };
  }

  // A category that is present must be a list; absent categories are allowed.
  // The platform's validator only requires `schema_version`, and the document it
  // synthesizes itself carries nothing but `identity` — so demanding all five
  // here would reject a manifest the platform accepts.
  for (const category of MANIFEST_CATEGORIES) {
    const value = manifest[category];
    if (value !== undefined && value !== null && !Array.isArray(value)) {
      issues.push({ path: `manifest.${category}`, message: `${category} 必须是列表` });
    }
  }

  // Every identity entry carries a type/content pair; an empty identity list would
  // produce a persona with no soul.
  if (Array.isArray(manifest.identity)) {
    if (manifest.identity.length === 0) {
      issues.push({ path: 'manifest.identity', message: '至少需要一条 identity（例如 SOUL.md）' });
    }
    manifest.identity.forEach((entry, index) => {
      if (!isRecord(entry)) {
        issues.push({ path: `manifest.identity[${index}]`, message: 'identity 条目必须是键值映射' });
        return;
      }
      if (typeof entry.type !== 'string' || !entry.type.trim()) {
        issues.push({ path: `manifest.identity[${index}].type`, message: '缺少 type' });
      }
      if (typeof entry.content !== 'string' || !entry.content.trim()) {
        issues.push({ path: `manifest.identity[${index}].content`, message: '缺少 content' });
      }
    });
  }

  if (parsed.script !== undefined && parsed.script !== null && !isRecord(parsed.script)) {
    issues.push({ path: 'script', message: 'script 必须是键值映射' });
  }

  return { ok: issues.length === 0, issues };
}

/** Blank manifest = let the platform synthesize the persona document. */
export function isBlankManifest(source: string | null | undefined): boolean {
  return !(source || '').trim();
}

/**
 * Normalizes what is sent: a blank document is omitted so the field never carries
 * an empty string to the gateway (which would be a present-but-blank manifest).
 */
export function normalizeManifest(source: string | null | undefined): string | undefined {
  const text = (source || '').trim();
  return text ? text : undefined;
}

export function summarizeIssues(issues: readonly ManifestIssue[]): string {
  if (!issues.length) return '';
  const [first] = issues;
  const where = first.path ? `${first.path}: ` : '';
  return issues.length === 1 ? `${where}${first.message}` : `${where}${first.message}（另有 ${issues.length - 1} 项）`;
}