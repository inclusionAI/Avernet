// @asset-migrated: teamclaw 自研资产
/**
 * NodeListViewV2 —— L1 节点卡片列表(PRD 方案A):
 * 点根节点 = 选中并恢复宿主会话；点其他节点 = 选中并联动会话；
 * 节点点击仅做会话联动；下钻入口当前先隐藏，不提供节点副屏下钻展示。
 * 卡片信息:present 状态点/标题(完成划线)/执行体 pill(群/单聊 icon)/返工次数/HITL 徽章/子任务入口。
 */
import React from 'react';

import { Info, Users } from '../icons';
import { resolveNodeSessionTarget } from '../sessionResolution';
import { Empty as StateEmpty } from '../theme';
import { C, PRESENT_TONES } from '../tokens';
import type { TaskView } from '../types';
import type { TaskPanelNav } from './taskPanelNav';

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
    <ul style={{ listStyle: 'none', margin: 0, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {task.nodes.map((node) => {
        const present = node.present ?? node.status;
        const tone = PRESENT_TONES[present] ?? { label: present, color: C.textMuted };
        const isDone = present === 'done' || present === 'merged';
        const isGroup = node.runMode === 'coop_group' || node.runMode === 'bbs' || node.drillable === true;
        const executorName =
          node.executorLabel || node.assigneeName || (isGroup ? node.groupName : null) || node.executor || '—';
        const selected = nav.selectedNodeId === node.id;
        const target = resolveNodeSessionTarget(node, task);
        const isRootNode = node.id === rootNodeId;
        const selectForConversation = () => {
          if (isRootNode) nav.selectRootNode(node.id);
          else nav.selectNode(node.id, target);
        };
        return (
          <li
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={`选中节点 ${node.name}`}
            onClick={selectForConversation}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectForConversation();
              }
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '10px 12px',
              borderRadius: 10,
              border: `1px solid ${selected ? C.primary : C.border}`,
              background: selected ? `${C.primary}0F` : C.surface,
              cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
          >
            <span
              role="img"
              aria-label={tone.label}
              style={{
                display: 'inline-block',
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: tone.color,
                border: `2px solid ${tone.color}33`,
                flexShrink: 0,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: isDone ? C.textSecondary : C.textPrimary,
                  textDecoration: isDone ? 'line-through' : 'none',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {node.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    fontSize: 11,
                    color: '#5B3FA8',
                    background: '#F1ECFB',
                    borderRadius: 999,
                    padding: '1px 8px',
                    lineHeight: '18px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {isGroup ? <Users size={11} /> : <Info size={11} />}
                  {executorName}
                </span>
                {/* 返工次数 = attemptCount-1(action_log 的 attempt 从 1 起算,首发不亮徽章) */}
                {(node.attemptCount ?? 0) > 1 && (
                  <span style={badgeStyle(C.warning)}>第 {(node.attemptCount ?? 0) - 1} 次返工</span>
                )}
                {node.needsHitl && present === 'running' && <span style={badgeStyle(C.warning)}>人工处理</span>}
              </div>
            </div>
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
