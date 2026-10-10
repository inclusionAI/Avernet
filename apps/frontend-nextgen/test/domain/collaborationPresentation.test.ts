import {
  fullCollaborationUrl,
  getCollaborationPresentation,
  validateCollaborationRoute,
} from '@/domain/collaborationPresentation';
import { getRouteMeta } from '@/shell/routeMeta';
import { routes } from '../../config/routes';

it.each([
  ['/workspace/collaboration-only', 'only'],
  ['/workspace/collaboration/group', 'group'],
  ['/workspace/collaboration/session', 'session'],
  ['/workspace/collaboration', 'full'],
  ['/workspace/collaboration/session/unknown', 'full'],
])('resolves exact route %s', (path, mode) => {
  expect(getCollaborationPresentation(path)).toBe(mode);
});
it('requires group and session without accepting whitespace', () => {
  expect(validateCollaborationRoute('group', {})).toContain('group');
  expect(validateCollaborationRoute('session', { groupId: 'g' })).toContain('session');
  expect(validateCollaborationRoute('group', { groupId: '  ' })).toContain('group');
  expect(validateCollaborationRoute('only', {})).toBeNull();
});
it('returns to full collaboration with encoded verified context', () => {
  expect(fullCollaborationUrl({ currentIdentityId: 'human_test', groupId: 'g', sessionId: 'g:s' })).toBe(
    '/workspace/collaboration?current=human_test&group=g&session=g%3As',
  );
  expect(fullCollaborationUrl({})).toBe('/workspace/collaboration');
});
it.each(['only', 'group', 'session'])('registers %s within the authenticated layout', (mode) => {
  const path = mode === 'only' ? '/workspace/collaboration-only' : `/workspace/collaboration/${mode}`;
  const layout = routes.find((route) => route.component === '@/layouts/AppLayout');
  expect(layout?.routes?.find((route) => route.path === path)?.component).toBe(
    '@/pages/Workspace/Collaboration/Focused',
  );
  expect(getRouteMeta(path)?.navigationVisibility).toBe('hidden');
  expect(getRouteMeta('/workspace/collaboration')?.navigationVisibility).not.toBe('hidden');
});
