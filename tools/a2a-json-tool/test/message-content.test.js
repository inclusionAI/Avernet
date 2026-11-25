import test from 'node:test';
import assert from 'node:assert/strict';
import { convertInput } from '../web/convert-input.js';
import { findFieldAtSelection, resolveFieldPath } from '../web/field-links.js';

const convert = input => convertInput(JSON.stringify(input), 'message');

test('根及嵌套 role + content 将任意 JSON 内容原样转换为 data', () => {
  for (const content of ['hello', '', 0, false, null, [1, false], { text: '业务值' }]) {
    for (const wrapped of [false, true]) {
      const source = { role: 'user', content, messageId: 'msg_demo', sessionId: 'session_demo', custom: '保留' };
      const input = wrapped ? { parts: ['外层业务字段'], message: source } : source;
      const before = structuredClone(input);
      const result = convert(input);
      const message = result.output.params.message;
      assert.equal(result.mapping, 'structured-message');
      assert.equal(message.role, 'ROLE_USER');
      assert.equal(message.messageId, 'msg_demo');
      assert.equal(message.contextId, 'session_demo');
      assert.deepEqual(message.parts, [{ data: content, mediaType: 'application/json' }]);
      assert.deepEqual(message.metadata.sourceFields, { custom: '保留' });
      if (wrapped) assert.deepEqual(result.output.params.metadata.sourceEnvelope, { parts: ['外层业务字段'] });
      assert.ok(result.noteRows.some(row => row.source === `${wrapped ? '$.message' : '$'}.content`
        && row.target === '$.params.message.parts[0].data'));
      assert.deepEqual(input, before);
    }
  }
});

test('parts 优先，Message.content 保留到 sourceFields，Part.content 继续映射 text', () => {
  for (const wrapped of [false, true]) {
    const content = { reason: '备用内容' };
    const source = { role: 'AGENT', parts: [{ content: '使用 parts' }], content, metadata: { existing: true } };
    const result = convert(wrapped ? { message: source } : source);
    assert.deepEqual(result.output.params.message.parts, [{ text: '使用 parts' }]);
    assert.deepEqual(result.output.params.message.metadata, { existing: true, sourceFields: { content } });
    assert.ok(result.noteRows.some(row => row.source === `${wrapped ? '$.message' : '$'}.content`
      && row.target === '$.params.message.metadata.sourceFields.content'));
  }
});

test('content 映射后保持深拷贝隔离，metadata 保留键冲突不覆盖', () => {
  for (const useParts of [false, true]) {
    const source = { role: 'USER', content: { values: [1] }, ...(useParts ? { parts: [{ text: 'parts' }] } : {}) };
    const result = convert(source);
    const mapped = useParts ? result.output.params.message.metadata.sourceFields.content : result.output.params.message.parts[0].data;
    mapped.values.push(2);
    assert.deepEqual(source.content, { values: [1] });
  }
  assert.throws(() => convert({ role: 'USER', parts: [{ text: 'parts' }], content: 'hello', metadata: { sourceFields: { content: 'existing' } } }), /sourceFields/);
});

test('普通业务对象不因 role 或 parts 单独出现而误判，明确消息的错误保持失败', () => {
  for (const source of [{ role: 'user' }, { parts: ['business'] }, { content: 'hello' }, { role: 'business', content: 'hello' }]) {
    for (const input of [source, { message: source }]) {
      const result = convert(input);
      assert.equal(result.mapping, 'data');
      assert.deepEqual(result.output.params.message.parts[0].data, input);
    }
  }
  for (const parts of [null, [], {}, 'invalid', [{ url: 'https://' }]]) {
    for (const wrapped of [false, true]) {
      const source = { role: 'USER', parts, content: '不能回退' };
      assert.throws(() => convert(wrapped ? { message: source } : source), /parts/);
    }
  }
  assert.throws(() => convert({ message: { role: 'USER', messageId: 'msg_demo' } }), /parts/);
  assert.throws(() => convert({ message: { role: 'invalid', parts: [{ text: 'hello' }], content: '不能回退' } }), /role/);
});

test('根及嵌套 content 的字段名和值联动到 data 或 sourceFields 的实际路径', () => {
  for (const wrapped of [false, true]) {
    for (const useParts of [false, true]) {
      const source = { role: 'USER', content: { label: '选中内容' }, ...(useParts ? { parts: [{ content: 'parts 内容' }] } : {}) };
      const result = convert(wrapped ? { message: source } : source);
      const sourceBase = wrapped ? ['message'] : [];
      const targetBase = useParts ? ['params', 'message', 'metadata', 'sourceFields', 'content'] : ['params', 'message', 'parts', 0, 'data'];
      assert.deepEqual(resolveFieldPath([...sourceBase, 'content'], result), targetBase);
      const path = [...sourceBase, 'content', 'label'];
      const field = result.inputFields.find(item => JSON.stringify(item.path) === JSON.stringify(path));
      for (const [start, end] of [[field.start + 1, field.end - 1], [field.valueStart + 1, field.valueEnd - 1]]) {
        const selected = findFieldAtSelection(result.inputFields, start, end);
        assert.deepEqual(resolveFieldPath(selected.path, result), [...targetBase, 'label']);
      }
      if (useParts) assert.deepEqual(resolveFieldPath([...sourceBase, 'parts', 0, 'content'], result), ['params', 'message', 'parts', 0, 'text']);
    }
  }
});
