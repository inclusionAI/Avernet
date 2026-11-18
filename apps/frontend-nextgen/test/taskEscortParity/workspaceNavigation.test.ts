import {
  readWorkspacePage,
  workspaceNavigationSearch,
  workspaceSelectionSearch,
} from '@/services/taskEscortParity/workspaceNavigation';

it.each([
  ['', 'dashboard'],
  ['?view=workbench', 'issues'],
  ['?view=runs&tab=editor', 'runs'],
  ['?view=missing', 'dashboard'],
  ['?view=constructor', 'dashboard'],
  ['?tab=toString', 'dashboard'],
  ['?tab=overview', 'runs'],
  ['?tab=dashboard', 'dashboard'],
  ['?tab=editor', 'editor'],
  ['?tab=management', 'settings'],
  ['?tab=evolution&evoTab=diagnosis', 'issues'],
  ['?tab=evolution&evoTab=suggestions', 'issues'],
  ['?tab=evolution&evoTab=remedies', 'experiences'],
  ['?tab=diagnosis', 'issues'],
  ['?tab=remedies', 'experiences'],
])('maps formal and reference deep links %s → %s', (search, page) => {
  expect(readWorkspacePage(search)).toBe(page);
});

it('writes canonical plus reference aliases, strips demo and preserves arbitrary host/deep-link query', () => {
  const next = new URLSearchParams(
    workspaceNavigationSearch('?host=yes&demo=1&runId=run-a&workflowId=wf-a', 'workbench', {
      analysisId: 'analysis-a',
      issueSignature: 'node/a ? error',
    }),
  );
  expect(Object.fromEntries(next)).toEqual({
    host: 'yes',
    runId: 'run-a',
    workflowId: 'wf-a',
    view: 'issues',
    tab: 'evolution',
    evoTab: 'diagnosis',
    analysisId: 'analysis-a',
    issueSignature: 'node/a ? error',
  });
  const editor = new URLSearchParams(workspaceNavigationSearch(`?${next}`, 'editor', { runId: undefined }));
  expect(editor.has('evoTab')).toBe(false);
  expect(editor.has('runId')).toBe(false);
  expect(editor.get('tab')).toBe('editor');
});

it('changing workflow resets every old workflow detail focus without dropping view or host data', () => {
  const next = new URLSearchParams(
    workspaceSelectionSearch(
      '?host=yes&demo=1&view=runs&workflowId=wf-a&runId=r&analysisId=a&issueSignature=i&repairTaskId=p&baseline=b&current=c&nodeId=n',
      'wf-b',
      'wf-a',
    ),
  );
  expect(Object.fromEntries(next)).toEqual({ host: 'yes', view: 'runs', workflowId: 'wf-b' });
  const same = new URLSearchParams(workspaceSelectionSearch('?workflowId=wf-a&runId=r', 'wf-a', 'wf-a'));
  expect(same.get('runId')).toBe('r');
});
