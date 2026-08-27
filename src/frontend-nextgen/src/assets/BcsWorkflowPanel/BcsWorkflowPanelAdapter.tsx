// @asset-migrated: teamclaw 自研适配层（方案B本地注册的集成胶水，非 Avernet 源码）
/**
 * BcsWorkflowPanelAdapter —— 把引擎 PanelContentProps 契约适配为
 * StateMachineRunView 的业务 props（StateMachineRunViewProps）。
 *
 * 背景：StateMachineRunView 是有自身 props 契约（data/runId/baseUrl/onInteraction）的
 * 副屏组件，与引擎 PanelContentProps（params/onAction/onInteraction/tab/eventEmitter）签名不同。
 * 原 Avernet 用 UmdPanel 包装注入；方案 B 本地注册需要一个轻量适配层把两者接起来。
 *
 * 数据流向：
 * - 入：PanelContentProps.params（openPanelTab 时透传的 {runId, baseUrl, data...}）
 *      → 展平为 StateMachineRunViewProps 顶层字段 + data
 * - 出：StateMachineRunView 业务 onInteraction（节点点击等业务事件）
 *      → 映射为 PanelContentProps.onInteraction（InteractionRecord，供 Agent 上下文）
 *
 * 本文件属 src/assets 资产目录，遵守该目录守卫：不反向 import teamclaw 业务层。
 */
import type { CSSProperties } from 'react';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import StateMachineRunView, { type StateMachineRunViewData, type StateMachineNode, type StateMachineRun } from './StateMachineRunView';

type BcsWorkflowParams = Partial<StateMachineRunViewData>;

export function BcsWorkflowPanelAdapter({
  params,
  onInteraction,
  className,
  style,
}: PanelContentProps & { className?: string; style?: CSSProperties }) {
  const p = (params ?? {}) as BcsWorkflowParams;
  const data: StateMachineRunViewData | undefined =
    (params as { data?: StateMachineRunViewData })?.data ?? (p.runId || p.apiBaseUrl ? (p as StateMachineRunViewData) : undefined);

  return (
    <StateMachineRunView
      runId={p.runId}
      stateMachineRunId={p.stateMachineRunId}
      smRunId={p.smRunId}
      apiBaseUrl={p.apiBaseUrl}
      baseUrl={p.baseUrl}
      data={data}
      className={className}
      style={style}
      onInteraction={(payload: { type: string; node?: StateMachineNode; run?: StateMachineRun }) => {
        const node = payload.node;
        onInteraction({
          source: { type: 'panel', target: node ? `node:${node.node_id}` : 'workflow' },
          description: node
            ? `用户在 BCS workflow 副屏交互：${payload.type}（节点：${node.display_name || node.node_id}）`
            : `用户在 BCS workflow 副屏交互：${payload.type}`,
          action: { verb: payload.type, subject: node ? 'node' : 'workflow', params: { nodeId: node?.node_id, runId: payload.run?.run_id } },
          snapshot: { selectedNodeId: node?.node_id },
        });
      }}
    />
  );
}

export default BcsWorkflowPanelAdapter;
