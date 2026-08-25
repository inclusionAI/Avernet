import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Re-implement unwrapEnvelope here for the test (the production copy is
// inlined into the TSX). Keep them in sync.
function unwrapEnvelope(body) {
  if (
    body &&
    typeof body === 'object' &&
    !Array.isArray(body) &&
    ('code' in body || 'request_id' in body) &&
    'data' in body
  ) {
    return body.data;
  }
  return body;
}

const tests = [
  { name: 'raw object passthrough', input: { nodes: [], edges: [] }, expected: { nodes: [], edges: [] } },
  { name: 'raw array passthrough', input: [1, 2, 3], expected: [1, 2, 3] },
  { name: 'enveloped object unwrapped', input: { code: 20000, message: 'OK', data: { nodes: ['x'] }, request_id: 'r' }, expected: { nodes: ['x'] } },
  { name: 'object with data field but no envelope markers passes through', input: { data: { a: 1 } }, expected: { data: { a: 1 } } },
];

let failed = 0;
for (const t of tests) {
  const got = unwrapEnvelope(t.input);
  if (JSON.stringify(got) !== JSON.stringify(t.expected)) {
    failed++;
    console.error(`FAIL ${t.name}: got ${JSON.stringify(got)}`);
  }
}
if (failed) {
  process.exit(1);
}
console.log('unwrapEnvelope tests OK');