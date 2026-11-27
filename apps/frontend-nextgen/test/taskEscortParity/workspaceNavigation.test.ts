import {
  readWorkspacePage,
  workspaceNavigationSearch,
  workspaceProductNavigationSearch,
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
    workspaceNavigationSearch('?host=yes&demo=1&productNavigation=1&runId=run-a&workflowId=wf-a', 'workbench', {
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
      '?host=yes&demo=1&productNavigation=1&view=runs&workflowId=wf-a&runId=r&analysisId=a&issueSignature=i&repairTaskId=p&baseline=b&current=c&nodeId=n',
      'wf-b',
      'wf-a',
    ),
  );
  expect(Object.fromEntries(next)).toEqual({ host: 'yes', view: 'runs', workflowId: 'wf-b' });
  const same = new URLSearchParams(workspaceSelectionSearch('?workflowId=wf-a&runId=r', 'wf-a', 'wf-a'));
  expect(same.get('runId')).toBe('r');
});

it.each(['', '?view=editor&workflowId=wf-a&runId=real-run&analysisId=analysis-a&host=yes&host=other&period=30d'])(
  'product navigation changes only its own marker and keeps the underlying page: %s',
  (search) => {
    const opened = workspaceProductNavigationSearch(search, true);
    expect(new URLSearchParams(opened).get('productNavigation')).toBe('1');
    expect(readWorkspacePage(opened)).toBe(readWorkspacePage(search));
    expect(workspaceProductNavigationSearch(opened, false)).toBe(search);
  },
);

it('actual destinations cannot inherit the guide marker', () => {
  const search = '?view=runs&workflowId=wf-a&runId=r&productNavigation=1&host=yes';
  const destination = new URLSearchParams(workspaceNavigationSearch(search, 'editor', { productNavigation: '1' }));
  expect(destination.has('productNavigation')).toBe(false);
  expect(destination.get('view')).toBe('editor');
  expect(destination.get('runId')).toBe('r');
  const same = new URLSearchParams(workspaceSelectionSearch(search, 'wf-a', 'wf-a'));
  expect(same.has('productNavigation')).toBe(false);
  expect(same.get('runId')).toBe('r');
});
