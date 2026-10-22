// @asset-migrated: teamclaw 自研资产
/** Task file artifact 站内预览抽屉；仅依赖 TaskPanel 与既有 Service，避免反向依赖 Workspace 页面。 */
import { formatFileSize, getFileExt } from '@/services/workspace/sessionFileUtils';
import MarkdownIt from 'markdown-it';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import {
  downloadTaskArtifact,
  type TaskArtifactAccessContext,
  useTaskArtifactPreview,
} from '../hooks/useTaskArtifactPreview';
import { Close } from '../icons';
import { SafeHtml } from '../SafeHtml';
import { sanitizeMarkdownHtml } from '../sanitizeHtml';
import { C } from '../tokens';
import type { TaskArtifactView } from '../types';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });

export interface TaskArtifactPreviewDrawerProps {
  artifact: TaskArtifactView;
  context: TaskArtifactAccessContext;
  onClose: () => void;
  /** 嵌套在宿主 Drawer 时将预览挂到宿主内部，避免外层模态层拦截滚动与点击。 */
  portalContainer?: HTMLElement | null;
}

const actionStyle: React.CSSProperties = {
  minHeight: 32,
  padding: '0 14px',
  borderRadius: 6,
  border: `1px solid ${C.border}`,
  background: C.surface,
  color: C.textPrimary,
  cursor: 'pointer',
  fontSize: 12,
  fontWeight: 500,
};

function PreviewMessage({ children, danger = false }: { children: React.ReactNode; danger?: boolean }) {
  return (
    <div
      role={danger ? 'alert' : 'status'}
      style={{
        minHeight: 240,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        textAlign: 'center',
        color: danger ? C.danger : C.textSecondary,
        fontSize: 13,
      }}
    >
      {children}
    </div>
  );
}

export const TaskArtifactPreviewDrawer: React.FC<TaskArtifactPreviewDrawerProps> = ({
  artifact,
  context,
  onClose,
  portalContainer,
}) => {
  const preview = useTaskArtifactPreview(artifact, context);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const fileType = artifact.fileRef?.fileType ?? '';
  const isMarkdown = getFileExt(artifact.name) === 'md' || fileType.toLowerCase().startsWith('text/markdown');
  const markdownHtml = useMemo(
    () => (isMarkdown && preview.text !== null ? sanitizeMarkdownHtml(markdown.render(preview.text)) : null),
    [isMarkdown, preview.text],
  );

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    setDownloadError(null);
  }, [artifact.id]);

  const download = async () => {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadTaskArtifact(artifact, context);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '产物下载失败，请稍后重试。');
    } finally {
      setDownloading(false);
    }
  };

  const body = (() => {
    if (preview.status === 'loading') return <PreviewMessage>正在加载产物预览…</PreviewMessage>;
    if (preview.status === 'error') {
      return <PreviewMessage danger>{preview.errorMessage ?? '预览加载失败，请稍后重试。'}</PreviewMessage>;
    }
    if (preview.status === 'unsupported') {
      return <PreviewMessage>该文件类型暂不支持站内预览，请下载后查看。</PreviewMessage>;
    }
    if (preview.kind === 'image' && preview.contentUrl) {
      return (
        <div style={{ display: 'flex', minHeight: 240, alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <img
            src={preview.contentUrl}
            alt={artifact.name}
            style={{ display: 'block', maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain' }}
          />
        </div>
      );
    }
    if (preview.kind === 'video' && preview.contentUrl) {
      return (
        <div style={{ display: 'flex', minHeight: 240, alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <video controls src={preview.contentUrl} style={{ display: 'block', maxWidth: '100%', maxHeight: '70vh' }}>
            当前浏览器不支持视频预览。
          </video>
        </div>
      );
    }
    if ((preview.kind === 'pdf' || preview.kind === 'html') && preview.contentUrl) {
      return (
        <iframe
          src={preview.contentUrl}
          title={`${artifact.name} 预览`}
          sandbox={preview.kind === 'html' ? '' : undefined}
          style={{ width: '100%', minHeight: 'calc(100vh - 180px)', border: 0, background: C.surface }}
        />
      );
    }
    if (preview.kind === 'text' && preview.text !== null) {
      return (
        <div className="flex min-h-0 flex-1 flex-col">
          {markdownHtml !== null ? (
            <SafeHtml
              html={markdownHtml}
              className="markdown-body app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-4 text-sm leading-6 text-foreground"
            />
          ) : (
            <pre
              data-testid="task-artifact-plain-text"
              className="app-scrollbar m-0 min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-all px-6 py-4 font-mono text-xs leading-6 text-foreground"
            >
              {preview.text}
            </pre>
          )}
          {preview.truncated ? (
            <div className="shrink-0 border-t border-border px-6 py-3 text-xs text-muted-foreground">
              内容较长，仅显示部分，请下载查看完整内容。
            </div>
          ) : null}
        </div>
      );
    }
    return <PreviewMessage>暂无可展示的预览内容，请下载后查看。</PreviewMessage>;
  })();

  if (typeof document === 'undefined') return null;
  const fileRef = artifact.fileRef;
  const isContained = portalContainer !== null && portalContainer !== undefined;
  const meta = [fileRef?.storageType, preview.size !== null ? formatFileSize(preview.size) : null]
    .filter(Boolean)
    .join(' · ');

  return createPortal(
    <div
      data-testid="task-artifact-preview-drawer"
      onMouseDown={onClose}
      style={{
        position: isContained ? 'absolute' : 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.4)',
      }}
    >
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-artifact-preview-title"
        aria-describedby="task-artifact-preview-description"
        onMouseDown={(event) => event.stopPropagation()}
        style={{
          position: 'absolute',
          inset: '0 0 0 auto',
          display: 'flex',
          flexDirection: 'column',
          width: isContained ? '100%' : 'min(640px, 100vw)',
          background: C.surface,
          color: C.textPrimary,
          boxShadow: '-8px 0 24px rgba(0, 0, 0, 0.16)',
        }}
      >
        <header
          style={{
            position: 'relative',
            flexShrink: 0,
            padding: '16px 52px 14px 20px',
            borderBottom: `1px solid ${C.border}`,
          }}
        >
          <div id="task-artifact-preview-title" style={{ fontSize: 15, fontWeight: 600, overflowWrap: 'anywhere' }}>
            {artifact.name}
          </div>
          <div
            id="task-artifact-preview-description"
            style={{ marginTop: 5, fontSize: 11, color: C.textSecondary, overflowWrap: 'anywhere' }}
          >
            {[fileRef?.storageId, meta].filter(Boolean).join(' · ')}
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="关闭产物预览"
            onClick={onClose}
            style={{ ...actionStyle, position: 'absolute', top: 12, right: 12, width: 32, padding: 0, border: 'none' }}
          >
            <Close size={16} />
          </button>
        </header>
        <div
          style={{
            display: 'flex',
            flex: 1,
            minHeight: 0,
            flexDirection: 'column',
            overflow: preview.kind === 'text' ? 'hidden' : 'auto',
          }}
        >
          {body}
        </div>
        <footer
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 10,
            flexShrink: 0,
            padding: '12px 20px',
            borderTop: `1px solid ${C.border}`,
          }}
        >
          {downloadError && (
            <span role="alert" style={{ marginRight: 'auto', color: C.danger, fontSize: 11 }}>
              {downloadError}
            </span>
          )}
          <button type="button" onClick={onClose} style={actionStyle}>
            关闭
          </button>
          <button
            type="button"
            onClick={() => void download()}
            disabled={downloading}
            style={{
              ...actionStyle,
              borderColor: C.primary,
              background: C.primary,
              color: C.surface,
              opacity: downloading ? 0.6 : 1,
            }}
          >
            {downloading ? '下载中…' : '下载文件'}
          </button>
        </footer>
      </aside>
    </div>,
    portalContainer ?? document.body,
  );
};

export default TaskArtifactPreviewDrawer;
