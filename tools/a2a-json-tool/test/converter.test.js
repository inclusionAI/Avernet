import test from 'node:test';
import assert from 'node:assert/strict';
import { ConversionError, parseJson, convertAgentCard, convertMessage } from '../web/converter.js';

const simpleCard = () => ({
  name: '数据助手',
  description: '分析 JSON 数据',
  version: '2.3.0',
  url: 'http://localhost:8080/a2a',
  skills: [{ id: 'inspect', name: '分析', description: '分析结构化数据', tags: ['json'] }],
});

const canonicalCard = () => ({
  name: '数据助手',
  description: '分析 JSON 数据',
  version: '2.3.0',
  supportedInterfaces: [{ url: 'https://agent.example/a2a', protocolBinding: 'JSONRPC', protocolVersion: '1.0' }],
  capabilities: { streaming: false, pushNotifications: false, extendedAgentCard: false },
  defaultInputModes: ['application/json'],
  defaultOutputModes: ['text/plain'],
  skills: [{ id: 'inspect', name: '分析', description: '分析结构化数据', tags: ['json'] }],
});

function rejectsWith(fn, path) {
  assert.throws(fn, error => error instanceof ConversionError && error.issues.some(issue => issue.includes(path)));
}

test('parseJson 解析合法 JSON，并报告中文语法错误', () => {
  assert.deepEqual(parseJson('{"items":[1,true,null,"中文"]}'), { items: [1, true, null, '中文'] });
  rejectsWith(() => parseJson('{"a":}'), 'JSON');
  rejectsWith(() => parseJson(''), 'JSON');
  rejectsWith(() => parseJson(123), '文本');
});

test('parseJson 拒绝非有限数和不安全整数，字符串不受影响', () => {
  for (const input of ['1e400', '-1e400', '9007199254740993', '{"count":-9007199254740992}']) {
    rejectsWith(() => parseJson(input), '数');
  }
  assert.equal(parseJson('9007199254740991'), Number.MAX_SAFE_INTEGER);
  assert.deepEqual(parseJson('{"id":"9007199254740993","text":"1e400"}'), { id: '9007199254740993', text: '1e400' });
});

test('简化 AgentCard 显式映射接口并提示所有默认值', () => {
  const result = convertAgentCard(simpleCard());
  assert.deepEqual(result.output.supportedInterfaces, [{ url: 'http://localhost:8080/a2a', protocolBinding: 'JSONRPC', protocolVersion: '1.0' }]);
  assert.deepEqual(result.output.capabilities, {});
  assert.deepEqual(result.output.defaultInputModes, ['application/json']);
  assert.deepEqual(result.output.defaultOutputModes, ['application/json']);
  assert.equal(result.output.version, '2.3.0');
  assert.equal(Object.hasOwn(result.output, 'url'), false);
  assert.equal(Object.hasOwn(result.output, 'protocolBinding'), false);
  for (const field of ['supportedInterfaces', 'capabilities', 'defaultInputModes', 'defaultOutputModes', 'JSONRPC']) {
    assert.ok(result.notes.some(note => note.includes(field)), field);
  }
});

test('规范 AgentCard 保留已提供数据且深度隔离输入', () => {
  const input = canonicalCard();
  input.provider = { url: 'https://provider.example', organization: '开发团队' };
  input.skills[0].examples = ['分析销售数据'];
  input.skills[0].inputModes = ['application/json'];
  const before = structuredClone(input);
  const { output } = convertAgentCard(input);
  assert.deepEqual(output, before);
  output.skills[0].tags.push('changed');
  output.capabilities.streaming = true;
  assert.deepEqual(input, before);
});

test('未知字段和复杂可选字段保留并标明未深度验证', () => {
  const input = canonicalCard();
  input.vendor = { mode: 'custom' };
  input.capabilities.extensions = [{ uri: 'https://example.com/ext', params: { values: [1, 2] } }];
  input.securitySchemes = { bearer: { httpAuthSecurityScheme: { scheme: 'Bearer' } } };
  input.securityRequirements = [{ schemes: { bearer: { list: [] } } }];
  input.skills[0].securityRequirements = input.securityRequirements;
  const { output, notes } = convertAgentCard(input);
  assert.deepEqual(output, input);
  for (const field of ['vendor', 'capabilities.extensions', 'securitySchemes', 'securityRequirements', 'skills[0].securityRequirements']) {
    assert.ok(notes.some(note => note.includes(field) && note.includes('验证')), field);
  }
});

test('AgentCard 不虚构身份、技能、版本；检查必需类型', () => {
  for (const field of ['name', 'description', 'version', 'skills']) {
    const input = simpleCard();
    delete input[field];
    rejectsWith(() => convertAgentCard(input), field);
  }
  for (const input of [null, [], 'card', 1]) rejectsWith(() => convertAgentCard(input), '对象');
  for (const [field, value] of [['name', '  '], ['description', 3], ['version', null], ['skills', []], ['capabilities', []], ['defaultInputModes', 'json'], ['defaultOutputModes', []]]) {
    rejectsWith(() => convertAgentCard({ ...simpleCard(), [field]: value }), field);
  }
});

test('检查 provider、capabilities、skills 和接口的常见嵌套字段', () => {
  rejectsWith(() => convertAgentCard({ ...simpleCard(), provider: { organization: '团队' } }), 'provider.url');
  rejectsWith(() => convertAgentCard({ ...simpleCard(), capabilities: { streaming: 'true' } }), 'capabilities.streaming');
  rejectsWith(() => convertAgentCard({ ...simpleCard(), skills: [{ id: 'x' }] }), 'skills[0].name');
  rejectsWith(() => convertAgentCard({ ...simpleCard(), skills: [{ ...simpleCard().skills[0], tags: [7] }] }), 'skills[0].tags[0]');
  const input = canonicalCard();
  input.supportedInterfaces[0].protocolVersion = '1.0.0';
  rejectsWith(() => convertAgentCard(input), 'protocolVersion');
  input.supportedInterfaces[0].protocolVersion = '1.0';
  input.supportedInterfaces[0].protocolBinding = 'CUSTOM';
  rejectsWith(() => convertAgentCard(input), 'protocolBinding');
  rejectsWith(() => convertAgentCard({ ...canonicalCard(), supportedInterfaces: [] }), 'supportedInterfaces');
});

test('AgentCard 的必需 tags 数组至少包含一个标签', () => {
  for (const makeCard of [simpleCard, canonicalCard]) {
    const input = makeCard();
    input.skills[0].tags = [];
    rejectsWith(() => convertAgentCard(input), 'skills[0].tags');
  }
});

test('接口接受 HTTPS、本机 HTTP、GRPC 主机端口；拒绝其他 HTTP 与非法地址', () => {
  for (const url of ['https://agent.example/a2a', 'http://localhost:8080/a2a', 'http://127.0.0.1:8080', 'http://[::1]:8080']) {
    assert.equal(convertAgentCard({ ...simpleCard(), url }).output.supportedInterfaces[0].url, url);
  }
  for (const url of ['http://agent.example/a2a', '/a2a', 'javascript:alert(1)', 'http://localhost.evil.example/a2a', 'https:example.com', 'http:localhost', 'https://user:pass@agent.example', 'grpc.example:443']) {
    rejectsWith(() => convertAgentCard({ ...simpleCard(), url }), 'url');
  }
  assert.equal(convertAgentCard({ ...simpleCard(), url: 'grpc.example:443', protocolBinding: 'GRPC' }).output.supportedInterfaces[0].url, 'grpc.example:443');
  assert.equal(convertAgentCard({ ...simpleCard(), protocolBinding: 'HTTP+JSON' }).output.supportedInterfaces[0].protocolBinding, 'HTTP+JSON');
  rejectsWith(() => convertAgentCard({ ...simpleCard(), protocolBinding: null }), 'protocolBinding');
  rejectsWith(() => convertAgentCard({ ...simpleCard(), url: '[:::]:443', protocolBinding: 'GRPC' }), 'url');
});

test('拒绝旧版字段、接口歧义及已有签名', () => {
  for (const field of ['preferredTransport', 'additionalInterfaces', 'security', 'supportsAuthenticatedExtendedCard', 'protocolVersion', 'signatures']) {
    rejectsWith(() => convertAgentCard({ ...simpleCard(), [field]: [] }), field);
  }
  rejectsWith(() => convertAgentCard({ ...canonicalCard(), url: 'https://other.example' }), 'url');
  rejectsWith(() => convertAgentCard({ ...canonicalCard(), protocolBinding: 'GRPC' }), 'protocolBinding');
});

test('任意业务 JSON 保真封装为 A2A 1.0 SendMessage', () => {
  for (const input of [null, false, true, 0, 1.25, '中文', [1, 'x'], { question: '你好', kind: 'business', nested: { role: 'user' } }]) {
    const { output, notes } = convertMessage(input, { requestId: 'req-1', messageId: 'msg-1' });
    assert.deepEqual(output, {
      jsonrpc: '2.0', id: 'req-1', method: 'SendMessage',
      params: { message: { messageId: 'msg-1', role: 'ROLE_USER', parts: [{ data: input, mediaType: 'application/json' }] } },
    });
    assert.ok(Array.isArray(notes));
  }
});

test('消息生成独立 UUID，可给关联 ID 且不修改源数据', () => {
  const input = { items: [{ price: 3 }] };
  const { output } = convertMessage(input, { contextId: 'ctx-1', taskId: 'task-1' });
  assert.match(output.id, /^[0-9a-f-]{36}$/);
  assert.match(output.params.message.messageId, /^[0-9a-f-]{36}$/);
  assert.notEqual(output.id, output.params.message.messageId);
  assert.equal(output.params.message.contextId, 'ctx-1');
  assert.equal(output.params.message.taskId, 'task-1');
  output.params.message.parts[0].data.items[0].price = 9;
  assert.equal(input.items[0].price, 3);
  const empty = convertMessage(null, { messageId: '', requestId: '', contextId: '', taskId: '' }).output;
  assert.ok(empty.id);
  assert.equal(Object.hasOwn(empty.params.message, 'contextId'), false);
  assert.equal(Object.hasOwn(empty.params.message, 'taskId'), false);
});

test('消息选项给出字段错误；允许只给 taskId 让服务端推断 contextId', () => {
  const message = convertMessage({}, { taskId: 'task-1' }).output.params.message;
  assert.equal(message.taskId, 'task-1');
  assert.equal(Object.hasOwn(message, 'contextId'), false);
  for (const field of ['messageId', 'requestId', 'contextId', 'taskId']) {
    rejectsWith(() => convertMessage({}, { [field]: 5 }), field);
  }
});

test('直接调用同样拒绝非法 JSON 数值和数据，不吞失字段', () => {
  for (const input of [NaN, Infinity, 9007199254740992, { item: undefined }, undefined, new Date()]) {
    rejectsWith(() => convertMessage(input), '$');
  }
  const cycle = {}; cycle.self = cycle;
  rejectsWith(() => convertMessage(cycle), '$.self');
  const input = JSON.parse('{"__proto__":{"safe":true},"constructor":{"value":1}}');
  assert.deepEqual(convertMessage(input).output.params.message.parts[0].data, input);
});
