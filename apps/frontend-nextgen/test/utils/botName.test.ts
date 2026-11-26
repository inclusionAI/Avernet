/** @jest-environment node */
// FE bot name validator mirrors backend ``validate_bot_name`` (bot_service.py):
// only Chinese/letters/digits/underscore/hyphen/space and + ( ), length <= 32.
// These cases pin the contract so the FE does not accept a name the server
// rejects (copy/create otherwise pack the manifest + authorize first, then
// fail the async create_bot task and leave a half-built bot 404ing forever).
import { BOT_NAME_MAX_LEN, botNameHasInvalidCharacter } from '@/utils/botName';
import { describe, expect, it } from '@jest/globals';

describe('botNameHasInvalidCharacter', () => {
  it('accepts the backend-allowed charset', () => {
    // Chinese, latin, digits, underscore, hyphen, space, +, (, ).
    expect(botNameHasInvalidCharacter('项目知识助手')).toBe(false);
    expect(botNameHasInvalidCharacter('测试 Bot')).toBe(false);
    expect(botNameHasInvalidCharacter('bot_v1 (beta)+')).toBe(false);
    expect(botNameHasInvalidCharacter('A 1-_+()')).toBe(false);
  });

  it('rejects the characters the backend regex excludes', () => {
    // The three copy-recent failures were characters the old FE (only '@')
    // let through but the server rejects: '.', '#', '/', '@', and others.
    expect(botNameHasInvalidCharacter('bot v1.2')).toBe(true); // '.'
    expect(botNameHasInvalidCharacter('bot#1')).toBe(true);
    expect(botNameHasInvalidCharacter('a/b')).toBe(true);
    expect(botNameHasInvalidCharacter('a@b')).toBe(true);
    expect(botNameHasInvalidCharacter('a,b')).toBe(true);
    expect(botNameHasInvalidCharacter('a:b')).toBe(true);
    expect(botNameHasInvalidCharacter('a;b')).toBe(true);
    expect(botNameHasInvalidCharacter('a&b')).toBe(true);
    expect(botNameHasInvalidCharacter('a*b')).toBe(true);
    // CJK punctuation is outside the CJK Unified Ideographs block.
    expect(botNameHasInvalidCharacter('测试、Bot')).toBe(true);
    expect(botNameHasInvalidCharacter('测试。Bot')).toBe(true);
  });
});

describe('BOT_NAME_MAX_LEN', () => {
  it('matches the backend 32-char cap', () => {
    expect(BOT_NAME_MAX_LEN).toBe(32);
  });
});
