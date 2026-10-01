import assert from 'node:assert/strict';
import { test } from 'node:test';
import { dshSessionIdForV2, resolveBcnSessionIdentity } from '../src/session-identity.js';

test('prefers the explicit canonical BCN session id', () => {
  assert.equal(resolveBcnSessionIdentity({
    sessionKey: 'group:bcs_grp_example',
    groupId: 'bcs_grp_example:wire-session',
    bcsSessionId: 'bcs_grp_example:canonical-session',
  }), 'bcs_grp_example:canonical-session');
});

test('uses the session-scoped V2 bcs_group_id instead of the shared session_key', () => {
  const first = resolveBcnSessionIdentity({
    sessionKey: 'group:bcs_grp_example',
    groupId: 'bcs_grp_example:first-session',
  });
  const second = resolveBcnSessionIdentity({
    sessionKey: 'group:bcs_grp_example',
    groupId: 'bcs_grp_example:second-session',
  });
  assert.equal(first, 'bcs_grp_example:first-session');
  assert.equal(second, 'bcs_grp_example:second-session');
  assert.notEqual(dshSessionIdForV2(first), dshSessionIdForV2(second));
});

test('preserves the historical session_key mapping for legacy group frames', () => {
  assert.equal(resolveBcnSessionIdentity({
    sessionKey: 'group:legacy-group',
    groupId: 'legacy-group',
  }), 'group:legacy-group');
});
