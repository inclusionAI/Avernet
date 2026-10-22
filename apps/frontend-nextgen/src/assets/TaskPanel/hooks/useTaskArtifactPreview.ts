// @asset-migrated: teamclaw 自研资产
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import {
  getFileExt,
  getPreviewKind,
  prettifyJsonText,
  SESSION_FILE_TEXT_PREVIEW_MAX_BYTES,
  type SessionFilePreviewKind,
} from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

import type { TaskArtifactView } from '../types';

export interface TaskArtifactAccessContext {
  taskId: string;
  ownerBotId: string;
  ownerUserId: string;
}

export interface TaskArtifactPreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  contentUrl: string | null;
  text: string | null;
  truncated: boolean;
  size: number | null;
  errorMessage: string | null;
}

function stateFor(kind: SessionFilePreviewKind, status: TaskArtifactPreviewState['status']): TaskArtifactPreviewState {
  return { kind, status, contentUrl: null, text: null, truncated: false, size: null, errorMessage: null };
}

function friendlyError(error: unknown, action: 'preview' | 'download'): string {
  const status = (error as { status?: number })?.status;
  if (status === 401) return '登录状态已失效，请重新登录后重试。';
  if (status === 403) return '无权访问该产物。';
  if (status === 404) return '产物文件不存在或已被删除。';
  if (status === 413) return '产物文件过大，请下载后查看。';
  return action === 'preview' ? '预览加载失败，请稍后重试。' : '产物下载失败，请稍后重试。';
}

async function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('read artifact failed'));
    reader.readAsText(blob);
  });
}

async function readTextPreview(blob: Blob, name: string) {
  const truncated = blob.size > SESSION_FILE_TEXT_PREVIEW_MAX_BYTES;
  const readable = truncated ? blob.slice(0, SESSION_FILE_TEXT_PREVIEW_MAX_BYTES) : blob;
  const raw = await blobText(readable);
  return {
    text: getFileExt(name) === 'json' ? prettifyJsonText(raw) : raw,
    truncated,
    size: blob.size,
  };
}

function clickDownload(href: string, fileName: string, openInNewTab = false): void {
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = fileName;
  if (openInNewTab) {
    anchor.target = '_blank';
    anchor.rel = 'noopener';
  }
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** 使用既有 session_files / bot workspace 服务加载 Task file artifact。 */
export function useTaskArtifactPreview(
  artifact: TaskArtifactView | null,
  context: TaskArtifactAccessContext,
): TaskArtifactPreviewState {
  const [state, setState] = useState<TaskArtifactPreviewState>(() => stateFor('other', 'unsupported'));
  const fileRef = artifact?.kind === 'file' ? artifact.fileRef : null;
  const name = artifact?.name ?? '';
  const storageType = fileRef?.storageType ?? null;
  const storageId = fileRef?.storageId ?? null;
  const fileId = fileRef?.fileId ?? null;
  const fileType = fileRef?.fileType ?? '';

  useEffect(() => {
    if (!artifact || !fileRef || !storageId) {
      setState(stateFor('other', 'unsupported'));
      return;
    }
    const kind = getPreviewKind(name, fileType);
    if (kind === 'other') {
      setState(stateFor(kind, 'unsupported'));
      return;
    }
    if (storageType === 'session_file' && !fileId) {
      setState({ ...stateFor(kind, 'error'), errorMessage: '会话文件引用不完整，无法预览。' });
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;
    setState(stateFor(kind, 'loading'));

    const load = async () => {
      try {
        if (storageType === 'session_file') {
          const contentUrl = sessionFileService.buildContentUrl(storageId, fileId!);
          if (kind !== 'text') {
            if (!cancelled) setState({ ...stateFor(kind, 'ready'), contentUrl });
            return;
          }
          const result = await sessionFileService.fetchContentBlob(storageId, fileId!);
          if (!result.ok) {
            if (!cancelled) {
              setState({ ...stateFor(kind, 'error'), errorMessage: result.error.friendlyMessage });
            }
            return;
          }
          const preview = await readTextPreview(result.data, name);
          if (!cancelled) setState({ ...stateFor(kind, 'ready'), contentUrl, ...preview });
          return;
        }

        if (kind === 'text') {
          const result = await botEditorService.previewResource(context.ownerBotId, storageId, context.ownerUserId);
          if (result.kind !== 'text') throw new Error('unexpected workspace preview kind');
          const preview = await readTextPreview(new Blob([result.content], { type: result.contentType }), name);
          if (!cancelled) setState({ ...stateFor(kind, 'ready'), ...preview });
          return;
        }

        const blob = await botEditorService.downloadResource(context.ownerBotId, storageId, context.ownerUserId);
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setState({ ...stateFor(kind, 'ready'), contentUrl: objectUrl, size: blob.size });
      } catch (error) {
        if (!cancelled) setState({ ...stateFor(kind, 'error'), errorMessage: friendlyError(error, 'preview') });
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact?.id, context.ownerBotId, context.ownerUserId, fileId, fileRef, fileType, name, storageId, storageType]);

  return state;
}

/** 下载 Task file artifact；session_file 走导航下载，workspace 走既有 Blob 下载服务。 */
export async function downloadTaskArtifact(
  artifact: TaskArtifactView,
  context: TaskArtifactAccessContext,
): Promise<void> {
  const fileRef = artifact.kind === 'file' ? artifact.fileRef : null;
  if (!fileRef) throw new Error('产物文件引用不完整，无法下载。');

  if (fileRef.storageType === 'session_file') {
    if (!fileRef.fileId) throw new Error('会话文件引用不完整，无法下载。');
    clickDownload(sessionFileService.buildDownloadUrl(fileRef.storageId, fileRef.fileId), artifact.name, true);
    return;
  }

  try {
    const blob = await botEditorService.downloadResource(context.ownerBotId, fileRef.storageId, context.ownerUserId);
    const objectUrl = URL.createObjectURL(blob);
    clickDownload(objectUrl, artifact.name);
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  } catch (error) {
    throw new Error(friendlyError(error, 'download'));
  }
}
