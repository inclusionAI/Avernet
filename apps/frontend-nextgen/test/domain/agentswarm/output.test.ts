/**
 * Reading an agent's output as empty.
 *
 * Observed in a real run: three writers were asked for a word each and one
 * returned `{"text": ""}` while the gateway marked it COMPLETED. Every surface
 * showed green and the stage that produced nothing was invisible.
 */
import { isEmptyOutput } from '@/domain/agentswarm/output';

describe('isEmptyOutput', () => {
  it('treats the prose envelope as empty when the prose is', () => {
    expect(isEmptyOutput({ text: '' })).toBe(true);
    expect(isEmptyOutput({ text: '   ' })).toBe(true);
    expect(isEmptyOutput({ result: '' })).toBe(true);
    expect(isEmptyOutput({ answer: '' })).toBe(true);
  });

  it('sees through an empty envelope in any prose key', () => {
    expect(isEmptyOutput({ content: '' })).toBe(true);
  });

  it('keeps a real answer', () => {
    expect(isEmptyOutput({ text: 'brave' })).toBe(false);
    expect(isEmptyOutput('brave')).toBe(false);
    expect(isEmptyOutput({ text: 0 })).toBe(false);
    expect(isEmptyOutput({ text: false })).toBe(false);
  });

  it('judges a richer payload on its own fields', () => {
    // Not a prose envelope: absence of `text` says nothing about the rest.
    expect(isEmptyOutput({ steps: [], usage: 3 })).toBe(false);
    expect(isEmptyOutput({ a: '', b: '' })).toBe(true);
    expect(isEmptyOutput({})).toBe(true);
    expect(isEmptyOutput([])).toBe(true);
  });

  it('counts null and undefined as empty', () => {
    expect(isEmptyOutput(null)).toBe(true);
    expect(isEmptyOutput(undefined)).toBe(true);
  });
});
