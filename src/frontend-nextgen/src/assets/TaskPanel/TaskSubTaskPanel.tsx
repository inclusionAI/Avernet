// @asset-migrated: teamclaw 自研资产
/** 子任务下钻面板：保留主任务上下文，在副屏左侧以并列 Tab 展示子任务。 */
import React, { useCallback, useEffect, useState } from 'react';
import { TaskArtifactsTab } from './TaskArtifactsTab';
import { TaskInfoTab } from './TaskInfoTab';
import { TaskPanelFetcher } from './TaskPanelFetcher';
import { TaskPanelHeader } from './TaskPanelHeader';
import { TaskProgressTab } from './TaskProgressTab';
import { Empty } from './theme';
import { C } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskView } from './types';

const SUBTASK_TABS: Array<{ key: SubTaskTab; label: string }> = [
  { key: 'info', label: '任务信息' },
  { key: 'artifacts', label: '产物' },
  { key: 'progress', label: '进度' },
];

const smallTabStyle: React.CSSProperties = {
  padding: '6px 9px',
  border: '1px solid transparent',
  borderRadius: 6,
  background: 'transparent',
  color: C.textSecondary,
  cursor: 'pointer',
  fontSize: 11,
};

const activeSmallTabStyle: React.CSSProperties = {
  borderColor: C.border,
  background: C.surfaceRaised,
  color: C.primary,
  fontWeight: 650,
};

const secondaryButtonStyle: React.CSSProperties = {
  display: 'block',
  margin: '0 auto',
  padding: '7px 14px',
  border: `1px solid ${C.border}`,
  borderRadius: 7,
  background: C.surface,
  color: C.textPrimary,
  cursor: 'pointer',
  fontSize: 12,
};

const TaskLabelEffect: React.FC<{ task: TaskView; onLabel: (taskId: string, label: string) => void }> = ({
  task,
  onLabel,
}) => {
  useEffect(() => {
    onLabel(task.id, task.name);
  }, [onLabel, task.id, task.name]);
  return null;
};

export interface TaskSubTaskPanelProps {
  apiBaseUrl: string;
  // task API 路径前缀（不含 host），透传给内部 TaskPanelFetcher。
  taskApiBase?: string;
  taskIds: string[];
  activeTaskId: string;
  onSelect: (taskId: string) => void;
  onClose: (taskId: string) => void;
}

type SubTaskTab = 'info' | 'artifacts' | 'progress';

export const TaskSubTaskPanel: React.FC<TaskSubTaskPanelProps> = ({
  apiBaseUrl,
  taskApiBase,
  taskIds,
  activeTaskId,
  onSelect,
  onClose,
}) => {
  const [tab, setTab] = useState<SubTaskTab>('progress');
  const [labels, setLabels] = useState<Record<string, string>>({});
  const updateLabel = useCallback((taskId: string, label: string) => {
    setLabels((current) => (current[taskId] === label ? current : { ...current, [taskId]: label }));
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        background: C.surface,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          minHeight: 48,
          padding: '6px 8px',
          borderBottom: `1px solid ${C.border}`,
          background: C.surfaceRaised,
          overflowX: 'auto',
          flexShrink: 0,
        }}
      >
        {taskIds.map((taskId) => {
          const active = taskId === activeTaskId;
          return (
            <div
              key={taskId}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              onClick={() => onSelect(taskId)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(taskId);
                }
              }}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                minWidth: 0,
                maxWidth: 190,
                padding: '7px 8px 7px 10px',
                border: `1px solid ${active ? C.primary : 'transparent'}`,
                borderRadius: 8,
                background: active ? C.primaryBg : 'transparent',
                color: active ? C.primary : C.textSecondary,
                cursor: 'pointer',
                fontSize: 11,
                transition: 'background 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out',
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: active ? C.primary : C.textMuted,
                  flexShrink: 0,
                }}
              />
              <TruncatedText
                value={labels[taskId] ?? `子任务 ${taskId.slice(-6)}`}
                maxLength={20}
                style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              />
              <button
                type="button"
                aria-label={`关闭 ${labels[taskId] ?? taskId}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onClose(taskId);
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 18,
                  height: 18,
                  marginLeft: 2,
                  border: 0,
                  borderRadius: 5,
                  background: 'transparent',
                  color: active ? C.primary : C.textMuted,
                  cursor: 'pointer',
                  fontSize: 14,
                  lineHeight: 1,
                  flexShrink: 0,
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      <TaskPanelFetcher apiBaseUrl={apiBaseUrl} taskApiBase={taskApiBase} taskId={activeTaskId}>
        {({ task, loading, error, retry }) => {
          if (loading && !task) {
            return <Empty description="正在加载子任务…" minHeight={180} />;
          }
          if (error && !task) {
            return (
              <div style={{ padding: 20 }}>
                <Empty description={error} minHeight={120} />
                <button type="button" onClick={retry} style={secondaryButtonStyle}>
                  重试
                </button>
              </div>
            );
          }
          if (!task) return <Empty description="暂无子任务数据" minHeight={180} />;

          return (
            <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%', overflow: 'hidden' }}>
              <TaskLabelEffect task={task} onLabel={updateLabel} />
              <TaskPanelHeader task={task} compact />
              <div
                style={{
                  display: 'flex',
                  gap: 4,
                  padding: '8px 10px',
                  borderBottom: `1px solid ${C.border}`,
                  background: C.surface,
                  flexShrink: 0,
                }}
              >
                {SUBTASK_TABS.map((item) => {
                  const active = tab === item.key;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => setTab(item.key)}
                      style={{ ...smallTabStyle, ...(active ? activeSmallTabStyle : {}) }}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                {tab === 'info' && <TaskInfoTab task={task} />}
                {tab === 'artifacts' && <TaskArtifactsTab task={task} />}
                {tab === 'progress' && <TaskProgressTab task={task} />}
              </div>
            </div>
          );
        }}
      </TaskPanelFetcher>
    </div>
  );
};
