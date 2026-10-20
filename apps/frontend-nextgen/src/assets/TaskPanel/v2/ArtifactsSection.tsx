// @asset-migrated: teamclaw 自研资产
/**
 * ArtifactsSection —— 产物区(PRD #产物):任务级聚合(或 L2 按 node 过滤)后的产物卡列表。
 * 卡片:name + kind/类型徽章 + owner + 时间 + 可打开链接(Descriptor 口径 file 自行拼读口)。
 * L1/L2 两处复用,过滤职责在调用方(plan Row3 接口一致)。
 */
import React from 'react';

import { artifactViewHref } from '../artifactDescriptor';
import { ExternalLink } from '../icons';
import { Empty as StateEmpty } from '../theme';
import { ARTIFACT_KIND_LABELS, ARTIFACT_TYPE_LABELS, C } from '../tokens';
import type { TaskArtifactView } from '../types';

export interface ArtifactsSectionProps {
  artifacts: TaskArtifactView[];
  apiBaseUrl: string;
  /** 区块展示标题 */
  title?: string;
}

export const ArtifactsSection: React.FC<ArtifactsSectionProps> = ({ artifacts, apiBaseUrl, title = '产物' }) => (
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
    {artifacts.length === 0 ? (
      <StateEmpty description="暂无产物" minHeight={120} />
    ) : (
      <ul style={{ listStyle: 'none', margin: 0, padding: '12px 16px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {artifacts.map((a) => {
          const badge = a.kind ? ARTIFACT_KIND_LABELS[a.kind] ?? a.kind : ARTIFACT_TYPE_LABELS[a.type] ?? a.type;
          const href = artifactViewHref(a, apiBaseUrl);
          const time = a.updatedAt ? a.updatedAt.replace('T', ' ').slice(0, 16) : '';
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
                <div style={{ fontSize: 12, color: C.textPrimary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.name}
                </div>
                <div style={{ fontSize: 11, color: C.textSecondary, marginTop: 2 }}>
                  {[a.owner, time].filter(Boolean).join(' · ')}
                </div>
                {a.kind === 'text' && a.summary && (
                  <div
                    style={{ fontSize: 11, color: C.textSecondary, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={a.summary}
                  >
                    {a.summary}
                  </div>
                )}
              </div>
              {href && (
                <a
                  href={href}
                  title={`打开产物:${a.name}`}
                  target="_blank"
                  rel="noreferrer"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.primary, flexShrink: 0 }}
                >
                  <ExternalLink size={12} />
                </a>
              )}
            </li>
          );
        })}
      </ul>
    )}
  </section>
);

export default ArtifactsSection;