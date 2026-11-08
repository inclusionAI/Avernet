import React, { useEffect, useId, useState } from 'react';
import styled from 'styled-components';
import type { RoomComposition } from './sceneLayout';
import type { WorkflowBoardView, WorkflowNodeView, WorkflowStatusTone } from './workflowGraph';

const TONE_COLORS: Record<WorkflowStatusTone, string> = {
  pending: '#6e7686',
  ready: '#8f9aab',
  running: '#e8b45e',
  completed: '#5fb7a6',
  failed: '#c0504a',
  aborted: '#b98b5e',
  retry: '#d9a066',
  skipped: '#646894',
  unknown: '#9b555b',
};

const COL_W = 214;
const ROW_H = 82;
const NODE_W = 164;
const NODE_H = 62;
const PAD = 10;

const Board = styled.section<{ $collapsed: boolean }>`margin: 0 0 ${p => (p.$collapsed ? 6 : 10)}px;padding: ${p => (p.$collapsed ? '7px 10px' : '10px 12px 12px')};border: 1px solid #8e6531;border-radius: 8px;background: linear-gradient(180deg, #121f2f 0%, #101b2a 100%);box-shadow: inset 0 0 0 2px #080a10;`;
const BoardHead = styled.div`display: flex;flex-wrap: wrap;align-items: center;justify-content: space-between;gap: 6px 10px;margin-bottom: 4px;`;
const BoardTitle = styled.h3`margin: 0;color: #f2e7ce;font-size: 12px;font-weight: 700;letter-spacing: 0.06em;`;
const BoardMeta = styled.div`display: flex;flex-wrap: wrap;align-items: center;gap: 6px;color: #8f9aab;font-size: 10px;`;
const RunChip = styled.span<{ $tone: WorkflowStatusTone }>`padding: 1px 7px;border: 1px solid ${p => TONE_COLORS[p.$tone]};border-radius: 4px;color: ${p => TONE_COLORS[p.$tone]};background: #0b0e16;font-size: 10px;letter-spacing: 0.04em;white-space: nowrap;`;
const Toggle = styled.button`flex-shrink: 0;cursor: pointer;border: 1px solid #e8b45e;border-radius: 4px;min-height: 26px;padding: 3px 9px;color: #ffe7b0;background: #0b0e16;font: inherit;font-size: 10px;letter-spacing: 0.04em;white-space: nowrap;&:hover{background: #16233a;}&:focus-visible{outline: 2px solid #ffe7b0;outline-offset: 2px;}`;
const Notice = styled.p`margin: 4px 0 0;color: #c9b48c;font-size: 11px;line-height: 1.6;overflow-wrap: anywhere;`;
const Empty = styled.p`margin: 4px 0 0;color: #8f9aab;font-size: 11px;line-height: 1.6;`;
const Scroller = styled.div<{ $narrow: boolean }>`margin-top: 6px;overflow: auto;overscroll-behavior: contain;padding-bottom: 2px;max-width: 100%;max-height: min(240px, 30vh);${p => (p.$narrow ? 'scrollbar-width: thin;' : '')}`;
const Canvas = styled.div`position: relative;`;
const EdgeLayer = styled.svg`position: absolute;left: 0;top: 0;pointer-events: none;overflow: visible;`;
const NodeCard = styled.div<{ $tone: WorkflowStatusTone; $mapped: boolean; $final: boolean }>`position: absolute;z-index: 2;width: ${NODE_W}px;min-height: ${NODE_H}px;box-sizing: border-box;padding: 5px 7px 5px 9px;border: 1px solid ${p => (p.$mapped ? '#8e6531' : '#6e7686')};border-left: 3px solid ${p => TONE_COLORS[p.$tone]};border-radius: 4px;background: ${p => (p.$mapped ? '#16233a' : '#12151d')};color: #f2e7ce;box-shadow: 0 2px 0 #080a10;opacity: ${p => (p.$mapped ? 1 : 0.82)};`;
const NodeTop = styled.div`display: flex;align-items: center;justify-content: space-between;gap: 6px;color: #8f9aab;font-size: 9px;letter-spacing: 0.06em;`;
const NodeKind = styled.span`color: #c9b48c;font-weight: 700;`;
const NodeSeat = styled.span`color: #e8b45e;white-space: nowrap;`;
const NodeName = styled.div`margin-top: 1px;overflow: hidden;text-overflow: ellipsis;white-space: nowrap;font-size: 12px;font-weight: 700;color: #f2e7ce;`;
const NodeId = styled.span`margin-left: 2px;color: #8f9aab;font-size: 9px;font-weight: 500;`;
const NodeStatus = styled.div<{ $tone: WorkflowStatusTone }>`display: flex;align-items: center;gap: 5px;margin-top: 2px;color: ${p => TONE_COLORS[p.$tone]};font-size: 10px;line-height: 1.3;&::before{content: '';width: 7px;height: 7px;border-radius: 50%;background: currentColor;}`;
const Attempt = styled.span`margin-left: auto;color: #8f9aab;font-size: 9px;`;
const FinalTag = styled.span`color: #8f9aab;font-size: 9px;letter-spacing: 0.04em;`;
const SrOnly = styled.span`position: absolute;width: 1px;height: 1px;overflow: hidden;clip-path: inset(50%);white-space: nowrap;`;

function position(node: WorkflowNodeView): { x: number; y: number } {
  return { x: PAD + node.rank * COL_W, y: PAD + node.row * ROW_H };
}

function NodeLabel({ node, final }: { node: WorkflowNodeView; final: boolean }) {
  const seat = node.isHost ? '主持' : node.kind === 'vote-start' ? '预备' : typeof node.seatNumber === 'number' ? `${node.seatNumber}号` : '未映射';
  const label = `${node.kindLabel} ${seat} ${node.displayName}，${node.statusLabel}${node.attemptLabel ? `，${node.attemptLabel}` : ''}${final ? '，末节点' : ''}`;
  return <NodeCard
    data-workflow-node="true"
    data-node-id={node.nodeId}
    data-node-kind={node.kind}
    data-node-status={(node.status ?? 'unknown').toLowerCase()}
    data-node-mapped={node.mapped ? 'true' : 'false'}
    data-node-attempt={node.attempt}
    data-node-rank={node.rank}
    data-final-node={final ? 'true' : undefined}
    role="listitem"
    aria-label={label}
    title={node.nodeId}
    $tone={node.tone}
    $mapped={node.mapped}
    $final={final}
    style={{ left: position(node).x, top: position(node).y }}
  >
    <NodeTop><NodeKind>{node.kindLabel}</NodeKind><NodeSeat>{seat}</NodeSeat></NodeTop>
    <NodeName>{node.displayName}{!node.mapped && <NodeId> · {node.nodeId}</NodeId>}</NodeName>
    <NodeStatus $tone={node.tone}>{node.statusLabel}{node.attemptLabel && <Attempt>{node.attemptLabel}</Attempt>}{final && <FinalTag>末节点</FinalTag>}</NodeStatus>
  </NodeCard>;
}

export interface WorkflowBoardProps {
  board: WorkflowBoardView;
  composition: RoomComposition;
  collapsed?: boolean;
  runKey?: string;
}

/**
 * Night-banquet presentation of the real run graph. Every node and edge below
 * is drawn from the current run's graph; the board never derives progress from
 * phase, never counts nodes on its own and never reads artifacts or errors.
 */
export function WorkflowBoard({ board, composition, collapsed = false, runKey }: WorkflowBoardProps) {
  const markerId = `wf-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const contentId = `wf-content-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [expanded, setExpanded] = useState(!collapsed);
  useEffect(() => { setExpanded(!collapsed); }, [runKey, collapsed]);
  const showContent = !collapsed || expanded;
  const maxRows = board.lanes.reduce((max, lane) => Math.max(max, lane.length), 0);
  const width = board.lanes.length ? PAD * 2 + (board.lanes.length - 1) * COL_W + NODE_W : 0;
  const height = maxRows ? PAD * 2 + (maxRows - 1) * ROW_H + NODE_H : 0;
  const byId = new Map(board.nodes.map((node) => [node.nodeId, node]));
  return <Board $collapsed={!showContent} data-region="workflow-board" data-workflow-collapsed={!showContent ? 'true' : 'false'} data-workflow-expanded={expanded ? 'true' : 'false'} data-run-id={board.runId ?? ''} data-run-status={(board.runStatus ?? 'unknown').toLowerCase()} data-workflow-degrade={board.degradeReasons.join(' ') || undefined} aria-label="当前运行流程">
    <BoardHead>
      <BoardTitle>本阶段真实流程</BoardTitle>
      <BoardMeta>
        <RunChip $tone={board.runStatusTone}>{board.runStatusLabel}</RunChip>
        {board.available && <span>{board.nodes.length} 个节点 · {board.edges.length} 条连线</span>}
        {!showContent ? <span>已收起</span> : (board.lanes.length > 3 || maxRows > 3) && <span>滚动查看全部</span>}
        {collapsed && <Toggle type="button" data-region="workflow-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded((value) => !value)}>{expanded ? '收起流程' : '查看流程'}</Toggle>}
      </BoardMeta>
    </BoardHead>
    {board.notice && <Notice role="status" data-workflow-notice="true">{board.notice}</Notice>}
    <div id={contentId}>
    {showContent && (!board.available || !board.nodes.length
      ? <Empty>暂无可展示的运行节点。</Empty>
      : <>
        <Scroller $narrow={composition === 'narrow'}>
          <Canvas style={{ width, height }}>
            <EdgeLayer width={width} height={height} aria-hidden="true">
              <defs>
                <marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0 0L8 4L0 8z" fill="#8e6531" />
                </marker>
              </defs>
              {board.edges.map((edge) => {
                const source = byId.get(edge.source);
                const target = byId.get(edge.target);
                if (!source || !target) return null;
                const from = position(source);
                const to = position(target);
                const x1 = from.x + NODE_W;
                const y1 = from.y + NODE_H / 2;
                const x2 = to.x;
                const y2 = to.y + NODE_H / 2;
                const mid = Math.max(18, Math.abs(x2 - x1) / 2);
                return <path
                  key={`${edge.source}->${edge.target}`}
                  data-workflow-edge="true"
                  data-edge-source={edge.source}
                  data-edge-target={edge.target}
                  d={`M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="#8e6531"
                  strokeWidth="1.5"
                  markerEnd={`url(#${markerId})`}
                />;
              })}
            </EdgeLayer>
            <div role="list">
              {board.nodes.map((node) => <NodeLabel key={node.nodeId} node={node} final={board.edges.length > 0 && board.finalNodeIds.includes(node.nodeId)} />)}
            </div>
            <SrOnly aria-hidden="true">{board.edges.map((edge) => <span key={`${edge.source}->${edge.target}`} data-workflow-edge-summary="true">{`${edge.source} → ${edge.target}`}</span>)}</SrOnly>
          </Canvas>
        </Scroller>
      </>)}
    </div>
  </Board>;
}
