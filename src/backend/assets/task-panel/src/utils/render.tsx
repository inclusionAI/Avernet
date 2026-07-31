/**
 * Task workflow panel — SVG render primitives.
 * Ported from bcsPanel.StateMachineRunView (renderNodeStatusMarker:1832,
 * NodeGroup:546, StatusPill:455, edge paths). Pure presentational SVG + a
 * StatusPill for the HTML header.
 */
import React from 'react';

import type { EdgeState, StatusTone } from './statusTone';
import { getEdgeStroke } from './statusTone';
import type { LayoutEdge, LayoutNode } from './layout';

// --- HTML status pill (header / modal) ------------------------------------

export function StatusPill({
  tone,
  label,
}: {
  tone: StatusTone;
  label: string;
}): React.ReactElement {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        color: tone.text,
        lineHeight: '20px',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: tone.stroke,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
}

// --- node status marker (SVG, ported from :1832) --------------------------

export function renderNodeStatusMarker(
  status: string | undefined,
): React.ReactElement {
  const s = (status || '').trim().toLowerCase();
  if (s === 'done') {
    return <path d="M -3.2 0 L -0.8 2.5 L 4 -3.2" fill="none" />;
  }
  if (s === 'failed') {
    return (
      <>
        <path d="M -3.2 -3.2 L 3.2 3.2" fill="none" />
        <path d="M 3.2 -3.2 L -3.2 3.2" fill="none" />
      </>
    );
  }
  if (s === 'running') {
    return <path d="M -1.8 -3.2 L 4 0 L -1.8 3.2 Z" fill="#ffffff" />;
  }
  if (s === 'skipped') {
    return <path d="M -3.6 0 L 3.6 0" fill="none" />;
  }
  if (s === 'human_required') {
    return (
      <>
        <path d="M -3.2 -1.6 H 3.2" fill="none" />
        <path d="M -3.2 1.6 H 3.2" fill="none" />
      </>
    );
  }
  return <circle cx="0" cy="0" fill="#ffffff" r="2" />;
}

// --- node group (SVG <g>) -------------------------------------------------

export function NodeGroup({
  layoutNode,
  tone,
  label,
  onClick,
  isSelected,
}: {
  layoutNode: LayoutNode;
  tone: StatusTone;
  label: string;
  onClick?: (nodeId: string) => void;
  isSelected?: boolean;
}): React.ReactElement {
  const { node, x, y, width, height } = layoutNode;
  const displayName = node.display_name || node.node_id;
  const assignee = node.assignee || '';
  const cx = x + width - 12;
  const cy = y + 12;

  return (
    <g
      style={{ cursor: onClick ? 'pointer' : 'default' }}
      onClick={onClick ? () => onClick(node.node_id) : undefined}
    >
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={8}
        ry={8}
        fill={tone.fill}
        stroke={isSelected ? tone.stroke : tone.border}
        strokeWidth={isSelected ? 2 : 1}
      />
      <circle
        cx={cx}
        cy={cy}
        r={7}
        fill={tone.stroke}
        stroke="#ffffff"
        strokeWidth={1.5}
      />
      <g
        transform={`translate(${cx}, ${cy})`}
        stroke={tone.stroke}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {renderNodeStatusMarker(node.status)}
      </g>
      <text
        x={x + 12}
        y={y + 24}
        fontSize={13}
        fontWeight={600}
        fill="#1e293b"
        style={{ userSelect: 'none' }}
      >
        {displayName.length > 16 ? `${displayName.slice(0, 15)}…` : displayName}
      </text>
      {assignee ? (
        <text x={x + 12} y={y + 42} fontSize={11} fill={tone.text} style={{ userSelect: 'none' }}>
          {assignee}
        </text>
      ) : null}
      {node.sub_dag_ref ? (
        <text
          x={x + width - 12}
          y={y + height - 8}
          fontSize={10}
          fill={tone.text}
          textAnchor="end"
          style={{ userSelect: 'none' }}
        >
          协作子图 ›
        </text>
      ) : null}
      <title>{`${label} · ${displayName}${assignee ? ` · ${assignee}` : ''}`}</title>
    </g>
  );
}

// --- edge path (SVG) ------------------------------------------------------

function edgePathD(source: LayoutNode, target: LayoutNode): string {
  const sx = source.x + source.width;
  const sy = source.y + source.height / 2;
  const tx = target.x;
  const ty = target.y + target.height / 2;
  const mx = (sx + tx) / 2;
  return `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}`;
}

export function EdgePath({
  layoutEdge,
  state,
}: {
  layoutEdge: LayoutEdge;
  state: EdgeState;
}): React.ReactElement {
  const stroke = getEdgeStroke(state, layoutEdge.edge.kind);
  const d = edgePathD(layoutEdge.source, layoutEdge.target);
  const tx = layoutEdge.target.x;
  const ty = layoutEdge.target.y + layoutEdge.target.height / 2;
  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={stroke.color}
        strokeWidth={stroke.width}
        strokeDasharray={stroke.dasharray || undefined}
      />
      {state !== 'skipped' ? (
        <polygon
          points={`${tx - 6},${ty - 4} ${tx},${ty} ${tx - 6},${ty + 4}`}
          fill={stroke.color}
        />
      ) : null}
      {layoutEdge.edge.outcome ? (
        <text
          x={(layoutEdge.source.x + layoutEdge.source.width + layoutEdge.target.x) / 2}
          y={ty - 6}
          fontSize={10}
          fill="#64748b"
          textAnchor="middle"
          style={{ userSelect: 'none' }}
        >
          {layoutEdge.edge.outcome}
        </text>
      ) : null}
    </g>
  );
}
