// @asset-migrated: teamclaw 自研资产
/** 节点视图：垂直执行时间线 + 可点击节点卡片 + 子任务下钻入口。 */
import { getCollaborationBotConversationUrl } from '@/utils/collaborationSquare';
import React from 'react';
import { ChevronRight, ExternalLink, Info, NodeStatusIcon } from './icons';
import { Empty } from './theme';
import { C } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskNodeView, TaskView } from './types';

const groupBadgeStyle: React.CSSProperties = {
  padding: '2px 5px',
  borderRadius: 4,
  background: `${C.primary}14`,
  color: C.primary,
  fontSize: 9,
  fontWeight: 650,
  whiteSpace: 'nowrap',
  flexShrink: 0,
};

const RUN_MODE_LABELS: Record<string, string> = {
  single_bot: '单Bot',
  coop_group: '协作群',
  bbs: 'bbs认领',
};
const subTaskBadgeStyle: React.CSSProperties = {
  padding: '2px 5px',
  borderRadius: 4,
  background: C.primaryBg,
  color: C.primary,
  fontSize: 9,
  fontWeight: 650,
  whiteSpace: 'nowrap',
  flexShrink: 0,
};

// 状态图标顶部与右侧节点卡片的上边缘对齐。
const NODE_ICON_TOP_OFFSET = 0;
const NODE_ICON_SIZE = 20;

const iconButtonStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 24,
  height: 24,
  border: 0,
  borderRadius: 6,
  background: 'transparent',
  color: C.textSecondary,
  cursor: 'pointer',
  flexShrink: 0,
};

export const NodeListView: React.FC<{
  nodes: TaskView['nodes'];
  ownerBotId?: string | null;
  userId?: string;
  onViewNodeDetail: (node: TaskNodeView) => void;
  onOpenSubTask?: (subTaskId: string) => void;
  onOpenGroupSession?: (node: TaskNodeView) => void;
}> = ({ nodes, ownerBotId, userId, onViewNodeDetail, onOpenSubTask, onOpenGroupSession }) => {
  if (!nodes.length) {
    return <Empty description="暂无执行节点" />;
  }

  const getConversationBotId = (node: TaskNodeView): string | null => {
    const botId = node.assignee?.trim() || ownerBotId?.trim();
    if (!botId) return null;
    return botId.includes(':') || !userId ? botId : `${botId}:${userId}`;
  };

  return (
    <div style={{ padding: '14px 12px 24px' }}>
      {nodes.map((node, idx) => {
        const isLast = idx === nodes.length - 1;
        const canOpenSub = Boolean(node.hasSubTask && node.subTaskId && onOpenSubTask);
        const canDrillSession = Boolean(node.sessionId) && Boolean(onOpenGroupSession);
        const runModeLabel = RUN_MODE_LABELS[node.runMode ?? ''];
        const executorLabel =
          node.runMode === 'coop_group' || node.groupId ? node.groupName ?? 'BCS协作群' : node.executor;
        const actionLabel = canOpenSub ? `打开子任务 ${node.name}` : `查看节点详情 ${node.name}`;
        const openNode = () => {
          if (canOpenSub && node.subTaskId) {
            onOpenSubTask?.(node.subTaskId);
          } else {
            onViewNodeDetail(node);
          }
        };

        return (
          <div
            key={node.id}
            style={{ display: 'flex', position: 'relative', alignItems: 'stretch', marginBottom: isLast ? 0 : 10 }}
          >
            <div style={{ position: 'relative', alignSelf: 'stretch', width: 28, flexShrink: 0 }}>
              <div
                style={{
                  position: 'absolute',
                  left: '50%',
                  top: NODE_ICON_TOP_OFFSET,
                  width: NODE_ICON_SIZE,
                  height: NODE_ICON_SIZE,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: '50%',
                  background: C.page,
                  transform: 'translateX(-50%)',
                  zIndex: 1,
                }}
              >
                <NodeStatusIcon status={node.status} size={NODE_ICON_SIZE} />
              </div>
              {!isLast && (
                <div
                  style={{
                    position: 'absolute',
                    left: '50%',
                    top: NODE_ICON_SIZE,
                    bottom: -10,
                    width: 2,
                    background: C.border,
                    transform: 'translateX(-50%)',
                  }}
                />
              )}
            </div>

            <div
              role="button"
              tabIndex={0}
              aria-label={actionLabel}
              onClick={openNode}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  openNode();
                }
              }}
              style={{
                flex: 1,
                minWidth: 0,
                marginLeft: 8,
                marginBottom: 0,
                padding: '12px 12px 11px',
                border: `1px solid ${canOpenSub ? C.primary + '35' : C.border}`,
                borderRadius: 10,
                background: canOpenSub ? `linear-gradient(135deg, ${C.primaryBg} 0%, ${C.surface} 72%)` : C.surface,
                boxShadow: canOpenSub ? '0 2px 10px rgba(22, 93, 255, 0.08)' : '0 1px 3px rgba(29, 33, 41, 0.04)',
                cursor: 'pointer',
                transition: 'border-color 150ms ease-out, box-shadow 150ms ease-out, transform 150ms ease-out',
              }}
              onMouseEnter={(event) => {
                event.currentTarget.style.borderColor = canOpenSub ? C.primary : C.primary + '80';
                event.currentTarget.style.boxShadow = '0 6px 18px rgba(29, 33, 41, 0.09)';
                event.currentTarget.style.transform = 'translateY(-1px)';
              }}
              onMouseLeave={(event) => {
                event.currentTarget.style.borderColor = canOpenSub ? C.primary + '35' : C.border;
                event.currentTarget.style.boxShadow = canOpenSub
                  ? '0 2px 10px rgba(22, 93, 255, 0.08)'
                  : '0 1px 3px rgba(29, 33, 41, 0.04)';
                event.currentTarget.style.transform = 'translateY(0)';
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                    <TruncatedText
                      value={node.name}
                      maxLength={20}
                      style={{
                        minWidth: 0,
                        color: C.textPrimary,
                        fontSize: 13,
                        fontWeight: 650,
                        lineHeight: '18px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    />
                    {runModeLabel && <span style={groupBadgeStyle}>{runModeLabel}</span>}
                    {canOpenSub && <span style={subTaskBadgeStyle}>子任务</span>}
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      marginTop: 5,
                      color: C.textMuted,
                      fontSize: 10,
                    }}
                  >
                    <span>{node.startedAt ?? (node.status === 'pending' ? '尚未开始' : '—')}</span>
                    {node.timeConsuming && <span>· 耗时 {node.timeConsuming}</span>}
                  </div>
                </div>
                {node.sessionId && getConversationBotId(node) && (
                  <a
                    href={getCollaborationBotConversationUrl(getConversationBotId(node)!, node.sessionId)}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`新开会话 ${node.name}`}
                    title="新开页面查看会话"
                    onClick={(event) => event.stopPropagation()}
                    className="inline-flex items-center justify-center rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <ExternalLink size={14} />
                  </a>
                )}
                <button
                  type="button"
                  aria-label={`查看 ${node.name} 详情`}
                  title="查看节点详情"
                  onClick={(event) => {
                    event.stopPropagation();
                    onViewNodeDetail(node);
                  }}
                  style={iconButtonStyle}
                >
                  <Info size={14} />
                </button>
              </div>

              {executorLabel && (
                <div
                  role={canDrillSession || canOpenSub ? 'button' : undefined}
                  tabIndex={canDrillSession || canOpenSub ? 0 : undefined}
                  aria-label={
                    canDrillSession
                      ? `查看执行会话 ${executorLabel}`
                      : canOpenSub
                      ? `打开子任务 ${executorLabel}`
                      : undefined
                  }
                  onClick={(event) => {
                    event.stopPropagation();
                    if (canDrillSession) {
                      onOpenGroupSession?.(node);
                    } else if (canOpenSub && node.subTaskId) {
                      onOpenSubTask?.(node.subTaskId);
                    }
                  }}
                  onKeyDown={(event) => {
                    if ((event.key === 'Enter' || event.key === ' ') && (canDrillSession || canOpenSub)) {
                      event.preventDefault();
                      if (canDrillSession) {
                        onOpenGroupSession?.(node);
                      } else if (node.subTaskId) {
                        onOpenSubTask?.(node.subTaskId);
                      }
                    }
                  }}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    marginTop: 9,
                    padding: '3px 7px 3px 4px',
                    borderRadius: 6,
                    background: canDrillSession ? `${C.primary}12` : canOpenSub ? `${C.primary}10` : C.surfaceAlt,
                    color: canDrillSession || canOpenSub ? C.primary : C.textSecondary,
                    fontSize: 11,
                    cursor: canDrillSession || canOpenSub ? 'pointer' : 'default',
                    transition: 'background 150ms ease-out, color 150ms ease-out',
                  }}
                  title={canDrillSession ? '点击查看执行会话' : canOpenSub ? '点击节点打开子任务' : executorLabel}
                >
                  <div
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: '50%',
                      background: node.executorColor ?? C.textSecondary,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: '#fff',
                      fontSize: 9,
                      flexShrink: 0,
                    }}
                  >
                    {executorLabel.slice(0, 1) || 'B'}
                  </div>
                  <span style={{ fontWeight: canDrillSession || canOpenSub ? 550 : 400 }}>{executorLabel}</span>
                  {(canDrillSession || canOpenSub) && <ChevronRight size={12} />}
                </div>
              )}

              {node.outputSummary && (
                <div
                  style={{
                    marginTop: 9,
                    color: C.textSecondary,
                    fontSize: 11,
                    lineHeight: 1.55,
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}
                >
                  {node.outputSummary}
                </div>
              )}

              {node.artifacts.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
                  {node.artifacts.map((artifact) => (
                    <div
                      key={artifact.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 7px',
                        border: `1px solid ${C.border}`,
                        borderRadius: 6,
                        background: C.surfaceRaised,
                        color: C.textSecondary,
                        fontSize: 11,
                      }}
                    >
                      <span style={{ color: C.textMuted, fontSize: 10 }}>附件</span>
                      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {artifact.name}
                      </span>
                      {artifact.url && (
                        <a
                          href={artifact.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={`打开 ${artifact.name}`}
                          onClick={(event) => event.stopPropagation()}
                          style={{ marginLeft: 'auto', color: C.primary, flexShrink: 0 }}
                        >
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};
