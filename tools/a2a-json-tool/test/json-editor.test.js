import test from 'node:test';
import assert from 'node:assert/strict';
import { createJsonState, foldRangesAtDepth, renderJsonBracketMatch } from '../web/json-editor.js';

const contents = (state, depth) => foldRangesAtDepth(state, depth).map(({ from, to }) => state.sliceDoc(from, to));

test('折叠按对象与数组实际层级定位，单行和多行均可折叠', () => {
  const source = '{\n  "message": {"parts": [{"data": {"value": 1}}]},\n  "other": [1,2]\n}';
  const state = createJsonState(source);
  assert.deepEqual(contents(state, 1), [source.slice(1, -1)]);
  assert.deepEqual(contents(state, 2), ['"parts": [{"data": {"value": 1}}]', '1,2']);
  assert.deepEqual(contents(state, 3), ['{"data": {"value": 1}}']);
  assert.equal(state.doc.toString(), source);
});

test('字符串内括号与转义引号不创建折叠范围，中文和 emoji 偏移保持准确', () => {
  const source = JSON.stringify({ label: '😀中文{[\\\"]}', real: [{ value: '[]{}' }] });
  const state = createJsonState(source);
  assert.deepEqual(contents(state, 2), ['{"value":"[]{}"}']);
  assert.deepEqual(contents(state, 3), ['"value":"[]{}"']);
  assert.equal(state.doc.toString(), source);
});

test('片段中的完整结构可以折叠，未闭合和空结构不生成虚假的完整范围', () => {
  const fragment = createJsonState('"items": [1,2], "empty": {}, "unfinished": {');
  assert.ok(contents(fragment, 1).includes('1,2'));
  assert.deepEqual(foldRangesAtDepth(createJsonState('{"items": [1,2'), 1), []);
  assert.deepEqual(foldRangesAtDepth(createJsonState('{}'), 1), []);
  assert.deepEqual(foldRangesAtDepth(createJsonState('[]'), 1), []);
});

test('层级超出文档返回空范围，非法层级明确拒绝', () => {
  const state = createJsonState('{"a":1}');
  assert.deepEqual(foldRangesAtDepth(state, 3), []);
  for (const depth of [0, -1, 1.5, NaN]) assert.throws(() => foldRangesAtDepth(state, depth), RangeError);
});

test('括号标记排除字符串与字段名，保留真实配对和错配提示', () => {
  const source = '{"text":"[]{}","[key]":[]}';
  const state = createJsonState(source);
  const pair = (from, to, matched = true) => ({ start: { from, to: from + 1 }, end: { from: to, to: to + 1 }, matched });
  assert.deepEqual(renderJsonBracketMatch(pair(9, 10), state), []);
  assert.deepEqual(renderJsonBracketMatch(pair(11, 12), state), []);
  const keyStart = source.indexOf('[key]');
  assert.deepEqual(renderJsonBracketMatch(pair(keyStart, keyStart + 4), state), []);
  const marks = renderJsonBracketMatch(pair(0, source.length - 1), state);
  assert.equal(marks.length, 2);
  assert.deepEqual(marks.map(mark => source.slice(mark.from, mark.to)), ['{', '}']);
  const invalid = createJsonState('{]');
  const errors = renderJsonBracketMatch(pair(0, 1, false), invalid);
  assert.equal(errors.length, 2);
  assert.equal(errors[0].value.spec.class, 'cm-nonmatchingBracket');
});
