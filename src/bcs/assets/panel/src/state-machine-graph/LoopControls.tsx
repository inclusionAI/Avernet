import React from 'react';
import styled from 'styled-components';
import type { StateMachineRunGraph } from '../StateMachineRunView';
import type { LoopGroupLayout } from './layout';
import { loopProgress } from './loops';

const Bar = styled.div`
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-size: 12px;
  background: white; color: #64748b;
  strong { font-weight: 500; color: #475569; }
  select, button { font: inherit; padding: 5px 8px; border: 1px solid #e2e8f0; border-radius: 6px; background: white; color: #475569; }
  select { max-width: 100%; }
  button { cursor: pointer; }
`;

export function LoopControls({ graph, choices, onChoose }: {
  graph: StateMachineRunGraph; choices: Record<string, number>;
  onChoose: (loopId: string, iteration?: number) => void;
}) {
  return <>{Object.entries(graph.loops || {}).map(([id, loop]) => {
    const progress = loopProgress(graph, id);
    return <Bar key={id} data-loop-controls={id}>
      <strong title={`max_iterations: ${loop.max_iterations}`}>{loop.display_name || id}</strong><span>{progress.status}</span>
      {progress.rounds.length > 0 && <label>查看执行 <select aria-label={`${loop.display_name || id} 查看执行`}
        value={progress.rounds.some((round) => round.iteration === choices[id]) ? choices[id] : progress.iteration} onChange={(event) => onChoose(id, Number(event.target.value))}>
        {progress.rounds.map((round) => <option key={round.iteration} value={round.iteration}>
          #{round.iteration} · {round.label}
        </option>)}
      </select></label>}
      {choices[id] !== undefined && <button type="button" onClick={() => onChoose(id)}>回到当前执行</button>}
    </Bar>;
  })}</>;
}

export function LoopContainers({ groups }: { groups: LoopGroupLayout[] }) {
  return <>{groups.map((group) => <g key={`${group.loopId}:${group.iteration}`} data-loop-container={group.loopId}>
    <title>{group.title} · Loop #{group.iteration} · max_iterations: {group.maxIterations}</title>
    <rect x={group.x} y={group.y} width={group.width} height={group.height} rx={12} fill="#fff" fillOpacity={0.4} stroke="#e2e8f0" strokeWidth={1} />
    <text x={group.x + 12} y={group.y + 23} fill="#64748b" fontSize={11} fontWeight={500}>
      {group.title.length > 16 ? `${group.title.slice(0, 15)}…` : group.title}
    </text>
    <text x={group.x + group.width - 12} y={group.y + 23} fill="#64748b" fontSize={10} textAnchor="end">
      Loop #{group.iteration}
    </text>
  </g>)}</>;
}
