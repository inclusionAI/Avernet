import React from 'react';
import { artifactViewHref } from '../artifactDescriptor';
import { formatArtifactUpdatedAt } from '../artifactTime';
import { ExternalLink } from '../icons';
import { C } from '../tokens';
import type { TaskArtifactView } from '../types';
import './TaskPanelV2.css';

function FileIcon({ artifact }: { artifact: TaskArtifactView }) {
  const extension = artifact.name.split('.').at(-1)?.toLowerCase();
  const sheet = ['xls', 'xlsx', 'csv'].includes(extension ?? '');
  const text = artifact.kind === 'text' || ['md', 'txt'].includes(extension ?? '');
  const color = sheet || text ? C.success : C.primary;
  return (
    <svg width="20" height="24" viewBox="0 0 20 24" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d="M3 0h9l8 8v13a3 3 0 0 1-3 3H3a3 3 0 0 1-3-3V3a3 3 0 0 1 3-3Z" fill={color} />
      <path d="M12 0v6a2 2 0 0 0 2 2h6Z" fill="white" fillOpacity=".45" />
      <text x="9" y="19" textAnchor="middle" fill="white" fontSize="9" fontFamily="inherit" fontWeight="600">
        {sheet ? 'X' : text ? 'M' : 'F'}
      </text>
    </svg>
  );
}

export interface ArtifactListItemProps {
  artifact: TaskArtifactView;
  downloading: boolean;
  onPreview: () => void;
  onDownload: () => void;
}

/** The row opens the existing reader; hover actions never replace authenticated downloads. */
export function ArtifactListItem({ artifact, downloading, onPreview, onDownload }: ArtifactListItemProps) {
  const href = artifactViewHref(artifact);
  const file = artifact.kind === 'file' && artifact.fileRef != null;
  const preview = file || artifact.kind === 'text';
  const nodeLabel = artifact.nodeId ? `节点 ${artifact.nodeId}` : null;
  const stage = artifact.isFinalArtifact ? '最终产物' : nodeLabel;
  const meta = [
    artifact.ownerLabel ?? artifact.owner,
    formatArtifactUpdatedAt(artifact.updatedAt),
    artifact.isFinalArtifact ? nodeLabel : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const mainStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    minWidth: 0,
    flex: 1,
    padding: 0,
    border: 0,
    background: 'transparent',
    color: 'inherit',
    font: 'inherit',
    textAlign: 'left',
    textDecoration: 'none',
    cursor: preview || href ? 'pointer' : 'default',
  };
  const body = (
    <>
      <FileIcon artifact={artifact} />
      <span style={{ minWidth: 0, flex: 1 }}>
        <span
          title={artifact.name}
          style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontSize: 14,
            fontWeight: 500,
            lineHeight: '22px',
            color: C.textPrimary,
          }}
        >
          {artifact.name}
        </span>
        <span
          title={meta}
          style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontSize: 13,
            lineHeight: '22px',
            color: C.textSecondary,
          }}
        >
          {meta}
          {meta && stage ? ' · ' : ''}
          {stage ? <span data-testid="artifact-stage">{stage}</span> : null}
          {!meta && !stage ? '暂无来源信息' : null}
        </span>
      </span>
    </>
  );
  return (
    <li className="task-artifact-row" data-final-artifact={artifact.isFinalArtifact || undefined}>
      {preview ? (
        <button type="button" aria-label={`预览产物 ${artifact.name}`} onClick={onPreview} style={mainStyle}>
          {body}
        </button>
      ) : href ? (
        <a
          href={href}
          aria-label={`跳转产物 ${artifact.name}`}
          title={`打开产物:${artifact.name}`}
          target="_blank"
          rel="noreferrer"
          style={mainStyle}
        >
          {body}
        </a>
      ) : (
        <div style={mainStyle}>{body}</div>
      )}
      <span className="task-artifact-actions">
        {file ? (
          <button
            className="task-artifact-icon"
            type="button"
            aria-label={`下载产物 ${artifact.name}`}
            title={downloading ? '下载中…' : '下载产物'}
            disabled={downloading}
            onClick={onDownload}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              aria-hidden="true"
            >
              <path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />
            </svg>
          </button>
        ) : null}
        {preview ? (
          <button
            className="task-artifact-icon"
            type="button"
            aria-label={`打开产物 ${artifact.name}`}
            title="打开产物"
            onClick={onPreview}
          >
            <ExternalLink size={18} />
          </button>
        ) : href ? (
          <a
            className="task-artifact-icon"
            href={href}
            aria-label={`打开产物 ${artifact.name}`}
            title="打开产物"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={18} />
          </a>
        ) : null}
      </span>
    </li>
  );
}
