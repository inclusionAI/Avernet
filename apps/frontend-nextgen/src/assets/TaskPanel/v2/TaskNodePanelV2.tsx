// @asset-migrated: teamclaw 自研资产
/**
 * TaskNodePanelV2 —— Level2 节点副屏(PRD #5)三区 + 群下钻吸收:
 * ① 节点信息(名称/执行体/present 标签/返工/HITL/goal/起止时间/步骤追踪)
 * ② 执行流程(node.execDag,action_log 派生,DagViewV2 只读)
 * ③ 节点产物(nodes[].artifacts,Descriptor 优先口径)
 * ④ 群节点追加:bcsBaseUrl 注入时内嵌 GroupSessionView(存量群成员/群消息明细能力吸收)
 * ⑤ 子任务链路保持 hasSubTask → 宿主 onOpenSubTask(不回退,spec P3)
 * drillStack 顶节点消失(图变更)→ 空态 + 返回,不崩溃不卡栈。
 */
import React from 'react';

import { GroupSessionView } from '../GroupDrillDown';
import { Empty as StateEmpty } from '../theme';
import { C, PRESENT_TONES } from '../tokens';
import type { TaskNodeView, TaskView } from '../types';
import { ArtifactsSection } from './ArtifactsSection';
import { DagViewV2 } from './DagViewV2';
import type { TaskPanelNav } from './taskPanelNav';

export interface TaskNodePanelV2Props {
  task: TaskView;
  /** null = drillStack 顶节点已不存在(数据变更),渲染空态并允许返回 */
  node: TaskNodeView | null;
  nav: TaskPanelNav;
  apiBaseUrl: string;
  bcsBaseUrl?: string;
  userId?: string;
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

const SectionTitle: React.FC<{ title: string; sub?: string }> = ({ title, sub }) => (
  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
    <span style={{ fontSize: 13, fontWeight: 600, color: C.textPrimary }}>{title}</span>
    {sub && <span style={{ fontSize: 11, color: C.textSecondary }}>{sub}</span>}
  </div>
);

export const TaskNodePanelV2: React.FC<TaskNodePanelV2Props> = ({
  task,
  node,
  nav,
  apiBaseUrl,
  bcsBaseUrl,
  userId,
  onOpenSubTask,
}) => {
  if (!node) {
    return (
      <div style={{ padding: 24 }}>
        <StateEmpty description="节点数据缺失(任务图已变更)" />
        <button
          type="button"
          onClick={nav.backToOverview}
          style={{
            display: 'block',
            margin: '12px auto 0',
            padding: '6px 16px',
            border: `1px solid ${C.primary}`,
            borderRadius: 6,
            background: 'transparent',
            color: C.primary,
            cursor: 'pointer',
          }}
        >
          返回任务总览
        </button>
      </div>
    );
  }

  // 节点存在但 L2 内再查不到(防御:node 是 task.nodes 中的实例)
  void task;

  const present = node.present ?? node.status;
  const presentTone = PRESENT_TONES[present] ?? { label: present, color: C.textMuted };
  const groupEmbedEnabled = node.drillable === true && !!bcsBaseUrl;
  const goal = node.taskSpec?.instruction || node.taskSpec?.target || '';

  return (
    <div data-testid="task-node-panel-v2">
      {/* ① 节点信息 */}
      <section style={{ padding: '14px 16px', borderBottom: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: C.textPrimary, marginBottom: 8 }}>{node.name}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
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
            }}
          >
            {node.executorLabel || node.assigneeName || node.groupName || node.executor || '—'}
          </span>
          <span style={{ ...badgeStyle(presentTone.color), display: 'inline-flex' }}>{presentTone.label}</span>
          {/* 返工次数 = attemptCount-1(attempt 从 1 起算,首发不亮徽章) */}
          {(node.attemptCount ?? 0) > 1 && (
            <span style={badgeStyle(C.warning)}>第 {(node.attemptCount ?? 0) - 1} 次返工</span>
          )}
          {node.needsHitl && <span style={badgeStyle(C.warning)}>人工处理</span>}
          {node.hasSubTask && node.subTaskId && onOpenSubTask && (
            <button
              type="button"
              title="查看子任务"
              onClick={() => onOpenSubTask(node.subTaskId!)}
              style={{
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                background: C.surface,
                color: C.textSecondary,
                fontSize: 11,
                padding: '2px 8px',
                cursor: 'pointer',
              }}
            >
              子任务
            </button>
          )}
        </div>
        {goal && <div style={{ fontSize: 12, color: C.textSecondary, lineHeight: 1.6, marginBottom: 6 }}>{goal}</div>}
        <div style={{ fontSize: 11, color: C.textSecondary }}>
          {node.startedAt ? `开始 ${node.startedAt}` : '未开始'} · {node.endAt ? `完成 ${node.endAt}` : '进行中'}
        </div>
        {/* 步骤追踪(存量信息能力保留;PRD #8 生命周期事件的落点,Ruling 3) */}
        {node.stepTraces.length > 0 && (
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: C.textPrimary }}>步骤追踪</summary>
            <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
              {node.stepTraces.map((s) => (
                <li key={s.id} style={{ fontSize: 11, color: C.textSecondary, marginBottom: 4 }}>
                  {s.timestamp} {s.title}:{s.content}
                </li>
              ))}
            </ol>
          </details>
        )}
      </section>

      {/* ② 执行流程(node.execDag 只读) */}
      <section style={{ padding: '14px 16px', borderBottom: `1px solid ${C.border}` }}>
        <SectionTitle title="执行流程" sub="节点内部 workflow(只读)" />
        {node.execDag && node.execDag.nodes.length > 0 ? (
          <div style={{ height: 240, border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
            <DagViewV2 view="readonly-exec" steps={node.execDag.nodes} stepEdges={node.execDag.edges} />
          </div>
        ) : (
          <StateEmpty description="该节点暂无内部执行流程" minHeight={80} />
        )}
      </section>

      {/* ③ 节点产物(nodes[].artifacts 已按节点圈定,无需过滤) */}
      <ArtifactsSection
        artifacts={node.artifacts}
        apiBaseUrl={apiBaseUrl}
        context={{ taskId: task.id, ownerBotId: task.ownerBotId, ownerUserId: task.ownerUserId }}
      />

      {/* ④ 群节点追加:存量的群成员 + 群消息明细(吸收,不再走左侧 rail) */}
      {groupEmbedEnabled && (
        <div style={{ borderTop: `1px solid ${C.border}` }}>
          <GroupSessionView
            node={node}
            bcsBaseUrl={bcsBaseUrl!}
            apiBaseUrl={apiBaseUrl}
            userId={userId}
            onBack={nav.backToOverview}
          />
        </div>
      )}
    </div>
  );
};

export default TaskNodePanelV2;