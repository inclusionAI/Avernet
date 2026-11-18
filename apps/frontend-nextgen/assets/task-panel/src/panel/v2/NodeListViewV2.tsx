// @asset-migrated: teamclaw 自研资产
/**
 * NodeListViewV2 —— L1 节点卡片列表(PRD 方案A):
 * 点根节点 = 选中并恢复宿主会话；点其他节点 = 选中并联动会话；
 * 节点本体仅做会话联动；独立图标下钻到节点副屏，不联动会话。
 * 行布局:present 状态环/任务说明(完成划线)/执行体/独立下钻/返工与 HITL 徽章/子任务入口。
 */
import React from 'react';

import { Users } from '../icons';
import { resolveNodeSessionTarget } from '../sessionResolution';
import { Empty as StateEmpty } from '../theme';
import { C, PANEL_CSS_VARIABLES, PRESENT_TONES } from '../tokens';
import type { TaskView } from '../types';
import type { TaskPanelNav } from './taskPanelNav';
import './TaskPanelV2.css';

export interface NodeListViewV2Props {
  task: TaskView;
  nav: TaskPanelNav;
  onOpenSubTask?: (subTaskId: string) => void;
}

const badgeStyle = (color: string): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  fontSize: 11,
  color,
  background: `${color}1A`,
  border: `1px solid ${color}40`,
  borderRadius: 999,
  padding: '1px 8px',
  lineHeight: '18px',
  whiteSpace: 'nowrap',
});

export const NodeListViewV2: React.FC<NodeListViewV2Props> = ({ task, nav, onOpenSubTask }) => {
  if (!task.nodes.length) {
    return <StateEmpty description="暂无任务节点" minHeight={200} />;
  }
  const rootNodeId = task.nodes[0]?.id;
  return (
    <ul
      style={{
        ...PANEL_CSS_VARIABLES,
        listStyle: 'none',
        margin: 0,
        padding: '12px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      {task.nodes.map((node, stageIndex) => {
        const present = node.present ?? node.status;
        const tone = PRESENT_TONES[present] ?? { label: present, color: C.textMuted };
        const stateColor = present === 'running' ? C.primary : tone.color;
        const isDone = present === 'done' || present === 'merged';
        const isGroup = node.runMode === 'coop_group' || node.runMode === 'bbs' || node.drillable === true;
        const executorName =
          node.executorLabel || node.assigneeName || (isGroup ? node.groupName : null) || node.executor || '—';
        const selected = nav.selectedNodeId === node.id;
        const target = resolveNodeSessionTarget(node, task);
        // 下钻必须有节点自身的会话与映射身份，不能借用会话导航的任务级 fallback。
        const canDrill = Boolean(node.sessionId?.trim() && (node.drillable === true || node.assignee?.trim()));
        const drillReason = '节点缺少会话或执行者信息，无法下钻';
        const stageName = `第${stageIndex + 1}棒 ${node.name}`;
        const selectForConversation = () => {
          if (node.id === rootNodeId) nav.selectRootNode(node.id);
          else nav.selectNode(node.id, target);
        };
        return (
          <li
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={`选中节点 ${stageName}`}
            onClick={selectForConversation}
            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return;
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectForConversation();
              }
            }}
            className="task-node-row"
            aria-pressed={selected}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              padding: '14px 12px',
              borderRadius: 8,
              background: selected ? C.surfaceHover : undefined,
              cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
          >
            <span
              role="img"
              aria-label={tone.label}
              className={present === 'running' ? 'task-node-state-running' : undefined}
              style={{
                display: 'inline-block',
                width: 14,
                height: 14,
                marginTop: 3,
                boxShadow: `0 0 0 4px ${stateColor}10`,
                borderRadius: '50%',
                background: 'transparent',
                border: `2px solid ${stateColor}`,
                borderTopColor: present === 'running' ? 'transparent' : stateColor,
                color: stateColor,
                animation: present === 'running' ? 'task-node-state-spin 1s linear infinite' : undefined,
                transformOrigin: 'center',
                willChange: present === 'running' ? 'transform' : undefined,
                lineHeight: '10px',
                fontSize: 11,
                textAlign: 'center',
                flexShrink: 0,
              }}
            >
              {isDone ? '✓' : ''}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                data-node-title="true"
                title={node.taskSpec?.instruction || node.taskSpec?.target || node.name}
                style={{
                  fontSize: 14,
                  fontWeight: 400,
                  color: isDone ? C.textSecondary : C.textPrimary,
                  textDecoration: isDone ? 'line-through' : 'none',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {node.taskSpec?.instruction || node.taskSpec?.target || node.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    maxWidth: '100%',
                    minWidth: 0,
                    fontSize: 12,
                    color: C.textSecondary,
                    padding: 0,
                    lineHeight: '18px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isGroup ? (
                    <Users size={14} style={{ flexShrink: 0 }} />
                  ) : (
                    <svg
                      width="16"
                      height="16"
                      style={{ flexShrink: 0 }}
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      aria-hidden="true"
                    >
                      <rect x="4" y="7" width="16" height="13" rx="5" />
                      <path d="M12 7V3M9 3h6M1 12v5m22-5v5M8 16h8" />
                      <circle cx="8.5" cy="12" r="1" />
                      <circle cx="15.5" cy="12" r="1" />
                    </svg>
                  )}
                  <span
                    title={executorName}
                    style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  >
                    {executorName}
                  </span>
                </span>
                {/* 返工次数 = attemptCount-1(action_log 的 attempt 从 1 起算,首发不亮徽章) */}
                {(node.attemptCount ?? 0) > 1 && (
                  <span style={badgeStyle(C.warning)}>第 {(node.attemptCount ?? 0) - 1} 次返工</span>
                )}
                {node.needsHitl && present === 'running' && <span style={badgeStyle(C.warning)}>人工处理</span>}
              </div>
            </div>
              <button
                type="button"
                aria-label={`下钻 ${node.name}${canDrill ? '' : `：${drillReason}`}`}
                aria-disabled={!canDrill}
                title={canDrill ? '下钻节点副屏' : drillReason}
                onClick={(e) => {
                  e.stopPropagation();
                  // null 只更新下钻栈；传入会话目标会同时触发宿主导航。
                  if (canDrill) nav.drillInto(node.id, null);
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = C.surfaceAlt)}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 28,
                  height: 28,
                  border: 0,
                  borderRadius: 999,
                  background: 'transparent',
                  color: canDrill ? C.textSecondary : C.textMuted,
                  cursor: canDrill ? 'pointer' : 'not-allowed',
                  flexShrink: 0,
                }}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  aria-hidden="true"
                >
                  <rect x="3" y="2" width="8" height="8" rx="2" />
                  <rect x="13" y="14" width="8" height="8" rx="2" />
                  <path d="M7 10v5a3 3 0 0 0 3 3h3" />
                </svg>
              </button>
            {node.hasSubTask && node.subTaskId && onOpenSubTask && (
              <button
                type="button"
                title="查看子任务"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenSubTask(node.subTaskId!);
                }}
                style={{
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  background: C.surface,
                  color: C.textSecondary,
                  fontSize: 11,
                  padding: '3px 8px',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                子任务
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
};

export default NodeListViewV2;
