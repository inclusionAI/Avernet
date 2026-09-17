import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { CollaborationDefinitionValidationResponse } from '@/services/backend-api/BcnController';
import CollaborationValidationNotices from '../CollaborationValidationNotices';
import CollaborationPreviewMetadata from '../CollaborationPreviewMetadata';

const response: CollaborationDefinitionValidationResponse = JSON.parse(readFileSync(
  resolve(__dirname, '../../../../../../bcs/tests/fixtures/fixed_loop_preview.json'), 'utf8',
));

it('shows preview-only availability and the authoring diagnostic path', () => {
  const html = renderToStaticMarkup(React.createElement(CollaborationValidationNotices, { warnings: response.warnings! }));
  expect(html).toContain('暂不能创建协作群');
  expect(html).toContain('VALIDATION_ONLY_FEATURE');
  expect(html).toContain('$.runtime.state_machine.version');
  expect(html).toContain('version 2 execution is not enabled');
});

it('keeps limits in collapsed details without displaying future execution counts', () => {
  const html = renderToStaticMarkup(React.createElement(CollaborationPreviewMetadata, { graph: response.graph! }));
  expect(html).not.toContain('展开后');
  expect(html).not.toContain('兜底');
  expect(html).toContain('Rounds：max_iterations = 3');
  expect(html).toContain('<summary class="cursor-pointer">循环限制</summary>');
  expect(html).not.toContain('<details open');
  expect(html).toContain('节点 ID 仅用于当前预览');
});

it('omits Loop notices and metadata for an ordinary v1 preview', () => {
  expect(renderToStaticMarkup(React.createElement(CollaborationValidationNotices, { warnings: [] }))).toBe('');
  expect(renderToStaticMarkup(React.createElement(CollaborationPreviewMetadata, {
    graph: { graph_mode: 'acyclic', nodes: [], edges: [] },
  }))).toBe('');
});
