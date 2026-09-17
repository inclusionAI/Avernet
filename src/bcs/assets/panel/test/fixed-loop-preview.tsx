import React from 'react';
import { createRoot } from 'react-dom/client';
import StateMachineRunView from '../src/StateMachineRunView';
import fixture from '../../../tests/fixtures/fixed_loop_api.json';
import writing from '../../../tests/fixtures/fixed_loop_logical_view.json';

const scenario = new URLSearchParams(location.search).get('scenario') || 'writing';
const graph = structuredClone(scenario === 'writing' ? writing : fixture.graph);
const human = scenario === 'first' || scenario === 'later';
const index = scenario === 'first' || scenario === 'break' ? 0 : 2;
if (scenario !== 'writing') graph.nodes.forEach((node, i) => {
  node.status = i < index ? 'completed' : i === index ? (human ? 'running' : 'completed') : scenario === 'break' && i < 3 ? 'skipped' : 'pending';
});
if (human) graph.nodes[index].kind = 'human_input';
else if (scenario !== 'writing') {
  graph.nodes[index].outcome = scenario === 'break' ? 'done' : 'again';
  graph.nodes[3].status = 'running';
}
window.fetch = async (url, options) => {
  const path = String(url);
  const pending = human ? (scenario === 'first' ? fixture.pending_first : fixture.pending_later) : [];
  const selected = graph.nodes.find((node) => path.endsWith(`/nodes/${encodeURIComponent(node.node_id)}`)) || graph.nodes[index];
  const node = { ...fixture.run.nodes[index], ...selected, attempt: 'attempt' in selected ? selected.attempt : 0 };
  const data = path.endsWith('/graph') ? graph : path.endsWith('/pending-human-nodes') ? pending : { node, execution: selected.execution };
  if (options?.method === 'POST') return new Response(JSON.stringify({ code: 409, message: '此页是展示 fixture；请在实际 Run 中提交。' }), { status: 409 });
  return new Response(JSON.stringify(data));
};
const select = document.querySelector<HTMLSelectElement>('#scenario')!;
select.value = scenario;
select.onchange = () => { location.search = `scenario=${select.value}`; };
createRoot(document.getElementById('root')!).render(<StateMachineRunView runId="run-1" autoRefresh={false} />);
