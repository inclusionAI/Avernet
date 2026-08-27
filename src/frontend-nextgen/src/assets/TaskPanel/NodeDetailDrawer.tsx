// @asset-migrated: teamclaw 自研资产
/** 节点详情抽屉：任务规格、执行上下文、步骤追踪和验收结果。 */
import MarkdownIt from 'markdown-it';
import React, { useEffect, useState } from 'react';
import { Close, NodeStatusIcon } from './icons';
import { Empty, LabelValue, SectionCard } from './theme';
import { C, NODE_STATUS_TONES } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { NodeStatus, TaskNodeView } from './types';

const NODE_STATUS_LABELS: Record<NodeStatus, string> = {
  done: '已完成',
  running: '执行中',
  failed: '失败',
  pending: '待执行',
  skipped: '已跳过',
};

const StepItem: React.FC<{ step: TaskNodeView['stepTraces'][number]; index: number; isLast: boolean }> = ({
  step,
  index,
  isLast,
}) => (
  <div style={{ display: 'flex', gap: 10, minWidth: 0 }}>
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20, flexShrink: 0 }}>
      <div
        style={{
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: step.type === 'tool_call' ? C.primaryBg : C.surfaceAlt,
          color: step.type === 'tool_call' ? C.primary : C.textSecondary,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 10,
          fontWeight: 650,
        }}
      >
        {index + 1}
      </div>
      {!isLast && <div style={{ width: 1, flex: 1, minHeight: 22, margin: '4px 0', background: C.border }} />}
    </div>
    <div style={{ flex: 1, minWidth: 0, padding: '1px 0 12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span
          style={{
            minWidth: 0,
            color: C.textPrimary,
            fontSize: 12,
            fontWeight: 600,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {step.title}
        </span>
        <span style={{ color: C.textMuted, fontSize: 10, whiteSpace: 'nowrap' }}>{step.timestamp}</span>
      </div>
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          marginTop: 5,
          padding: '2px 6px',
          borderRadius: 4,
          background: step.type === 'tool_call' ? C.primaryBg : C.surfaceAlt,
          color: step.type === 'tool_call' ? C.primary : C.textSecondary,
          fontSize: 10,
        }}
      >
        {step.toolName ?? step.type}
      </div>
      {step.content && (
        <div
          style={{
            marginTop: 6,
            color: C.textSecondary,
            fontSize: 11,
            lineHeight: 1.55,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {step.content}
        </div>
      )}
    </div>
  </div>
);

const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

/** 轻量 markdown 渲染单元格：输出摘要等长文本按 markdown 格式渲染。
 * 内容较多时默认折叠（max-height 截断 + 渐变遮罩），点击「展开全部」查看全文，避免淹没其它字段。 */
const COLLAPSED_HEIGHT = 120;
const EXPANDED_MAX_HEIGHT = 320;
const MarkdownCell: React.FC<{ content: string | null | undefined }> = ({ content }) => {
  const [expanded, setExpanded] = useState(false);
  if (!content || !content.trim()) {
    return <span style={{ color: C.textMuted }}>—</span>;
  }
  const html = md.render(content);
  // 折叠态：固定高度 + 底部渐变遮罩；展开态：全高显示
  const collapsed = !expanded;
  return (
    <div style={{ marginTop: 5, position: 'relative' }}>
      <div
        style={{
          color: C.textPrimary,
          fontSize: 12,
          lineHeight: 1.6,
          wordBreak: 'break-word',
          maxHeight: collapsed ? COLLAPSED_HEIGHT : EXPANDED_MAX_HEIGHT,
          overflow: 'auto',
          position: 'relative',
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {collapsed && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 0,
            height: 40,
            border: 0,
            background: `linear-gradient(to bottom, transparent, ${C.surfaceRaised})`,
            color: C.primary,
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
            paddingBottom: 2,
          }}
        >
          展开全部 ▾
        </button>
      )}
      {!collapsed && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          style={{
            marginTop: 6,
            border: 0,
            background: 'transparent',
            color: C.primary,
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            padding: 0,
          }}
        >
          收起 ▴
        </button>
      )}
    </div>
  );
};

const DetailField: React.FC<{ label: string; value?: React.ReactNode; wide?: boolean }> = ({
  label,
  value,
  wide = false,
}) => (
  <div
    style={{
      gridColumn: wide ? '1 / -1' : undefined,
      minWidth: 0,
      padding: '9px 10px',
      border: `1px solid ${C.border}`,
      borderRadius: 8,
      background: C.surfaceRaised,
    }}
  >
    <div style={{ color: C.textMuted, fontSize: 10, lineHeight: 1.3 }}>{label}</div>
    <div
      style={{
        marginTop: 5,
        color: value ? C.textPrimary : C.textMuted,
        fontSize: 12,
        lineHeight: 1.5,
        wordBreak: 'break-word',
        whiteSpace: wide ? 'pre-wrap' : undefined,
      }}
    >
      {value || '—'}
    </div>
  </div>
);

export const NodeDetailDrawer: React.FC<{
  node: TaskNodeView | null;
  open: boolean;
  onClose: () => void;
}> = ({ node, open, onClose }) => {
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, open]);

  if (!node) return null;
  const steps = node.stepTraces;
  const toolCalls = steps.filter((step) => step.type === 'tool_call').length;
  const tone = NODE_STATUS_TONES[node.status] ?? NODE_STATUS_TONES.pending;
  const statusLabel = NODE_STATUS_LABELS[node.status];
  const taskSpec = node.taskSpec ?? {};
  const acceptanceCriteria = taskSpec.acceptances ?? [];
  const acceptanceValue =
    acceptanceCriteria.length > 0 ? (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {acceptanceCriteria.map((item, index) => (
          <div key={`${item}-${index}`} style={{ display: 'flex', alignItems: 'flex-start', gap: 7 }}>
            <span aria-hidden style={{ color: C.primary, lineHeight: 1.5, flexShrink: 0 }}>
              •
            </span>
            <span style={{ minWidth: 0 }}>{item}</span>
          </div>
        ))}
      </div>
    ) : undefined;

  return (
    <aside
      aria-label={`${node.name} 节点详情`}
      style={{
        position: 'fixed',
        top: 0,
        right: 0,
        width: 420,
        maxWidth: 'min(420px, 100vw)',
        height: '100vh',
        background: C.page,
        borderLeft: `1px solid ${C.border}`,
        boxShadow: '-14px 0 36px rgba(29, 33, 41, 0.14)',
        transform: open ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 300ms ease-in-out',
        zIndex: 999,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <header
        style={{
          padding: '16px 18px 14px',
          borderBottom: `1px solid ${C.border}`,
          background: `linear-gradient(135deg, ${C.surface} 0%, ${C.surfaceRaised} 100%)`,
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, minWidth: 0 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 30,
                height: 30,
                borderRadius: 9,
                background: tone.fill,
                flexShrink: 0,
              }}
            >
              <NodeStatusIcon status={node.status} size={15} />
            </div>
            <div style={{ minWidth: 0 }}>
              <TruncatedText
                value={node.name}
                maxLength={20}
                as="h2"
                style={{
                  margin: 0,
                  color: C.textPrimary,
                  fontSize: 16,
                  fontWeight: 650,
                  lineHeight: 1.35,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              />
              <div
                style={{
                  marginTop: 4,
                  color: C.textMuted,
                  fontSize: 10,
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {node.id}
              </div>
            </div>
          </div>
          <button
            type="button"
            aria-label="关闭节点详情"
            onClick={onClose}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 30,
              height: 30,
              border: 0,
              borderRadius: 8,
              background: 'transparent',
              color: C.textSecondary,
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <Close size={17} />
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14 }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '4px 8px',
              border: `1px solid ${tone.stroke}35`,
              borderRadius: 6,
              background: tone.fill,
              color: tone.stroke,
              fontSize: 10,
              fontWeight: 650,
            }}
          >
            <NodeStatusIcon status={node.status} size={11} />
            {statusLabel}
          </span>
          <span style={{ color: C.textSecondary, fontSize: 11 }}>{node.runMode ?? '未标记执行模态'}</span>
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 14 }}>
        <SectionCard title="基本信息">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
            <DetailField label="执行器" value={node.executor} />
            <DetailField label="执行模态" value={node.runMode} />
            <DetailField label="开始时间" value={node.startedAt} />
            <DetailField label="结束时间" value={node.endAt} />
            <DetailField label="耗时" value={node.timeConsuming} />
            <DetailField
              label="Tokens"
              value={node.tokens !== null && node.tokens !== undefined ? node.tokens.toLocaleString() : undefined}
            />
            <DetailField label="输出摘要" value={<MarkdownCell content={node.outputSummary ?? node.output} />} wide />
          </div>
        </SectionCard>

        <SectionCard title="任务信息" marginTop={12}>
          <div style={{ display: 'grid', gap: 8 }}>
            <DetailField label="标题" value={<TruncatedText value={taskSpec.title ?? node.name} maxLength={20} />} />
            <DetailField label="执行指令" value={taskSpec.instruction} wide />
            <DetailField label="目标" value={taskSpec.target} wide />
            <DetailField label="验收标准" value={acceptanceValue} wide />
          </div>
        </SectionCard>

        <SectionCard title="步骤追踪" marginTop={12}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 14,
              padding: '8px 10px',
              borderRadius: 8,
              background: C.surfaceRaised,
              color: C.textSecondary,
              fontSize: 11,
            }}
          >
            <span>
              <strong style={{ color: C.textPrimary, fontWeight: 650 }}>{steps.length}</strong> 步
            </span>
            <span style={{ color: C.textMuted }}>·</span>
            <span>
              <strong style={{ color: C.textPrimary, fontWeight: 650 }}>{toolCalls}</strong> 次工具动作
            </span>
          </div>
          {steps.length > 0 ? (
            steps.map((step, index) => (
              <StepItem key={step.id} step={step} index={index} isLast={index === steps.length - 1} />
            ))
          ) : (
            <Empty description="暂无步骤追踪数据" minHeight={80} />
          )}
        </SectionCard>

        {node.acceptanceResult && (
          <SectionCard title="验收结果" marginTop={12}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                marginBottom: 10,
                padding: '8px 10px',
                borderRadius: 8,
                background: node.acceptanceResult.verdict === 'PASS' ? `${C.success}12` : `${C.danger}12`,
                color: node.acceptanceResult.verdict === 'PASS' ? C.success : C.danger,
                fontSize: 12,
                fontWeight: 650,
              }}
            >
              {node.acceptanceResult.verdict === 'PASS' ? '✓ 验收通过' : '！验收未通过'}
            </div>
            {node.acceptanceResult.gaps.length > 0 && (
              <LabelValue
                label="差距"
                value={node.acceptanceResult.gaps.map((item, index) => (
                  <div key={index} style={{ color: C.warning }}>
                    ! {item}
                  </div>
                ))}
              />
            )}
          </SectionCard>
        )}
      </div>
    </aside>
  );
};
