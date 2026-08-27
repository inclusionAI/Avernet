// @asset-migrated: teamclaw 自研资产
/** 产物 Tab（U5）：artifacts 卡片列表，按 type 显示图标，空态。 */
import React from 'react';
import { C, ARTIFACT_TYPE_LABELS } from './tokens';
import { Empty } from './theme';
import { ExternalLink } from './icons';
import type { TaskView } from './types';

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${m}月${day}日 ${hh}:${mm}`;
}

export const TaskArtifactsTab: React.FC<{ task: TaskView }> = ({ task }) => {
  if (!task.artifacts.length) {
    return <Empty description="暂无产物" />;
  }
  return (
    <div style={{ padding: 16 }}>
      {task.artifacts.map((a) => (
        <div
          key={a.id}
          style={{
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: 12,
            marginBottom: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            transition: 'all 0.2s ease',
            cursor: 'default',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-2px)';
            e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        >
          <div
            style={{
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              borderRadius: 6,
              background: C.surfaceAlt,
              fontSize: 11,
              fontWeight: 600,
              color: C.primary,
            }}
            title={ARTIFACT_TYPE_LABELS[a.type] ?? a.type}
          >
            {(ARTIFACT_TYPE_LABELS[a.type] ?? '?').slice(0, 1)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: C.textPrimary,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {a.name}
            </div>
            {a.summary && (
              <div style={{ fontSize: 12, color: C.textSecondary, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {a.summary}
              </div>
            )}
            <div style={{ fontSize: 12, color: C.textSecondary, marginTop: 2 }}>{formatTime(a.updatedAt)}</div>
          </div>
          {a.url && (
            <a
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              title="查看"
              style={{ flexShrink: 0, display: 'flex', alignItems: 'center', color: C.textSecondary, textDecoration: 'none' }}
            >
              <ExternalLink size={16} />
            </a>
          )}
        </div>
      ))}
    </div>
  );
};
