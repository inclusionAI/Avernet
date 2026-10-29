// @asset-migrated: teamclaw 自研资产
/**
 * ArtifactsSection —— 产物区(PRD #产物):任务级聚合(或 L2 按 node 过滤)后的产物卡列表。
 * Descriptor file/text 走站内预览与下载；链接/代码为外链型产物，仅保留跳转入口。
 */
import React, { useState } from 'react';

import { artifactViewHref } from '../artifactDescriptor';
import { formatArtifactUpdatedAt } from '../artifactTime';
import { downloadTaskArtifact, type TaskArtifactAccessContext } from '../hooks/useTaskArtifactPreview';
import { ExternalLink } from '../icons';
import { Empty as StateEmpty } from '../theme';
import { ARTIFACT_KIND_LABELS, ARTIFACT_TYPE_LABELS, C } from '../tokens';
import type { TaskArtifactView } from '../types';
import { TaskArtifactPreviewDrawer } from './TaskArtifactPreviewDrawer';

export interface ArtifactsSectionProps {
  artifacts: TaskArtifactView[];
  apiBaseUrl: string;
  context: TaskArtifactAccessContext;
  /** 区块展示标题 */
  title?: string;
  /** 外部 Drawer 宿主，用于让预览留在同一模态交互层内。 */
  previewPortalContainer?: HTMLElement | null;
}

const actionStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  boxSizing: 'border-box',
  minWidth: 40,
  minHeight: 24,
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  background: C.surface,
  color: C.primary,
  fontFamily: 'inherit',
  fontSize: 11,
  lineHeight: '16px',
  padding: '3px 8px',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

export const ArtifactsSection: React.FC<ArtifactsSectionProps> = ({
  artifacts,
  context,
  title = '产物',
  previewPortalContainer,
}) => {
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;

  const download = async (artifact: TaskArtifactView) => {
    setDownloadingId(artifact.id);
    setDownloadError(null);
    try {
      await downloadTaskArtifact(artifact, context);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '产物下载失败，请稍后重试。');
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <>
      <section data-testid="artifacts-section" style={{ borderTop: `1px solid ${C.border}` }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px 0',
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 600, color: C.textPrimary }}>{title}</span>
          <span title="产物计数" style={{ fontSize: 11, color: C.textSecondary }}>
            {artifacts.length} 项
          </span>
        </div>
        {downloadError && (
          <div role="alert" style={{ padding: '8px 16px 0', color: C.danger, fontSize: 11 }}>
            {downloadError}
          </div>
        )}
        {artifacts.length === 0 ? (
          <StateEmpty description="暂无产物" minHeight={120} />
        ) : (
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: '12px 16px 16px',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            {artifacts.map((a) => {
              const badge = a.kind ? ARTIFACT_KIND_LABELS[a.kind] ?? a.kind : ARTIFACT_TYPE_LABELS[a.type] ?? a.type;
              const href = artifactViewHref(a);
              const time = formatArtifactUpdatedAt(a.updatedAt);
              const isFileArtifact = a.kind === 'file' && a.fileRef !== null && a.fileRef !== undefined;
              const isInlineArtifact = a.kind === 'text';
              const isDetailArtifact =
                a.kind === 'url_link' ||
                a.kind === 'code' ||
                ((a.kind === null || a.kind === undefined) && a.type === 'link');
              const isExternalOnlyArtifact =
                a.kind === 'url_link' || a.kind === 'code' || a.type === 'link';
              const hasPreviewActions = isInlineArtifact || isFileArtifact || isDetailArtifact;
              const hasPreviewButton = hasPreviewActions && !isExternalOnlyArtifact;
              const hasDownloadAction = isFileArtifact;
              return (
                <li
                  key={a.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 10px',
                    borderRadius: 8,
                    border: `1px solid ${C.border}`,
                    background: C.surface,
                  }}
                >
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 11,
                      color: C.textSecondary,
                      background: C.surfaceAlt,
                      border: `1px solid ${C.border}`,
                      borderRadius: 4,
                      padding: '1px 6px',
                      lineHeight: '18px',
                    }}
                  >
                    {badge}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        color: C.textPrimary,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {a.name}
                    </div>
                    <div style={{ fontSize: 11, color: C.textSecondary, marginTop: 2 }}>
                      {[a.owner, time].filter(Boolean).join(' · ')}
                    </div>
                    {a.kind === 'text' && a.summary && (
                      <div
                        style={{
                          fontSize: 11,
                          color: C.textSecondary,
                          marginTop: 4,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                        title={a.summary}
                      >
                        {a.summary}
                      </div>
                    )}
                  </div>
                  {hasPreviewActions ? (
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                      {hasPreviewButton ? (
                        <button
                          type="button"
                          aria-label={`预览产物 ${a.name}`}
                          onClick={() => setSelectedArtifactId(a.id)}
                          style={actionStyle}
                        >
                          预览
                        </button>
                      ) : null}
                      {hasDownloadAction ? (
                        <button
                          type="button"
                          aria-label={`下载产物 ${a.name}`}
                          disabled={downloadingId === a.id}
                          onClick={() => void download(a)}
                          style={{ ...actionStyle, opacity: downloadingId === a.id ? 0.6 : 1 }}
                        >
                          {downloadingId === a.id ? '下载中…' : '下载'}
                        </button>
                      ) : isInlineArtifact ? (
                        <span aria-hidden="true" style={{ ...actionStyle, visibility: 'hidden' }}>
                          下载
                        </span>
                      ) : null}
                      {href ? (
                        <a
                          href={href}
                          aria-label={`跳转产物 ${a.name}`}
                          title={`打开产物:${a.name}`}
                          target="_blank"
                          rel="noreferrer"
                          style={{ ...actionStyle, textDecoration: 'none' }}
                        >
                          跳转
                        </a>
                      ) : null}
                    </div>
                  ) : href ? (
                    <a
                      href={href}
                      title={`打开产物:${a.name}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 11,
                        color: C.primary,
                        flexShrink: 0,
                      }}
                    >
                      <ExternalLink size={12} />
                    </a>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {selectedArtifact && (
        <TaskArtifactPreviewDrawer
          artifact={selectedArtifact}
          context={context}
          onClose={() => setSelectedArtifactId(null)}
          portalContainer={previewPortalContainer}
        />
      )}
    </>
  );
};

export default ArtifactsSection;
