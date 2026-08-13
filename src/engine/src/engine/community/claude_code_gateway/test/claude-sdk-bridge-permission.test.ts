import { strict as assert } from 'node:assert';
import { shouldInstallInteractiveToolGate } from '../src/claude-sdk-bridge.js';

describe('Claude SDK interactive tool gate', () => {
  it('does not install a BCS HITL gate for bypassPermissions', () => {
    assert.equal(shouldInstallInteractiveToolGate('bypassPermissions', true), false);
  });

  it('keeps the existing HITL gate for non-bypass permission modes', () => {
    assert.equal(shouldInstallInteractiveToolGate('default', true), true);
    assert.equal(shouldInstallInteractiveToolGate('plan', true), true);
  });

  it('does not install a gate without an interaction callback', () => {
    assert.equal(shouldInstallInteractiveToolGate('default', false), false);
  });
});
