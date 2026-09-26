import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from '@xyflow/react';
import React from 'react';

export default function CollaborationTransitionEdge(props: EdgeProps<Edge<{ outcome: string }>>) {
  const [path, labelX, labelY] = getBezierPath(props);
  return <g>
    <title>{`${props.label ?? props.data?.outcome}；实际 outcome：${props.data?.outcome}`}</title>
    <BaseEdge id={props.id} path={path} labelX={labelX} labelY={labelY}
      markerEnd={props.markerEnd} style={props.style} label={props.label}
      labelStyle={props.labelStyle} labelBgStyle={props.labelBgStyle}
      labelBgPadding={props.labelBgPadding} labelBgBorderRadius={props.labelBgBorderRadius} />
  </g>;
}
