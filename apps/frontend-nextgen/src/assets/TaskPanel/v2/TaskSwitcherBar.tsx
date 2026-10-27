// @asset-migrated: teamclaw 自研资产
/**
 * TaskSwitcherBar —— 副屏顶部任务切换横条(PRD #6,数据源 listTasks,宿主注入)。
 * 横条:任务小标 + 当前任务名 + 状态 tag + 任务总数徽章;点击展开下拉切换。
 * demo 的全屏拖拽能力不产品化，副屏内入口由 TaskSwitcherControl 承载。
 */
import React, { useState } from 'react';

import { ChevronRight } from '../icons';
import { C } from '../tokens';

export interface TaskSwitcherItem {
  taskId: string;
  title: string;
  status: 'running' | 'pending' | 'done';
  statusLabel: string;
}

export interface TaskSwitcherProps {
  items: TaskSwitcherItem[];
  activeTaskId: string;
  onSelectTask: (taskId: string) => void;
  /** 仅影响首帧；后续仍由用户点击横条控制。 */
  defaultOpen?: boolean;
}

const STATUS_TONE: Record<TaskSwitcherItem['status'], { color: string; bg: string }> = {
  running: { color: C.primary, bg: `${C.primary}14` },
  pending: { color: C.textSecondary, bg: C.surfaceAlt },
  done: { color: C.success, bg: '#E8FFEA' },
};

const Tag: React.FC<{ item: TaskSwitcherItem }> = ({ item }) => {
  const tone = STATUS_TONE[item.status];
  return (
    <span
      style={{
        fontSize: 10,
        color: tone.color,
        background: tone.bg,
        borderRadius: 999,
        padding: '1px 6px',
        lineHeight: '16px',
        whiteSpace: 'nowrap',
      }}
    >
      {item.statusLabel}
    </span>
  );
};

export const TaskSwitcherBar: React.FC<TaskSwitcherProps> = ({ items, activeTaskId, onSelectTask, defaultOpen }) => {
  const [open, setOpen] = useState(defaultOpen ?? false);
  if (items.length === 0) return null;

  const active = items.find((i) => i.taskId === activeTaskId) ?? items[0];

  return (
    <div style={{ position: 'relative', borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
      <button
        type="button"
        title="展开任务切换"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '8px 12px',
          border: 'none',
          background: open ? C.surfaceAlt : 'transparent',
          cursor: 'pointer',
          fontSize: 13,
          color: C.textPrimary,
          textAlign: 'left',
        }}
      >
        <span style={{ fontSize: 11, color: C.textSecondary, flexShrink: 0 }}>任务</span>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 600,
          }}
        >
          {active.title}
        </span>
        <Tag item={active} />
        <span
          title="任务总数"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: 18,
            height: 18,
            borderRadius: 999,
            background: C.surfaceAlt,
            border: `1px solid ${C.border}`,
            color: C.textSecondary,
            fontSize: 10,
            flexShrink: 0,
          }}
        >
          {items.length}
        </span>
        <ChevronRight
          style={{
            transform: open ? 'rotate(90deg)' : 'rotate(-90deg)',
            color: C.textSecondary,
            flexShrink: 0,
          }}
        />
      </button>

      {open && (
        <ul
          data-testid="task-switcher-dropdown"
          style={{
            position: 'absolute',
            top: '100%',
            left: 8,
            right: 8,
            zIndex: 30,
            listStyle: 'none',
            margin: 0,
            padding: 4,
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
            maxHeight: 320,
            overflow: 'auto',
          }}
        >
          {items.map((i) => {
            const current = i.taskId === active.taskId;
            return (
              <li key={i.taskId}>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    if (!current) onSelectTask(i.taskId);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    width: '100%',
                    padding: '6px 8px',
                    border: 'none',
                    borderRadius: 6,
                    background: current ? C.surfaceAlt : 'transparent',
                    color: C.textPrimary,
                    fontSize: 12,
                    cursor: current ? 'default' : 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <span
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {i.title}
                  </span>
                  <div style={{ flex: 1 }} />
                  <Tag item={i} />
                  {current && <span style={{ fontSize: 10, color: C.primary }}>当前</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default TaskSwitcherBar;
