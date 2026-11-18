/** Pure validation of complete Pack inventories and their workflow document. */
import { JSON_SCHEMA, load } from 'js-yaml';
import { authoringRecord, authoringText, authoringWorkflowId, parseWorkflowAuthoringYaml, workflowAuthoringSignature } from './workflowAuthoring';
import type { WorkflowAuthoringFile, WorkflowAuthoringSnapshot, WorkflowAuthoringSpec, WorkflowPublishChange } from './workflowAuthoringTypes';
export const authoringCommitPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
export function authoringCommit(value: unknown): string {
  const text = authoringText(value, 'Git commit');
  if (!authoringCommitPattern.test(text)) throw new Error('Git commit 无效');
  return text;
}
export function authoringToken(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) throw new Error('草稿 token 无效');
  return value;
}
function path(value: unknown): string {
  const text = authoringText(value, 'Pack 文件路径');
  if (/^[A-Za-z]:/.test(text) || /[\\\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(text) || text.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')) throw new Error('Pack 文件路径无效');
  return text;
}
export function decodeWorkflowAuthoringFile(file: WorkflowAuthoringFile): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(file.contentBase64), char => char.charCodeAt(0)));
}
export function authoringSnapshot(value: unknown, id: string, version: 'draft' | 'current'): WorkflowAuthoringSnapshot {
  const row = authoringRecord(value);
  if (row.workflowId !== id || !Array.isArray(row.versionFiles)) throw new Error('Pack 快照身份无效');
  const packId = authoringWorkflowId(row.packId), baseCommit = authoringCommit(row.baseCommit);
  const draftHead = row.draftHead === null ? null : authoringCommit(row.draftHead);
  const baseRef = authoringText(row.baseRef, 'Git ref');
  if (version === 'draft' ? baseRef !== `origin/wf/${id}` || draftHead !== baseCommit : !baseRef.startsWith(`refs/tags/deploy/${id}/#`) || !/^[1-9][0-9]*$/.test(baseRef.slice(`refs/tags/deploy/${id}/#`.length))) throw new Error('Pack ref 归属无效');
  if (row.packPath !== undefined && typeof row.packPath !== 'string') throw new Error('Pack 范围无效');
  if (typeof row.packPath === 'string' && row.packPath !== '') path(row.packPath.replace(/\/$/, ''));
  const seen = new Set<string>();
  const versionFiles = row.versionFiles.map((raw): WorkflowAuthoringFile => {
    const item = authoringRecord(raw), filename = path(item.path);
    if (seen.has(filename) || !['100644', '100755'].includes(String(item.mode)) || typeof item.contentBase64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(item.contentBase64)) throw new Error('Pack 文件格式无效');
    if (btoa(atob(item.contentBase64)) !== item.contentBase64) throw new Error('Pack Base64 无效');
    seen.add(filename);
    return { path: filename, contentBase64: item.contentBase64, mode: item.mode as WorkflowAuthoringFile['mode'] };
  });
  for (const file of seen) {
    const parts = file.split('/');
    for (let i = 1; i < parts.length; i++) if (seen.has(parts.slice(0, i).join('/'))) throw new Error('Pack 路径存在冲突');
  }
  return { workflowId: id, packId, baseRef, baseCommit, draftHead, versionFiles,
    ...(typeof row.packPath === 'string' ? { packPath: row.packPath } : {}) };
}
export function workflowFromAuthoringPack(snapshot: WorkflowAuthoringSnapshot): { spec: WorkflowAuthoringSpec; path: string } {
  const manifestFile = snapshot.versionFiles.find(file => file.path === 'workflow.pack.yaml');
  if (!manifestFile) throw new Error('Pack manifest 缺失');
  const manifest = authoringRecord(load(decodeWorkflowAuthoringFile(manifestFile), { schema: JSON_SCHEMA }));
  if (manifest.id !== snapshot.packId || !Array.isArray(manifest.workflows)) throw new Error('Pack manifest 身份无效');
  const entries = manifest.workflows.filter(entry => typeof entry === 'string' ? entry === snapshot.workflowId : authoringRecord(entry).id === snapshot.workflowId);
  if (entries.length !== 1) throw new Error('Pack 必须唯一声明工作流');
  const filename = path(typeof entries[0] === 'string' ? `workflows/${snapshot.workflowId}.yaml` : authoringRecord(entries[0]).file);
  const file = snapshot.versionFiles.find(item => item.path === filename);
  if (!file) throw new Error('Pack 工作流文件缺失');
  const spec = parseWorkflowAuthoringYaml(decodeWorkflowAuthoringFile(file));
  if (spec.id !== snapshot.workflowId) throw new Error('Pack 工作流身份无效');
  return { spec, path: filename };
}
export function sameAuthoringExecution(a: WorkflowAuthoringSpec, b: WorkflowAuthoringSpec): boolean {
  const execution = (spec: WorkflowAuthoringSpec) => ({ ...spec, nodes: spec.nodes.map(source => {
    const node = { ...source };
    delete node._x;
    delete node._y;
    return node;
  }) });
  return workflowAuthoringSignature(execution(a)) === workflowAuthoringSignature(execution(b));
}
export function authoringFileChanges(before: WorkflowAuthoringFile[], after: WorkflowAuthoringFile[]): WorkflowPublishChange[] {
  const a = new Map(before.map(file => [file.path, file])), b = new Map(after.map(file => [file.path, file]));
  const changes: WorkflowPublishChange[] = [];
  for (const name of new Set([...a.keys(), ...b.keys()])) {
    const old = a.get(name), next = b.get(name);
    if (old?.contentBase64 === next?.contentBase64 && old?.mode === next?.mode) continue;
    const content = (file: WorkflowAuthoringFile | undefined) => {
      if (!file) return { text: null, encoding: 'utf-8' as const };
      try {
        const text = decodeWorkflowAuthoringFile(file);
        if (text.includes('\u0000')) throw new Error('binary');
        return { text, encoding: 'utf-8' as const };
      } catch {
        return { text: file.contentBase64, encoding: 'base64' as const };
      }
    };
    const previous = content(old), candidate = content(next);
    changes.push({ path: name, operation: !next ? 'delete' : old ? 'replace' : 'create',
      before: previous.text, after: candidate.text,
      beforeMode: old?.mode ?? null, afterMode: next?.mode ?? null,
      beforeEncoding: previous.encoding, afterEncoding: candidate.encoding });
  }
  return changes;
}
