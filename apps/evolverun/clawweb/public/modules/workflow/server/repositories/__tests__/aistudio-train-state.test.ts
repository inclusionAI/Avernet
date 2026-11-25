import { describe,expect,it } from 'vitest';
import { aistudioStateKey } from '../aistudio-train-state.js';

describe('Web/engine full SHA256 base32 state-key vectors', () => {
  it.each([
    ['aisw:', ['flow-1','train',1], 'oxltucqye7ieocpvlq3grsnfqlc5dlsle2dtkzaprqmrftqgywqa'],
    ['aisa:', ['aistudio_manual_watch-1_200'], 't2c5xvpz5acids6zsldpdax3w6bru63lsa2pfrhuflvu53yfmnsq'],
  ] as const)('uses canonical JSON UTF8 input for %s', (prefix,identity,digest) => {
    const key=aistudioStateKey(prefix,identity);
    expect(key).toBe(prefix+digest); expect(key).toHaveLength(57); expect(key.slice(5)).toMatch(/^[a-z2-7]{52}$/);
  });
  it('keeps tuple delimiters, numeric attempts, case and complete long identities distinct', () => {
    const keys=[['a:b','c',1],['a','b:c',1],['A','b:c',1],['a','b:c',2],['a'.repeat(255),'b'.repeat(255),1],['a'.repeat(254)+'b','b'.repeat(255),1]];
    expect(new Set(keys.map(identity => aistudioStateKey('aisw:',identity))).size).toBe(keys.length);
  });
});
