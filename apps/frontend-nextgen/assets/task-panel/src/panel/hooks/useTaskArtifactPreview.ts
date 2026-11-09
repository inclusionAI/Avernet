import { useEffect, useState } from 'react';
import type { ArtifactOwner, TaskPanelHost } from '../../contracts';
import { getFileExt, getPreviewKind, prettifyJsonText, SESSION_FILE_TEXT_PREVIEW_MAX_BYTES, type SessionFilePreviewKind } from '../../data/filePreview';
import { useTaskPanelHost } from '../hostContext';
import type { TaskArtifactView } from '../types';
export type TaskArtifactAccessContext = ArtifactOwner & { host?: TaskPanelHost | null };
export interface TaskArtifactPreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  contentUrl: string | null; text: string | null; truncated: boolean; size: number | null; errorMessage: string | null;
}
const stateFor = (kind: SessionFilePreviewKind, status: TaskArtifactPreviewState['status']): TaskArtifactPreviewState =>
  ({ kind, status, contentUrl: null, text: null, truncated: false, size: null, errorMessage: null });
function friendlyError(error: unknown): string {
  const status = (error as { status?: number })?.status;
  const friendly = (error as { friendlyMessage?: string })?.friendlyMessage;
  if (friendly) return friendly;
  if (status === 401) return '登录状态已失效，请重新登录后重试。';
  if (status === 403) return '无权访问该产物。';
  if (status === 404) return '产物文件不存在或已被删除。';
  if (status === 413) return '产物文件过大，请下载后查看。';
  return '预览加载失败，请稍后重试。';
}
function readText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error); reader.readAsText(blob);
  });
}
export function useTaskArtifactPreview(artifact: TaskArtifactView | null, context: TaskArtifactAccessContext): TaskArtifactPreviewState {
  const injected = useTaskPanelHost(); const host = context.host ?? injected;
  const [state, setState] = useState(() => stateFor('other', 'unsupported'));
  useEffect(() => {
    if (!artifact) { setState(stateFor('other', 'unsupported')); return; }
    if (artifact.kind === 'text') {
      const text = artifact.summary ?? '';
      setState({ ...stateFor('text', 'ready'), text, size: new Blob([text]).size }); return;
    }
    const ref = artifact.kind === 'file' ? artifact.fileRef : null;
    const kind = getPreviewKind(artifact.name, ref?.fileType ?? undefined);
    if (!ref || !ref.storageId || kind === 'other') { setState(stateFor(kind, 'unsupported')); return; }
    if (ref.storageType === 'session_file' && !ref.fileId) {
      setState({ ...stateFor(kind, 'error'), errorMessage: '会话文件引用不完整，无法预览。' }); return;
    }
    if (!host) { setState({ ...stateFor(kind, 'error'), errorMessage: '宿主未提供产物访问能力。' }); return; }
    const directUrl = host.artifacts.contentUrl?.(artifact) ?? null;
    if (directUrl && kind !== 'text') { setState({ ...stateFor(kind, 'ready'), contentUrl: directUrl }); return; }
    const ctrl = new AbortController(); let url: string | null = null;
    setState(stateFor(kind, 'loading'));
    void (async () => {
      try {
        const blob = await host.artifacts.readContent(artifact, context, ctrl.signal);
        if (ctrl.signal.aborted) return;
        if (kind === 'text') {
          const truncated = blob.size > SESSION_FILE_TEXT_PREVIEW_MAX_BYTES;
          const text = await readText(truncated ? blob.slice(0, SESSION_FILE_TEXT_PREVIEW_MAX_BYTES) : blob);
          if (!ctrl.signal.aborted) setState({ ...stateFor(kind, 'ready'), text: getFileExt(artifact.name) === 'json' ? prettifyJsonText(text) : text, contentUrl: directUrl, truncated, size: blob.size });
        } else {
          url = URL.createObjectURL(blob);
          if (ctrl.signal.aborted) { URL.revokeObjectURL(url); url = null; return; }
          setState({ ...stateFor(kind, 'ready'), contentUrl: url, size: blob.size });
        }
      } catch (error) {
        if (!ctrl.signal.aborted) setState({ ...stateFor(kind, 'error'), errorMessage: friendlyError(error) });
      }
    })();
    return () => { ctrl.abort(); if (url) URL.revokeObjectURL(url); };
  }, [artifact, context.taskId, context.ownerBotId, context.ownerUserId, host]);
  return state;
}
export async function downloadTaskArtifact(artifact: TaskArtifactView, context: TaskArtifactAccessContext): Promise<void> {
  if (artifact.kind === 'text') throw new Error('纯文本类型的产物不支持下载。');
  if (!artifact.fileRef) throw new Error('产物文件引用不完整，无法下载。');
  if (artifact.fileRef.storageType === 'session_file' && !artifact.fileRef.fileId) throw new Error('会话文件引用不完整，无法下载。');
  if (!context.host) throw new Error('宿主未提供产物下载能力。');
  try { await context.host.artifacts.download(artifact, context, new AbortController().signal); }
  catch (error) { throw new Error(friendlyError(error)); }
}
