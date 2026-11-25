import type {
  EditorAccess,
  EditorDiff,
  EditorHistory,
  EditorNode,
  EditorPack,
  EditorRelease,
  EditorSnapshot,
  EditorSpec,
} from '@/domain/taskEscortParity/editorTypes';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';

function fail(label: string): never {
  throw new Error(`${label}响应格式无效，未展示不可信数据`);
}
export function editorRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail('编辑器');
  return value as Record<string, unknown>;
}
export function editorPayload(value: unknown): unknown {
  const row = editorRecord(value);
  // A bare open spec may itself contain arbitrary data/error extension keys.
  if (typeof row.id === 'string' && Array.isArray(row.nodes)) return row;
  if ('code' in row || 'success' in row) {
    if (!isEnvelopeSuccessAnyDialect(row)) throw new Error(String(row.message ?? row.error ?? '编辑器读取失败'));
    return row.data;
  }
  if (typeof row.error === 'string') throw new Error(String(row.message ?? row.error));
  return row;
}
function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) return fail(label);
  return value;
}
function integer(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) return fail(label);
  return value;
}
function identity(row: Record<string, unknown>, id: string, required = true) {
  if ((required || row.workflowId !== undefined) && row.workflowId !== id) throw new Error('响应不属于当前工作流');
}
export function mapEditorSpec(value: unknown, id: string): EditorSpec {
  const row = editorRecord(value);
  if (row.id !== id) throw new Error('规格不属于当前工作流');
  text(row.title, '工作流名称');
  if (
    row.version !== undefined &&
    typeof row.version !== 'string' &&
    !(typeof row.version === 'number' && Number.isFinite(row.version) && row.version >= 0)
  )
    return fail('工作流版本');
  if (!Array.isArray(row.nodes)) return fail('节点列表');
  const ids = new Set<string>();
  const nodes = row.nodes.map((value) => {
    const node = editorRecord(value),
      executor = editorRecord(node.executor);
    const nodeId = text(node.id, '节点 ID');
    if (ids.has(nodeId)) return fail('重复节点 ID');
    ids.add(nodeId);
    text(executor.type, '执行器类型');
    if (node.title !== undefined && typeof node.title !== 'string') return fail('节点名称');
    if (
      node.dependsOn !== undefined &&
      (!Array.isArray(node.dependsOn) || !node.dependsOn.every((id) => typeof id === 'string' && id.trim()))
    )
      return fail('节点依赖');
    return { ...node, executor: { ...executor } } as EditorNode;
  });
  return { ...row, nodes } as EditorSpec;
}
export function mapEditorAccess(value: unknown, id: string): EditorAccess {
  const row = editorRecord(value);
  identity(row, id);
  if (typeof row.canView !== 'boolean' || typeof row.canEdit !== 'boolean') return fail('访问权限');
  return { workflowId: id, canView: row.canView, canEdit: row.canEdit };
}
function release(value: unknown): EditorRelease {
  const row = editorRecord(value);
  integer(row.deployNumber, '发布序号');
  integer(row.version, '发布版本');
  text(row.action, '发布动作');
  if (row.isActive !== undefined && typeof row.isActive !== 'boolean') return fail('生效状态');
  if (
    row.gmtCreate !== undefined &&
    (typeof row.gmtCreate !== 'number' || !Number.isFinite(row.gmtCreate) || row.gmtCreate < 0)
  )
    return fail('发布时间');
  if (row.note !== null && row.note !== undefined && typeof row.note !== 'string') return fail('发布备注');
  return { ...row } as EditorRelease;
}
export function mapEditorHistory(value: unknown, id: string): EditorHistory {
  const row = editorRecord(value);
  identity(row, id);
  if (!Array.isArray(row.history) || !('active' in row)) return fail('发布历史');
  const history = row.history.map(release).filter((item) => item.action !== 'edit');
  if (new Set(history.map((item) => item.deployNumber)).size !== history.length) return fail('重复发布序号');
  return { workflowId: id, history, active: row.active === null ? null : release(row.active) };
}
export function mapEditorSnapshot(value: unknown, id: string, deploy: number, nested = false): EditorSnapshot {
  const row = editorRecord(value);
  identity(row, id, !nested);
  const item = release(row);
  if (item.deployNumber !== deploy) throw new Error('快照不属于所选发布');
  text(row.specJson, '发布快照');
  // Legacy snapshots can contain a YAML wrapper; do not synthesize a graph from them.
  return { ...item, specJson: row.specJson as string };
}
export function mapEditorDiff(value: unknown, id: string, from: number, to: number): EditorDiff {
  const row = editorRecord(value);
  identity(row, id);
  return {
    workflowId: id,
    from: mapEditorSnapshot(row.from, id, from, true),
    to: mapEditorSnapshot(row.to, id, to, true),
  };
}
export function mapEditorPack(value: unknown, id: string): EditorPack {
  const row = editorRecord(value);
  identity(row, id);
  const packId = text(row.packId, 'Pack ID'),
    baseRef = text(row.baseRef, 'Git 分支'),
    baseCommit = text(row.baseCommit, 'Git commit');
  if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(baseCommit) || !Array.isArray(row.files) || row.files.length > 500)
    return fail('Pack 快照');
  const paths = new Set<string>();
  const files = row.files.map((value) => {
    const file = editorRecord(value),
      path = text(file.path, '脚本路径');
    if (!/^scripts\/(?:[A-Za-z0-9_][A-Za-z0-9_.-]*\/)*[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(path) || paths.has(path))
      return fail('脚本路径');
    paths.add(path);
    if (
      typeof file.content !== 'string' ||
      file.content.length > 1024 * 1024 ||
      !['100644', '100755'].includes(String(file.mode))
    )
      return fail('脚本文件');
    return { path, content: file.content, mode: file.mode as '100644' | '100755' };
  });
  return { workflowId: id, packId, baseRef, baseCommit, files };
}
