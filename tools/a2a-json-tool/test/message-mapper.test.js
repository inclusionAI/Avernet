import test from 'node:test';
import assert from 'node:assert/strict';
import { ConversionError } from '../web/converter.js';
import { convertStructuredMessage } from '../web/message-mapper.js';

const fixture = () => ({
  userId: 123456,
  sessionId: 'session_demo',
  queryId: 'query_001',
  memory: null,
  message: {
    kind: 'message', role: 'USER',
    parts: [{
      kind: 'text', itemId: 'item_001', subType: 'normal', content: '想去杭州玩两天',
      hasNext: true, full: false, extParams: { scene: 'chat' },
      metadata: { textMetaKey: 'textMetaValue' }
    }],
    metadata: { msgMetaKey: 'msgMetaValue' },
    messageId: 'msg_001', taskId: 'task_001',
    extensions: ['ext_flag_1', 'ext_flag_2'],
    referenceTaskIds: ['ref_task_001', 'ref_task_002'],
    sessionId: 'session_demo'
  },
  artifact: null, bizServiceId: 'biz_demo', agentId: 'demoagent',
  metadata: { demoKey: 'sample', query: '想去杭州玩两天' }
});
const minimal = () => ({ role: 'USER', parts: [{ kind: 'text', content: '你好' }] });
const rejects = (fn, path) => assert.throws(fn, error =>
  error instanceof ConversionError && error.issues.some(issue => issue.includes(path)));

test('从业务外层提取 Message，文本与业务元数据分别保真映射', () => {
  const input = fixture();
  const { output, notes } = convertStructuredMessage(input, { requestId: 'request_demo' });
  assert.equal(output.method, 'SendMessage');
  assert.equal(output.id, 'request_demo');
  const message = output.params.message;
  assert.equal(message.role, 'ROLE_USER');
  assert.equal(message.messageId, 'msg_001');
  assert.equal(message.taskId, 'task_001');
  assert.equal(message.contextId, 'session_demo');
  assert.deepEqual(message.parts, [{
    text: '想去杭州玩两天',
    metadata: { textMetaKey: 'textMetaValue', sourceFields: {
      kind: 'text', itemId: 'item_001', subType: 'normal', hasNext: true,
      full: false, extParams: { scene: 'chat' }
    } }
  }]);
  assert.deepEqual(message.metadata, {
    msgMetaKey: 'msgMetaValue',
    sourceFields: { kind: 'message', extensions: ['ext_flag_1', 'ext_flag_2'] }
  });
  assert.deepEqual(message.referenceTaskIds, ['ref_task_001', 'ref_task_002']);
  const { message: originalMessage, sessionId: originalSessionId, ...envelope } = input;
  assert.deepEqual(output.params.metadata, { sourceEnvelope: envelope });
  assert.ok(notes.some(note => note.includes('extensions') && note.includes('metadata')));
  assert.ok(notes.every(note => !note.includes('想去杭州') && !note.includes('123456')));
});

test('根 Message 支持所有明确角色形式与原生 oneof Part', () => {
  for (const [role, expected] of [['USER', 'ROLE_USER'], ['user', 'ROLE_USER'], ['ROLE_USER', 'ROLE_USER'], ['AGENT', 'ROLE_AGENT'], ['agent', 'ROLE_AGENT'], ['ROLE_AGENT', 'ROLE_AGENT']]) {
    const input = { role, contextId: 'context_demo', parts: [
      { text: '' }, { data: null }, { data: [1, false, '中文'] },
      { raw: 'aGVsbG8=', mediaType: 'text/plain', filename: 'hello.txt' },
      { url: 'https://example.com/file.json', metadata: { from: 'example' } }
    ] };
    const { output } = convertStructuredMessage(input);
    assert.equal(output.params.message.role, expected);
    assert.equal(output.params.message.contextId, 'context_demo');
    assert.deepEqual(output.params.message.parts, input.parts);
    assert.equal(Object.hasOwn(output.params, 'metadata'), false);
  }
});

test('消息标识默认沿用，选项可覆盖；sessionId 显式映射而 queryId 不作推断', () => {
  const input = fixture();
  const { output, notes } = convertStructuredMessage(input, {
    messageId: 'message_override', requestId: 'request_override',
    contextId: 'session_demo', taskId: 'task_override'
  });
  assert.equal(output.id, 'request_override');
  assert.equal(output.params.message.messageId, 'message_override');
  assert.equal(output.params.message.contextId, 'session_demo');
  assert.equal(output.params.message.taskId, 'task_override');
  assert.ok(notes.some(note => note.includes('messageId') && note.includes('覆盖')));
  assert.ok(notes.every(note => !note.includes('message_override') && !note.includes('msg_001')));
  const emptyOptions = convertStructuredMessage(input, { messageId: '', taskId: '', requestId: '' }).output;
  assert.equal(emptyOptions.params.message.messageId, 'msg_001');
  assert.equal(emptyOptions.params.message.taskId, 'task_001');
  assert.notEqual(emptyOptions.id, input.queryId);
  const generated = convertStructuredMessage({ ...minimal(), sessionId: 'session_demo' }).output;
  assert.match(generated.id, /^[0-9a-f-]{36}$/);
  assert.match(generated.params.message.messageId, /^[0-9a-f-]{36}$/);
  assert.notEqual(generated.id, generated.params.message.messageId);
  assert.equal(generated.params.message.contextId, 'session_demo');
});

test('根、外层、内层及一致的双重 sessionId 映射到 contextId，移除重复业务字段', () => {
  const cases = [
    { ...minimal(), sessionId: 'session_private' },
    { sessionId: 'session_private', message: minimal() },
    { message: { ...minimal(), sessionId: 'session_private' } },
    { sessionId: 'session_private', message: { ...minimal(), sessionId: 'session_private' } }
  ];
  for (const input of cases) {
    const before = structuredClone(input);
    const { output, notes, noteRows } = convertStructuredMessage(input);
    assert.equal(output.params.message.contextId, 'session_private');
    assert.equal(Object.hasOwn(output.params.message, 'sessionId'), false);
    assert.equal(Object.hasOwn(output.params.message, 'metadata'), false);
    assert.equal(Object.hasOwn(output.params, 'metadata'), false);
    const expectedSources = input.message
      ? [...(Object.hasOwn(input, 'sessionId') ? ['$.sessionId'] : []), ...(Object.hasOwn(input.message, 'sessionId') ? ['$.message.sessionId'] : [])]
      : ['$.sessionId'];
    const rows = noteRows.filter(row => row.target === '$.params.message.contextId');
    assert.deepEqual(rows.map(row => row.source), expectedSources);
    assert.equal(rows.length, notes.filter(note => note.includes('sessionId') && note.includes('contextId')).length);
    assert.equal(JSON.stringify({ notes, noteRows }).includes('session_private'), false);
    assert.deepEqual(input, before);
  }
});

test('一致的已有 contextId 与选项兼容，taskId 及其他 metadata 保持原值', () => {
  const input = {
    sessionId: 'session_demo', metadata: { sessionId: 'outer_business' },
    message: { ...minimal(), sessionId: 'session_demo', contextId: 'session_demo', taskId: 'task_demo', metadata: { sessionId: 'inner_business' } }
  };
  const { output } = convertStructuredMessage(input, { contextId: 'session_demo' });
  assert.equal(output.params.message.contextId, 'session_demo');
  assert.equal(output.params.message.taskId, 'task_demo');
  assert.deepEqual(output.params.message.metadata, { sessionId: 'inner_business' });
  assert.deepEqual(output.params.metadata.sourceEnvelope, { metadata: { sessionId: 'outer_business' } });
});

test('sessionId 冲突不覆盖，错误包含字段路径而不包含原值', () => {
  const cases = [
    [{ sessionId: 'private_A', message: { ...minimal(), sessionId: 'private_B' } }, {}, ['$.sessionId', '$.message.sessionId']],
    [{ ...minimal(), sessionId: 'private_A', contextId: 'private_B' }, {}, ['$.sessionId', '$.contextId']],
    [{ message: { ...minimal(), sessionId: 'private_A', contextId: 'private_B' } }, { contextId: 'private_A' }, ['$.message.sessionId', '$.message.contextId']],
    [{ sessionId: 'private_A', message: minimal() }, { contextId: 'private_B' }, ['$.sessionId', 'options.contextId']]
  ];
  for (const [input, options, paths] of cases) {
    assert.throws(() => convertStructuredMessage(input, options), error => {
      assert.ok(error instanceof ConversionError);
      assert.ok(error.message.includes('冲突'));
      for (const path of paths) assert.ok(error.message.includes(path), path);
      assert.equal(error.message.includes('private_A'), false);
      assert.equal(error.message.includes('private_B'), false);
      return true;
    });
  }
});

test('每个提供的 sessionId 都须为非空字符串，未提供时保持旧标识规则', () => {
  for (const value of [null, '', '  ', 3, false, {}, []]) {
    rejects(() => convertStructuredMessage({ ...minimal(), sessionId: value }), '$.sessionId');
    rejects(() => convertStructuredMessage({ sessionId: value, message: minimal() }), '$.sessionId');
    rejects(() => convertStructuredMessage({ message: { ...minimal(), sessionId: value } }), '$.message.sessionId');
  }
  assert.equal(Object.hasOwn(convertStructuredMessage(minimal()).output.params.message, 'contextId'), false);
  const result = convertStructuredMessage({ ...minimal(), contextId: 'context_before' }, { contextId: 'context_after' });
  assert.equal(result.output.params.message.contextId, 'context_after');
  assert.ok(result.notes.some(note => note.includes('覆盖')));
  for (const contextId of [null, '']) {
    assert.equal(convertStructuredMessage({ ...minimal(), sessionId: 'session_demo', contextId }).output.params.message.contextId, 'session_demo');
  }
});

test('有效扩展 URI 与引用任务 ID 作为标准字段保留', () => {
  const input = { ...minimal(), extensions: ['https://example.com/ext/v1', 'urn:example:extension'], referenceTaskIds: [] };
  const { output } = convertStructuredMessage(input);
  assert.deepEqual(output.params.message.extensions, input.extensions);
  assert.deepEqual(output.params.message.referenceTaskIds, []);
});

test('完整 URI 可保留，不能将不完整地址或容错修正的地址作为协议字段输出', () => {
  for (const url of ['https://example.com/file?name=a%20b', 'urn:example:extension', 'file:///tmp/example.txt']) {
    const { output } = convertStructuredMessage({ ...minimal(), parts: [{ url }], extensions: [url] });
    assert.equal(output.params.message.parts[0].url, url);
    assert.deepEqual(output.params.message.extensions, [url]);
  }
  for (const url of ['https://', 'https://?x', 'https:example.com', 'https:///example.com', 'urn:', 'custom://', '/relative/file', 'https://example.com/%ZZ', 'https://example.com/has space', 'https://example.com/\\path']) {
    rejects(() => convertStructuredMessage({ ...minimal(), parts: [{ url }] }), 'parts[0].url');
    const extensions = ['urn:example:valid', url];
    const { output } = convertStructuredMessage({ ...minimal(), extensions });
    assert.equal(Object.hasOwn(output.params.message, 'extensions'), false);
    assert.deepEqual(output.params.message.metadata.sourceFields.extensions, extensions);
  }
});

test('根 Message 的 parts 优先于同名 message 业务字段', () => {
  const input = { ...minimal(), message: '业务提示文本' };
  const { output } = convertStructuredMessage(input);
  assert.equal(output.params.message.parts[0].text, '你好');
  assert.equal(output.params.message.metadata.sourceFields.message, '业务提示文本');
  assert.equal(Object.hasOwn(output.params, 'metadata'), false);
});

test('转换深度隔离原数据并保留特殊字段名', () => {
  const input = fixture();
  input.message.custom = JSON.parse('{"__proto__":{"safe":true}}');
  const before = structuredClone(input);
  const { output } = convertStructuredMessage(input);
  output.params.message.metadata.sourceFields.custom.__proto__.safe = false;
  output.params.message.parts[0].metadata.sourceFields.extParams.scene = 'changed';
  output.params.metadata.sourceEnvelope.metadata.query = 'changed';
  assert.deepEqual(input, before);
});

test('拒绝有歧义或无法转换的 parts，不丢弃内容', () => {
  for (const part of [null, 'text', {}, { kind: 'image', content: 'x' }, { text: 'x', data: {} }, { text: 'x', content: 'y' }, { kind: 'text', content: 3 }, { raw: 12 }, { raw: 'not@@base64' }, { url: '' }, { url: 'not-a-uri' }, { data: {}, mediaType: 5 }]) {
    rejects(() => convertStructuredMessage({ ...minimal(), parts: [part] }), 'parts[0]');
  }
  rejects(() => convertStructuredMessage({ ...minimal(), parts: [] }), 'parts');
  rejects(() => convertStructuredMessage({ ...minimal(), parts: {} }), 'parts');
});

test('元数据必须为对象，预留业务字段键冲突时明确报错', () => {
  rejects(() => convertStructuredMessage({ ...minimal(), metadata: null }), 'metadata');
  rejects(() => convertStructuredMessage({ ...minimal(), metadata: [] }), 'metadata');
  rejects(() => convertStructuredMessage({ ...minimal(), kind: 'message', metadata: { sourceFields: { original: true } } }), 'sourceFields');
  rejects(() => convertStructuredMessage({ ...minimal(), parts: [{ kind: 'text', content: 'x', metadata: { sourceFields: {} } }] }), 'sourceFields');
  rejects(() => convertStructuredMessage({ ...minimal(), parts: [{ text: 'x', metadata: null }] }), 'metadata');
  const input = { role: 'USER', metadata: { sourceFields: { original: true } }, parts: [{ text: 'x' }] };
  assert.deepEqual(convertStructuredMessage(input).output.params.message.metadata, input.metadata);
});

test('拒绝非法结构、角色、标识与非 JSON 数据', () => {
  for (const input of [null, [], 'message', {}, { message: null }, { role: 'system', parts: [{ text: 'x' }] }]) {
    assert.throws(() => convertStructuredMessage(input), ConversionError);
  }
  rejects(() => convertStructuredMessage({ ...minimal(), messageId: 3 }), 'messageId');
  rejects(() => convertStructuredMessage({ ...minimal(), referenceTaskIds: [3] }), 'referenceTaskIds');
  rejects(() => convertStructuredMessage({ ...minimal(), extensions: 'flag' }), 'extensions');
  rejects(() => convertStructuredMessage({ ...minimal(), parts: [{ data: 9007199254740992 }] }), '数');
  rejects(() => convertStructuredMessage(minimal(), { taskId: 3 }), 'taskId');
});
