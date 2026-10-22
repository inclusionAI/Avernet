// @asset-migrated: teamclaw 自研资产
/**
 * 产物 V2 归一化 —— Descriptor(读侧富化,kind-tagged)与 legacy TaskArtifactDto
 * 双口径统一收敛为 TaskArtifactView。契约差异在 mapper 层收口(spec §3.1-3):
 * 后端 task-artifact 读侧未富化时 Descriptor 为 null,自然回落 legacy,零切换动作。
 */
import type { TaskArtifactDescriptorDto, TaskArtifactDto } from './contract';
import type { TaskArtifactView } from './types';

function str(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}

function normalizeStorageType(value: unknown): 'bot_workspace' | 'session_file' | null {
  const normalized = str(value)?.toLowerCase();
  return normalized === 'bot_workspace' || normalized === 'session_file' ? normalized : null;
}

function msToIso(ms: number): string {
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? String(ms) : d.toISOString();
}

/** kind → legacy type 口径(渲染徽章复用既有 ARTIFACT_TYPE_LABELS) */
function kindToType(kind: TaskArtifactDescriptorDto['kind']): TaskArtifactView['type'] {
  switch (kind) {
    case 'text':
      return 'document';
    case 'file':
      return 'file';
    case 'url_link':
      return 'link';
    case 'code':
      return 'other';
  }
}

export function normalizeDescriptor(
  d: TaskArtifactDescriptorDto,
  fallbackNodeId: string | null,
): TaskArtifactView {
  const c: Record<string, unknown> = d.content ?? {};
  const name =
    d.artifact_name ?? (d.kind === 'file' ? str(c.file_name) : null) ?? d.artifact_id;
  const storageType = normalizeStorageType(c.storage_type);
  const storageId = str(c.storage_id);
  const fileRef =
    d.kind === 'file' && storageType && storageId
      ? {
          storageType,
          storageId,
          fileId: str(c.file_id),
          fileType: str(c.file_type),
        }
      : null;
  return {
    id: d.artifact_id,
    name,
    type: kindToType(d.kind),
    kind: d.kind,
    // url_link → 业务链接;code → pr_link 承载产物内容本身(后端 spec §3.4 边界澄清)
    url: d.kind === 'url_link' ? str(c.url) : d.kind === 'code' ? str(c.pr_link) : null,
    summary: d.kind === 'text' ? str(c.text) : d.kind === 'code' ? str(c.pr_desc) : null,
    nodeId: fallbackNodeId,
    owner: d.creator ?? null,
    updatedAt: msToIso(d.created_at),
    fileRef,
  };
}

function legacyToView(a: TaskArtifactDto, fallbackNodeId: string | null): TaskArtifactView {
  return {
    id: a.artifact_id,
    name: a.name,
    type: a.type,
    kind: null,
    url: a.url ?? null,
    summary: a.summary ?? null,
    nodeId: a.node_id ?? fallbackNodeId,
    owner: null,
    updatedAt: a.created_at,
    fileRef: null,
  };
}

/**
 * 合并节点产物:Descriptor(读侧富化,优先/信任新口径)与 legacy 口径按 id 去重。
 * 后端未富化时传入 descriptors=null/undefined,仅回落 legacy。
 */
export function mergeArtifactLists(
  descriptors: TaskArtifactDescriptorDto[] | null | undefined,
  legacy: TaskArtifactDto[] | undefined,
  fallbackNodeId: string | null,
): TaskArtifactView[] {
  const merged = new Map<string, TaskArtifactView>();
  for (const d of descriptors ?? []) {
    merged.set(d.artifact_id, normalizeDescriptor(d, fallbackNodeId));
  }
  for (const a of legacy ?? []) {
    if (!merged.has(a.artifact_id)) {
      merged.set(a.artifact_id, legacyToView(a, fallbackNodeId));
    }
  }
  return [...merged.values()];
}

/**
 * 可直接作为外链打开的产物地址。Descriptor file 需要携带任务 owner/bot 上下文，
 * 统一交给 useTaskArtifactPreview 调用既有资源服务，禁止在这里猜测 HTTP 路径。
 */
export function artifactViewHref(a: TaskArtifactView): string | null {
  if (a.kind === 'url_link' || a.kind === 'code') return a.url ?? null;
  if (a.kind === 'file') return null;
  // legacy 口径:backend 已给现成 url 则直接用
  return a.url ?? null;
}
