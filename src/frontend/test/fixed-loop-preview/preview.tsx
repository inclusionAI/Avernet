import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Button } from '@/components';
import CollaborationFlowPreview from '../../src/pages/GroupChat/components/CollaborationFlowPreview';
import CollaborationValidationNotices from '../../src/pages/GroupChat/components/CollaborationValidationNotices';
import { canExecuteValidatedCollaboration } from '../../src/pages/GroupChat/utils/collaborationValidation';
import type { CollaborationDefinitionValidationResponse } from '../../src/services/backend-api/BcnController';
import fixture from '../../../bcs/tests/fixtures/fixed_loop_preview.json';
import writing from '../../../bcs/tests/fixtures/fixed_loop_logical_view.json';
import './preview.css';

const loop = { ...fixture, warnings: [], summary: { ...fixture.summary, participants: 3, nodes: 9, initial_nodes: ['draft-1'] },
  graph: { graph_mode: 'hierarchical', execution_graph_mode: 'acyclic', loops: writing.loops,
    nodes: writing.nodes.map(node => ({ ...node, judge: node.execution?.definition_node_id === 'review' })), edges: writing.edges },
} as CollaborationDefinitionValidationResponse;
const v1: CollaborationDefinitionValidationResponse = {
  valid: true, warnings: [], summary: { participants: 1, nodes: 2, initial_nodes: ['draft'] },
  graph: { graph_mode: 'acyclic', nodes: [
    { node_id: 'draft', display_name: 'Draft', kind: 'bot_task', final_output: false, judge: false },
    { node_id: 'publish', display_name: 'Publish', kind: 'bot_task', final_output: true, judge: false },
  ], edges: [{ source: 'draft', target: 'publish', outcome: 'complete' }] },
};

function Preview() {
  const [scenario, setScenario] = useState('loop');
  const [selected, setSelected] = useState<string>();
  const response = scenario === 'loop' ? loop : v1;
  return <main className="mx-auto max-w-5xl space-y-3 p-4">
    <label className="flex items-center gap-3 text-sm font-medium">预览测试场景
      <select aria-label="预览测试场景" className="rounded border border-slate-300 p-2" value={scenario}
        onChange={(event) => { setScenario(event.target.value); setSelected(undefined); }}>
        <option value="loop">Fixed Loop</option><option value="v1">普通 v1</option>
      </select>
    </label>
    <CollaborationValidationNotices warnings={response.warnings ?? []} />
    <section style={{ height: '70vh', minHeight: 520 }} className="flex overflow-hidden rounded-xl border border-slate-200/60 bg-white">
      <CollaborationFlowPreview key={scenario} graph={response.graph!} initialNodes={response.summary.initial_nodes}
        bindingViews={{ writer: { roleName: '写作者', botId: 'fixture-writer', botName: '写作助手' },
          reviewer: { roleName: '主编', botId: 'fixture-editor', botName: '内容主编' },
          polisher: { roleName: '润色编辑', botId: 'fixture-polisher', botName: '润色助手' } }}
        selectedNodeId={selected} onNodeSelect={setSelected} />
    </section>
    <p role="status" className="text-sm text-slate-600">选中的预览节点：{selected ?? '未选择'}</p>
    <Button disabled={!canExecuteValidatedCollaboration(response.warnings)}>创建协作群</Button>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Preview />);
