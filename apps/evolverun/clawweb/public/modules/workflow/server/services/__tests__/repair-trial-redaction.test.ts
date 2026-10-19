// @vitest-environment node
import { expect, it } from 'vitest';
import { redactTrial, redactTrialInput, trialSensitiveValues } from '../repair-trial-input.js';

const privateValue = 'customer-private-42';
const definition = { type: 'string', writeOnly: true, default: 'private-schema-default' };
const privateRef = { $ref: '#/$defs/credential' };
function expectPrivate(input: unknown, schema: unknown) {
  expect(JSON.stringify(redactTrialInput(input, schema))).not.toContain(privateValue);
  expect(redactTrial(`echo ${privateValue}`, trialSensitiveValues(input, schema))).toBe('echo [REDACTED]');
}

it.each([
  { properties: { customer: privateRef } },
  { allOf: [{ properties: { customer: privateRef } }, { type: 'object' }] },
  { anyOf: [{ properties: { customer: { type: 'string' } } }, { properties: { customer: privateRef } }] },
  { oneOf: [{ properties: { customer: privateRef } }, { properties: { public: { type: 'string' } } }] },
  { if: { properties: { kind: { const: 'private' } } }, then: { properties: { customer: privateRef } } },
  { patternProperties: { '^customer$': privateRef } },
  { additionalProperties: privateRef },
  { dependencies: { customer: { properties: { customer: privateRef } } } },
])('redacts referenced/composed sensitive business values and their echoes: %j', applied => {
  const schema = { type: 'object', $defs: { credential: definition }, ...applied };
  const input = { customer: privateValue };
  expectPrivate(input, schema);
  expect(input.customer).toBe(privateValue); // Stored input remains unchanged for native execution.
});

it('combines all matching property/pattern schemas while preserving public fields', () => {
  const schema = { properties: { customer: { type: 'string' }, public: { type: 'string' } },
    patternProperties: { '^customer': privateRef }, $defs: { credential: definition }, additionalProperties: { type: 'string' } };
  expect(redactTrialInput({ customer: privateValue, public: 'readable' }, schema)).toEqual({ customer: '[REDACTED]', public: 'readable' });
});

it('resolves nested and tuple-array references against the original root, including escaped pointer names', () => {
  const schema = { $defs: { 'a/b~c': definition, row: { allOf: [{ properties: { customer: { $ref: '#/$defs/a~1b~0c' } } }] } },
    properties: { rows: { type: 'array', items: { $ref: '#/$defs/row' } }, tuple: { type: 'array', items: [privateRef, { type: 'string' }] } },
    definitions: { unused: {} } };
  const withCredential = { ...schema, $defs: { ...schema.$defs, credential: definition } };
  const input = { rows: [{ customer: privateValue, public: 'visible' }], tuple: [privateValue, 'visible'] };
  expect(redactTrialInput(input, withCredential)).toEqual({ rows: [{ customer: '[REDACTED]', public: 'visible' }], tuple: ['[REDACTED]', 'visible'] });
  expectPrivate(input, withCredential);
});

it.each([
  { $ref: '#/$defs/absent' }, { $ref: 'https://example.invalid/private.json' }, { $ref: '#private-anchor' },
  { $ref: '#/$defs/loop' }, { $dynamicRef: '#/$defs/credential' }, { allOf: 'invalid' },
  { patternProperties: { '[': privateRef } }, { $id: 'different-resource', properties: { customer: privateRef } },
])('fails closed for unresolvable, cyclic or unsupported schema: %j', uncertain => {
  const schema = { $defs: { credential: definition, loop: { $ref: '#/$defs/loop' } }, properties: { context: uncertain, public: { type: 'string' } } };
  const input = { context: { customer: privateValue }, public: 'readable' };
  expectPrivate(input, schema);
  expect((redactTrialInput(input, schema) as Record<string, unknown>).public).toBe('readable');
});

it('handles recursive data schemas without leaking deep values or looping on reference cycles', () => {
  const schema = { $defs: { node: { properties: { customer: definition, next: { $ref: '#/$defs/node' } } } }, properties: { head: { $ref: '#/$defs/node' } } };
  let head: unknown = { customer: privateValue };
  for (let i = 0; i < 80; i++) head = { next: head };
  expectPrivate({ head }, schema);
});

it('redacts serialized JSON echoes of quotes, backslashes and line breaks', () => {
  const secret = 'private"quote\\slash\nline';
  const schema = { $defs: { credential: definition }, properties: { customer: privateRef } };
  const input = { customer: secret };
  expect(redactTrial(JSON.stringify(input), trialSensitiveValues(input, schema))).not.toContain(JSON.stringify(secret).slice(1, -1));
});

it('fails closed when a local pointer crosses a nested schema resource', () => {
  const schema = { $defs: { resource: { $id: 'nested', $defs: { value: { $ref: '#/$defs/public' } } }, public: { type: 'string' } },
    properties: { customer: { $ref: '#/$defs/resource/$defs/value' } } };
  expectPrivate({ customer: privateValue }, schema);
});

it('redacts typed numeric and boolean echoes of private input', () => {
  const schema = { properties: { amount: { writeOnly: true }, enabled: { sensitive: true } } };
  const values = trialSensitiveValues({ amount: 42, enabled: false }, schema);
  expect(redactTrial({ amount: 42, enabled: false, count: 3 }, values)).toEqual({ amount: '[REDACTED]', enabled: '[REDACTED]', count: 3 });
});
