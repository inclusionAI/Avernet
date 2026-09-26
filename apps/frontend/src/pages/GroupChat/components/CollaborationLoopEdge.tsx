import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from '@xyflow/react';
import React from 'react';
import type { CollaborationGraphLayoutEdge } from '../utils/collaborationGraphLayout';

type LoopEdge = Edge<NonNullable<CollaborationGraphLayoutEdge['data']>>;

export default function CollaborationLoopEdge(props: EdgeProps<LoopEdge>) {
  const { sourceX, sourceY, targetX, targetY, data, label, id, markerEnd } = props;
  const [curve, centerX, centerY] = getBezierPath(props);
  const bypass = !data?.logicalView && targetY - sourceY > 120;
  const sideX = Math.max(sourceX, targetX) + 150;
  const path = data?.returnEdge && props.source === props.target
    ? `M ${sourceX} ${sourceY} C ${data.returnX! + 20} ${sourceY + 36}, ${data.returnX! + 20} ${targetY - 36}, ${targetX} ${targetY}`
    : data?.returnEdge
    ? `M ${sourceX} ${sourceY} L ${data.returnX! - 16} ${sourceY} Q ${data.returnX} ${sourceY}, ${data.returnX} ${sourceY - 16} L ${data.returnX} ${targetY + 16} Q ${data.returnX} ${targetY}, ${data.returnX! - 16} ${targetY} L ${targetX} ${targetY}`
    : bypass
    ? `M ${sourceX} ${sourceY} C ${sourceX} ${sourceY + 20}, ${sideX} ${sourceY + 20}, ${sideX} ${sourceY + 38} L ${sideX} ${targetY - 28} Q ${sideX} ${targetY - 12}, ${targetX} ${targetY}`
    : curve;
  const secondary = data?.returnEdge || data?.loopRoute.kind === 'exhausted';
  const color = secondary ? '#94a3b8' : { continue: '#6d28d9', break: '#047857', exhausted: '#94a3b8' }[data!.loopRoute.kind];
  const returnLabelX = data?.returnEdge ? data.returnX! - 32 : 0;
  return <g data-loop-route={data!.loopRoute.kind} data-actual-outcome={data!.outcome}>
    <title>{`${label}；实际 outcome：${data!.outcome}`}</title>
    <BaseEdge id={id} path={path} markerEnd={markerEnd}
      label={data?.returnEdge ? undefined : label} labelX={bypass ? sideX : centerX}
      labelY={(data?.returnEdge ? (sourceY + targetY) / 2 : bypass ? sourceY + 16 : centerY) + (data!.labelLane || 0) * 22}
      style={{ stroke: color, strokeWidth: secondary ? 1.25 : 2, strokeDasharray: secondary ? '5 4' : undefined }}
      labelStyle={{ fill: color, fontSize: secondary ? 10 : 11, fontWeight: secondary ? 400 : 700 }}
      labelBgStyle={{ fill: '#fff', fillOpacity: 0.95 }} labelBgPadding={[5, 3]} labelBgBorderRadius={5} />
    {data?.returnEdge && <text x={returnLabelX} y={(sourceY + targetY) / 2 - 5}
      textAnchor="middle" fill={color} fontSize={10} fontWeight={500}>
      {label}
    </text>}
  </g>;
}
