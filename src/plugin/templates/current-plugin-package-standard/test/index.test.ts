import { strict as assert } from 'node:assert';
import { processMessage } from '../src/index.js';

describe('index.test.ts', () => {
  it('trims input by default', () => {
    assert(processMessage('  hello  ') === 'hello');
  });

  it('applies a prefix when provided', () => {
    assert(processMessage('world', { prefix: 'hello ' }) === 'hello world');
  });

  it('can preserve whitespace when trim is disabled', () => {
    assert(processMessage('  raw  ', { trim: false }) === '  raw  ');
  });
});
