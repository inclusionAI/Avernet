// @asset-migrated: teamclaw 自研资产
/** 产物 Tab（U5）：展示根节点（data.tasks[0].run_info）的任务产出内容。
 *  渲染逻辑与节点详情「输出摘要」一致：剥 HTTP 信封后按 markdown 渲染，长内容可折叠/展开。
 *  当节点 output 为空时，若仍有文件类 artifacts，则回退展示 artifacts 卡片列表。 */
import React from 'react';
import { ExternalLink } from './icons';
import { MarkdownCell } from './MarkdownCell';
import { Empty } from './theme';
import { ARTIFACT_TYPE_LABELS, C } from './tokens';
import type { TaskOutputDimension, TaskView } from './types';

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${m}月${day}日 ${hh}:${mm}`;
}

const ArtifactCard: React.FC<{ a: TaskView['artifacts'][number] }> = ({ a }) => (
  <div
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
        <div
          style={{
            fontSize: 12,
            color: C.textSecondary,
            marginTop: 2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
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
);

/** 单个产出维度卡片：不展示维度标题，仅渲染正文内容框。 */
const DimensionCard: React.FC<{ dimension: TaskOutputDimension }> = ({ dimension }) => (
  <div
    style={{
      background: C.surface,
      border: `1px solid ${C.border}`,
      borderRadius: 8,
      padding: 14,
    }}
  >
    <MarkdownCell content={dimension.content} />
  </div>
);

export const TaskArtifactsTab: React.FC<{ task: TaskView }> = ({ task }) => {
  const dimensions = task.rootOutputDimensions ?? [];
  const hasDimensions = dimensions.length > 0;
  const hasOutput = !!task.rootOutputRender && task.rootOutputRender.trim().length > 0;
  const hasArtifacts = task.artifacts.length > 0;

  if (!hasDimensions && !hasOutput && !hasArtifacts) {
    return <Empty description="暂无产物" />;
  }

  return (
    <div style={{ padding: 16 }}>
      {(hasDimensions || hasOutput) &&
        (hasDimensions ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: hasArtifacts ? 16 : 0 }}>
            {dimensions.map((d) => (
              <DimensionCard key={d.key} dimension={d} />
            ))}
          </div>
        ) : (
          <div
            style={{
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              padding: 14,
              marginBottom: hasArtifacts ? 16 : 0,
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 650, color: C.textPrimary, marginBottom: 4 }}>产出内容</div>
            <MarkdownCell content={task.rootOutputRender} />
          </div>
        ))}
      {hasArtifacts && (
        <div>
          {(hasDimensions || hasOutput) && (
            <div style={{ fontSize: 12, fontWeight: 650, color: C.textSecondary, margin: '4px 0 8px' }}>产物文件</div>
          )}
          {task.artifacts.map((a) => (
            <ArtifactCard key={a.id} a={a} />
          ))}
        </div>
      )}
    </div>
  );
};
