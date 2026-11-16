import type { TodoArtifact } from '@/domain/todos/models';
import type { SessionFilePreviewState } from '@/pages/Workspace/hooks/useSessionFilePreview';
import {
  getFileExt,
  getPreviewKind,
  prettifyJsonText,
  SESSION_FILE_TEXT_PREVIEW_MAX_BYTES,
} from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export type TodoFileArtifact = Extract<TodoArtifact, { kind: 'file' }>;

function readBlobText(blob: Blob): Promise<string> {
  // jsdom 的 Blob 可能没有 text；同时保留旧环境兜底，浏览器优先走原生 Blob.text。
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('预览文件读取失败'));
    reader.readAsText(blob);
  });
}

const initialPreview: SessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
  text: null,
  truncated: false,
  errorMessage: null,
};

export function useTodoFilePreview(
  artifact: TodoFileArtifact | null,
  readArtifact: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>,
): SessionFilePreviewState {
  const [preview, setPreview] = useState<SessionFilePreviewState>(initialPreview);
  const artifactId = artifact?.artifactId ?? null;
  const fileName = artifact?.content.fileName ?? '';
  const fileType = artifact?.content.fileType ?? '';

  useEffect(() => {
    if (!artifact || !artifactId) {
      setPreview(initialPreview);
      return;
    }

    const kind = getPreviewKind(fileName, fileType);
    if (kind === 'other') {
      setPreview({ ...initialPreview, kind });
      return;
    }

    let cancelled = false;
    setPreview({ ...initialPreview, kind, status: 'loading' });
    void readArtifact(artifactId, 'inline')
      .then(async (blob) => {
        if (cancelled) return;

        if (kind === 'text') {
          const truncated = blob.size > SESSION_FILE_TEXT_PREVIEW_MAX_BYTES;
          const boundedBlob = truncated ? blob.slice(0, SESSION_FILE_TEXT_PREVIEW_MAX_BYTES) : blob;
          const raw = await readBlobText(boundedBlob);
          if (cancelled) return;
          const text = getFileExt(fileName) === 'json' ? prettifyJsonText(raw) : raw;
          setPreview({
            kind,
            status: 'ready',
            contentUrl: null,
            text,
            truncated,
            errorMessage: null,
          });
          return;
        }

        setPreview({
          kind,
          status: 'ready',
          contentUrl: URL.createObjectURL(blob),
          text: null,
          truncated: false,
          errorMessage: null,
        });
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setPreview({
          ...initialPreview,
          kind,
          status: 'error',
          errorMessage: cause instanceof Error ? cause.message : '预览加载失败',
        });
      });
    return () => {
      cancelled = true;
    };
  }, [artifact, artifactId, fileName, fileType, readArtifact]);

  useEffect(() => {
    const contentUrl = preview.contentUrl;
    return () => {
      if (contentUrl) URL.revokeObjectURL(contentUrl);
    };
  }, [preview.contentUrl]);

  return preview;
}
