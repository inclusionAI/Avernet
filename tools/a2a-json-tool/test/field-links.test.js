import test from 'node:test';
import assert from 'node:assert/strict';
import { convertInput } from '../web/convert-input.js';
import { parseInput } from '../web/input-parser.js';
import { resolveFieldPath, findFieldAtSelection } from '../web/field-links.js';

const convert = (input, mode = 'message', options = {}) => convertInput(JSON.stringify(input), mode, options);
const messagePath = (...path) => ['params', 'message', ...path];

test('普通 JSON 使用完整数据前缀，并保留嵌套数组与同名字段路径', () => {
  const result = convert({ role: 'business', metadata: { role: 'nested' }, rows: [{ role: 'first' }, { role: 'second' }] });
  assert.equal(result.mapping, 'data');
  for (const path of [['role'], ['metadata'], ['metadata', 'role'], ['rows', 1, 'role']]) {
    assert.deepEqual(resolveFieldPath(path, result), messagePath('parts', 0, 'data', ...path));
  }
  assert.equal(resolveFieldPath(['rows', 2, 'role'], result), null);
});

test('AgentCard 简化接口映射，其他已保留字段按真实路径关联', () => {
  const result = convert({
    name: '测试 Agent', description: '检查映射', version: '1.0.0',
    url: 'http://localhost:8080/a2a', protocolBinding: 'JSONRPC',
    skills: [{ id: 'echo', name: '回显', description: '回显输入', tags: ['json'] }]
  }, 'card');
  assert.deepEqual(resolveFieldPath(['url'], result), ['supportedInterfaces', 0, 'url']);
  assert.deepEqual(resolveFieldPath(['protocolBinding'], result), ['supportedInterfaces', 0, 'protocolBinding']);
  assert.deepEqual(resolveFieldPath(['skills', 0, 'name'], result), ['skills', 0, 'name']);
  assert.equal(resolveFieldPath(['capabilities'], result), null);
  const canonical = convert(result.output, 'card');
  assert.deepEqual(resolveFieldPath(['supportedInterfaces', 0, 'url'], canonical), ['supportedInterfaces', 0, 'url']);
});

test('包装消息按各层 metadata、sessionId 与数组下标分别定位', () => {
  const result = convert({
    sessionId: 'session_shared', metadata: { sessionId: 'outer-meta' },
    message: {
      role: 'USER', sessionId: 'session_shared',
      metadata: { sessionId: 'message-meta' },
      parts: [
        { kind: 'text', content: '甲', itemId: 'first', metadata: { sessionId: 'part-meta' } },
        { kind: 'text', content: '乙', extParams: { metadata: { content: 'business' } } }
      ]
    }
  });
  const rows = [
    [['message'], messagePath()],
    [['message', 'role'], messagePath('role')],
    [['message', 'parts'], messagePath('parts')],
    [['message', 'parts', 1, 'content'], messagePath('parts', 1, 'text')],
    [['message', 'parts', 0, 'kind'], messagePath('parts', 0, 'metadata', 'sourceFields', 'kind')],
    [['message', 'parts', 0, 'itemId'], messagePath('parts', 0, 'metadata', 'sourceFields', 'itemId')],
    [['message', 'parts', 0, 'metadata'], messagePath('parts', 0, 'metadata')],
    [['message', 'parts', 0, 'metadata', 'sessionId'], messagePath('parts', 0, 'metadata', 'sessionId')],
    [['message', 'parts', 1, 'extParams', 'metadata', 'content'], messagePath('parts', 1, 'metadata', 'sourceFields', 'extParams', 'metadata', 'content')],
    [['message', 'metadata', 'sessionId'], messagePath('metadata', 'sessionId')],
    [['message', 'sessionId'], messagePath('contextId')],
    [['metadata', 'sessionId'], ['params', 'metadata', 'sourceEnvelope', 'metadata', 'sessionId']],
    [['sessionId'], messagePath('contextId')]
  ];
  for (const [source, expected] of rows) assert.deepEqual(resolveFieldPath(source, result), expected, JSON.stringify(source));
});

test('根 Message 与其同名业务 message 字段保持独立', () => {
  const result = convert({ role: 'AGENT', message: { role: 'business' }, parts: [{ text: 'hello' }, { data: { text: 'data-text' }, mediaType: 'application/json' }] });
  assert.deepEqual(resolveFieldPath(['role'], result), messagePath('role'));
  assert.deepEqual(resolveFieldPath(['message', 'role'], result), messagePath('metadata', 'sourceFields', 'message', 'role'));
  assert.deepEqual(resolveFieldPath(['parts', 0, 'text'], result), messagePath('parts', 0, 'text'));
  assert.deepEqual(resolveFieldPath(['parts', 1, 'data', 'text'], result), messagePath('parts', 1, 'data', 'text'));
  assert.deepEqual(resolveFieldPath(['parts', 1, 'mediaType'], result), messagePath('parts', 1, 'mediaType'));
});

test('根、包装内外层 sessionId 的字段名和值均定位 contextId，其他层同名字段不改变', () => {
  for (const input of [
    { role: 'USER', sessionId: 'same_session', parts: [{ text: 'hello', sessionId: 'part_business' }] },
    { sessionId: 'same_session', message: { role: 'USER', sessionId: 'same_session', parts: [{ text: 'hello', sessionId: 'part_business' }] } }
  ]) {
    const text = JSON.stringify(input);
    const result = convertInput(text, 'message');
    for (const field of result.inputFields.filter(field => field.path.at(-1) === 'sessionId')) {
      const isPart = field.path.includes('parts');
      const expected = isPart ? messagePath('parts', 0, 'metadata', 'sourceFields', 'sessionId') : messagePath('contextId');
      for (const [start, end] of [[field.start + 1, field.end - 1], [field.valueStart + 1, field.valueEnd - 1]]) {
        const selected = findFieldAtSelection(result.inputFields, start, end);
        assert.deepEqual(resolveFieldPath(selected.path, result), expected);
      }
    }
  }
  const business = convert({ sessionId: 'business_session', question: 'hello' });
  assert.deepEqual(resolveFieldPath(['sessionId'], business), messagePath('parts', 0, 'data', 'sessionId'));
});

test('扩展依实际输出位置关联，标准引用与原生文件字段直接定位', () => {
  const base = { role: 'USER', parts: [{ raw: 'aGVsbG8=', filename: 'hello.txt' }, { url: 'https://example.com/file' }], referenceTaskIds: ['task_demo'] };
  const valid = convert({ ...base, extensions: ['https://example.com/extension'] });
  const custom = convert({ ...base, extensions: ['custom_flag'] });
  assert.deepEqual(resolveFieldPath(['extensions'], valid), messagePath('extensions'));
  assert.deepEqual(resolveFieldPath(['extensions', 0], custom), messagePath('metadata', 'sourceFields', 'extensions', 0));
  assert.deepEqual(resolveFieldPath(['referenceTaskIds', 0], valid), messagePath('referenceTaskIds', 0));
  for (const path of [['parts', 0, 'raw'], ['parts', 0, 'filename'], ['parts', 1, 'url']]) {
    assert.deepEqual(resolveFieldPath(path, valid), messagePath(...path));
  }
});

test('ID 为空或被选项覆盖时，以目标是否实际存在决定关联', () => {
  const input = { role: 'USER', messageId: null, taskId: null, contextId: '', parts: [{ text: 'hello' }] };
  const result = convert(input);
  assert.deepEqual(resolveFieldPath(['messageId'], result), messagePath('messageId'));
  assert.equal(resolveFieldPath(['taskId'], result), null);
  assert.equal(resolveFieldPath(['contextId'], result), null);
  const overridden = convert(input, 'message', { taskId: 'task_override', messageId: 'message_override' });
  assert.deepEqual(resolveFieldPath(['taskId'], overridden), messagePath('taskId'));
  assert.deepEqual(resolveFieldPath(['messageId'], overridden), messagePath('messageId'));
});

test('对象打印输入按 normalizedInput 定位；不修改传入路径', () => {
  const result = convertInput('message=Message(role=USER, parts=[TextPart(kind=text, content=你好, hasNext=true)])', 'message');
  const path = ['message', 'parts', 0, 'hasNext'];
  assert.deepEqual(resolveFieldPath(path, result), messagePath('parts', 0, 'metadata', 'sourceFields', 'hasNext'));
  assert.deepEqual(path, ['message', 'parts', 0, 'hasNext']);
});

test('非法或不存在的路径返回 null；特殊自有字段不触发原型寻址', () => {
  const result = convertInput('{"__proto__":{"safe":true},"constructor":{"own":1},"rows":[{"key":1}]}', 'message');
  assert.deepEqual(resolveFieldPath(['__proto__', 'safe'], result), messagePath('parts', 0, 'data', '__proto__', 'safe'));
  for (const path of [null, 'rows', [], ['toString'], ['constructor', 'prototype'], ['rows', '0', 'key'], ['rows', -1], ['rows', 0, 'missing'], ['rows', 0, 'key', 'toString']]) {
    assert.equal(resolveFieldPath(path, result), null);
  }
  assert.equal(resolveFieldPath(['rows'], { ...result, output: {} }), null);
  assert.equal(resolveFieldPath(['rows'], { ...result, mapping: 'unknown' }), null);
  assert.equal(resolveFieldPath(['rows'], null), null);
});

test('按 UTF-16 key 区间定位，忽略值中的同名文字与跨字段选择', () => {
  const text = '{"标题":"😀", "role":"role", "nested":{"role":"x"}}';
  const first = text.indexOf('"role"');
  const second = text.lastIndexOf('"role"');
  const fields = [
    { path: ['role'], start: first, end: first + 6 },
    { path: ['nested', 'role'], start: second, end: second + 6 }
  ];
  assert.equal(findFieldAtSelection(fields, first + 1, first + 5), fields[0]);
  assert.equal(findFieldAtSelection(fields, second + 1, second + 5), fields[1]);
  assert.equal(findFieldAtSelection(fields, first + 2, first + 2), fields[0]);
  const value = text.indexOf('"role"', first + 6);
  assert.equal(findFieldAtSelection(fields, value + 1, value + 5), null);
  assert.equal(findFieldAtSelection(fields, first, second + 6), null);
  assert.equal(findFieldAtSelection(fields, first - 1, first + 6), null);
  assert.equal(findFieldAtSelection(fields, first + 6, first + 6), null);
});

test('选择参数不合法、匹配不唯一或没有 key 时不关联', () => {
  const field = { path: ['key'], start: 2, end: 7 };
  for (const [start, end] of [[-1, 3], [4, 3], [2.5, 5], [NaN, 5], [2, Infinity]]) {
    assert.equal(findFieldAtSelection([field], start, end), null);
  }
  assert.equal(findFieldAtSelection([field, { ...field, path: ['other'] }], 3, 5), null);
  assert.equal(findFieldAtSelection([], 3, 5), null);
  assert.equal(findFieldAtSelection(null, 3, 5), null);
});

test('不同层相同 sessionId 值按位置归属，值中的 role 不匹配别处的 role key', () => {
  const text = JSON.stringify({
    sessionId: 'same_session', metadata: { sessionId: 'same_session' },
    message: { role: 'USER', sessionId: 'same_session', metadata: { reason: 'role' }, parts: [{ content: '你好' }] }
  });
  const result = convertInput(text, 'message');
  for (const path of [['sessionId'], ['metadata', 'sessionId'], ['message', 'sessionId'], ['message', 'metadata', 'reason']]) {
    const field = result.inputFields.find(field => JSON.stringify(field.path) === JSON.stringify(path));
    const selected = findFieldAtSelection(result.inputFields, field.valueStart + 1, field.valueEnd - 1);
    assert.deepEqual(selected.path, path);
    assert.deepEqual(resolveFieldPath(selected.path, result), resolveFieldPath(path, result));
  }
  const role = result.inputFields.find(field => JSON.stringify(field.path) === '["message","role"]');
  assert.equal(findFieldAtSelection(result.inputFields, role.start + 1, role.end - 1), role);
});

test('字符串子词、数值、null 与数组纯值定位最近所属字段', () => {
  const text = '{"userId":123456,"memory":null,"flags":["one","two"],"message":{"role":"USER","parts":[{"content":"😀 hello world","dataInfo":{"values":[true,42]}}]}}';
  const result = convertInput(text, 'message');
  for (const [token, path] of [
    ['123456', ['userId']], ['null', ['memory']], ['two', ['flags']],
    ['hello', ['message', 'parts', 0, 'content']], ['42', ['message', 'parts', 0, 'dataInfo', 'values']]
  ]) {
    const start = text.indexOf(token);
    assert.deepEqual(findFieldAtSelection(result.inputFields, start, start + token.length).path, path);
  }
  const contentStart = text.indexOf('hello');
  const content = findFieldAtSelection(result.inputFields, contentStart, contentStart + 5);
  assert.deepEqual(resolveFieldPath(content.path, result), messagePath('parts', 0, 'text'));
});

test('片段、构造器及 CRLF 围栏中的值使用原始 UTF-16 位置', () => {
  const text = ' \r\n```text\r\n  message=Message(role=USER, parts=[TextPart(content="😀 hello world", metadata={trace:"role"})]), userId=123456  \r\n```\r\n';
  const { fields } = parseInput(text, { includeFields: true });
  for (const [token, path] of [
    ['USER', ['message', 'role']], ['hello', ['message', 'parts', 0, 'content']],
    ['"role"', ['message', 'parts', 0, 'metadata', 'trace']], ['123456', ['userId']]
  ]) {
    const start = text.indexOf(token);
    assert.deepEqual(findFieldAtSelection(fields, start, start + token.length).path, path);
  }
});

test('跨字段和 token 边界选择不回退到父对象，等长歧义值范围不关联', () => {
  const text = '{"nested":{"first":"one", "second":"two"}, "last":null}';
  const { fields } = parseInput(text, { includeFields: true });
  const first = fields.find(field => field.path.at(-1) === 'first');
  const second = fields.find(field => field.path.at(-1) === 'second');
  assert.equal(findFieldAtSelection(fields, first.valueStart + 1, second.valueEnd - 1), null);
  assert.equal(findFieldAtSelection(fields, first.start - 1, first.end), null);
  assert.equal(findFieldAtSelection(fields, first.start, first.valueEnd), null);
  const last = fields.find(field => field.path[0] === 'last');
  assert.equal(findFieldAtSelection(fields, last.valueEnd, last.valueEnd), null);
  const duplicate = { ...first, path: ['other'] };
  assert.equal(findFieldAtSelection([...fields, duplicate], first.valueStart + 1, first.valueEnd - 1), null);
});
