import test from 'node:test';
import assert from 'node:assert/strict';
import { convertInput } from '../web/convert-input.js';

test('标准业务 JSON 继续使用 data，返回可查看的解析结果', () => {
  const input = '{"question":"你好","values":[1,false,null]}';
  const result = convertInput(input, 'message', { requestId: 'req', messageId: 'msg' });
  assert.equal(result.source, 'json');
  assert.equal(result.mapping, 'data');
  assert.deepEqual(result.normalizedInput, JSON.parse(input));
  assert.deepEqual(result.output.params.message.parts[0].data, JSON.parse(input));
});

test('对象打印格式提取 message，原始业务字段保留在请求 metadata', () => {
  const input = 'userId=123456, queryId=query_demo, message=Message(role=USER, parts=[TextPart(kind=text, content=想去杭州玩两天, itemId=item_demo)], messageId=msg_demo), metadata={"demoKey":"sample"}';
  const result = convertInput(input, 'message');
  assert.equal(result.source, 'object-dump');
  assert.equal(result.mapping, 'structured-message');
  assert.equal(result.output.params.message.role, 'ROLE_USER');
  assert.equal(result.output.params.message.parts[0].text, '想去杭州玩两天');
  assert.equal(result.output.params.message.messageId, 'msg_demo');
  assert.deepEqual(result.output.params.metadata.sourceEnvelope.metadata, { demoKey: 'sample' });
  assert.equal(result.output.params.metadata.sourceEnvelope.userId, 123456);
  assert.ok(result.notes.length > 0);
});

test('标准 JSON 中明确的消息结构同样提取，非消息字段不触发映射', () => {
  const result = convertInput('{"role":"ROLE_USER","messageId":"m","parts":[{"text":"你好"}]}', 'message');
  assert.equal(result.mapping, 'structured-message');
  assert.equal(result.output.params.message.parts[0].text, '你好');
  assert.equal(convertInput('{"message":"hello","count":1}', 'message').mapping, 'data');
  assert.equal(convertInput('{"parts":["business-part"],"count":1}', 'message').mapping, 'data');
});

test('AgentCard 字段片段可转换，语法修复不填补缺失的身份或技能', () => {
  const input = '"name":"助手", "description":"分析 JSON", "version":"1.0.0", "url":"http://localhost:8080/a2a", "skills":[{"id":"analyze","name":"分析","description":"分析数据","tags":["data"]}],';
  const result = convertInput(input, 'card');
  assert.equal(result.mapping, 'card');
  assert.equal(result.output.supportedInterfaces[0].protocolVersion, '1.0');
  assert.ok(result.notes.length > 0);
  assert.throws(() => convertInput('name=只有名称', 'card'), /description/);
});

test('无法解析或映射的输入保持失败，不悄悄回退成字符串/data', () => {
  assert.throws(() => convertInput('message=Message(role=USER, parts=[UnknownPart(kind=unknown,content=test)])', 'message'));
  assert.throws(() => convertInput('message=', 'message'));
  assert.throws(() => convertInput('{}', 'wrong'), /模式/);
});

function checkNoteRows(result) {
  assert.equal(result.noteRows.length, result.notes.length);
  for (const row of result.noteRows) {
    assert.equal(typeof row.source, 'string');
    assert.equal(typeof row.target, 'string');
    assert.equal(typeof row.description, 'string');
    assert.ok(row.description.trim());
  }
}

test('普通 JSON 的结构化说明与原 notes 一一对应', () => {
  const result = convertInput('{"question":"private_input_value"}', 'message');
  checkNoteRows(result);
  assert.deepEqual(result.noteRows, [{
    source: '$', target: '$.params.message.parts[0].data',
    description: '完整保留原始 JSON，不推断或改写业务字段。'
  }]);
  assert.deepEqual(result.notes, ['原始 JSON 完整放入 message.parts[0].data；未推断或改写业务字段。']);
  assert.equal(JSON.stringify(result.noteRows).includes('private_input_value'), false);
});

test('根与嵌套消息说明使用完整路径，保留覆盖、业务字段与扩展说明', () => {
  const source = {
    role: 'USER', messageId: 'source_private_id', sessionId: 'private_session',
    parts: [{ kind: 'text', content: 'private_text_value', itemId: 'private_item' }],
    extensions: ['private_flag']
  };
  const wrapped = convertInput(JSON.stringify({ message: source, userId: 'private_user' }), 'message', { messageId: 'override_private_id' });
  checkNoteRows(wrapped);
  const expected = [
    ['$.message.messageId', '$.params.message.messageId'],
    ['$.message.sessionId', '$.params.message.contextId'],
    ['$.message.parts[0].content', '$.params.message.parts[0].text'],
    ['$.message.parts[0] 的其他业务字段', '$.params.message.parts[0].metadata.sourceFields'],
    ['$.message.extensions', '$.params.message.metadata.sourceFields.extensions'],
    ['$.message 的其他业务字段', '$.params.message.metadata.sourceFields'],
    ['$.message 和 $.sessionId 之外的字段', '$.params.metadata.sourceEnvelope']
  ];
  assert.deepEqual(wrapped.noteRows.map(({ source, target }) => [source, target]), expected);
  assert.equal(wrapped.noteRows[2].description, '转换为 A2A 文本，内容保持不变。');
  assert.equal(wrapped.noteRows[4].description, '包含非 URI 标记，作为业务字段保留，未声明为协议扩展。');
  assert.equal(wrapped.noteRows.at(-1).description, '原样保留其余外层业务字段。');
  assert.ok(wrapped.notes.includes('$.message.parts[0].content → text。'));
  assert.ok(wrapped.notes.some(note => note.includes('$.message.sessionId') && note.includes('contextId')));
  assert.equal(wrapped.notes.some(note => note.includes('未将 sessionId 推断')), false);
  assert.equal(JSON.stringify(wrapped.noteRows).includes('private_'), false);
  const root = convertInput('{"role":"USER","parts":[{"content":"hello"}]}', 'message');
  checkNoteRows(root);
  assert.deepEqual(root.noteRows.map(({ source, target }) => [source, target]), [
    ['未提供', '$.params.message.messageId'],
    ['$.parts[0].content', '$.params.message.parts[0].text']
  ]);
});

test('AgentCard 说明标明接口映射、默认值、未知和未深度验证字段', () => {
  const result = convertInput(JSON.stringify({
    name: '助手', description: '处理 JSON', version: '1.0.0', url: 'http://localhost:8080/a2a',
    skills: [{ id: 'inspect', name: '检查', description: '检查数据', tags: ['json'], custom: { value: 'private_value' } }],
    securitySchemes: { bearer: { httpAuthSecurityScheme: { scheme: 'Bearer' } } }
  }), 'card');
  checkNoteRows(result);
  assert.deepEqual(result.noteRows.map(({ source, target }) => [source, target]), [
    ['$.url', '$.supportedInterfaces[0].url'],
    ['未提供', '$.supportedInterfaces[0].protocolBinding'],
    ['未提供', '$.capabilities'],
    ['未提供', '$.defaultInputModes'],
    ['未提供', '$.defaultOutputModes'],
    ['$.securitySchemes', '$.securitySchemes'],
    ['$.skills[0].custom', '$.skills[0].custom']
  ]);
  assert.equal(result.noteRows[1].description, '默认使用 JSONRPC。');
  assert.match(result.noteRows[5].description, /未深度验证/);
  assert.match(result.noteRows[6].description, /未验证是否符合 A2A 1.0/);
  assert.ok(result.notes.includes('protocolBinding 未提供，默认使用 JSONRPC。'));
  assert.equal(JSON.stringify(result.noteRows).includes('private_value'), false);
});

test('解析修复说明按原顺序合并，转换失败仍提供解析说明行', () => {
  const result = convertInput('message=Message(role=USER,parts=[TextPart(kind=text,content=你好)])', 'message');
  checkNoteRows(result);
  const firstMapping = result.noteRows.findIndex(row => row.source !== '输入文本');
  assert.ok(firstMapping > 0);
  for (const row of result.noteRows.slice(0, firstMapping)) assert.equal(row.target, '—');
  assert.deepEqual(result.noteRows.slice(0, firstMapping).map(row => row.description), result.notes.slice(0, firstMapping));
  assert.ok(result.noteRows.slice(firstMapping).some(row => row.source === '$.message.parts[0].content'));
  assert.throws(() => convertInput('name=缺少其他字段', 'card'), error => {
    checkNoteRows(error.inputResult);
    assert.ok(error.inputResult.noteRows.length > 0);
    assert.ok(error.inputResult.noteRows.every(row => row.source === '输入文本' && row.target === '—'));
    return true;
  });
});

test('结构化片段映射内外层 sessionId，普通业务 JSON 的 sessionId 仍保留在 data', () => {
  const fragment = 'sessionId=session_demo, message=Message(role=USER,sessionId=session_demo,parts=[TextPart(kind=text,content=你好)],taskId=task_demo)';
  const result = convertInput(fragment, 'message');
  assert.equal(result.mapping, 'structured-message');
  assert.equal(result.output.params.message.contextId, 'session_demo');
  assert.equal(result.output.params.message.taskId, 'task_demo');
  assert.equal(Object.hasOwn(result.output.params, 'metadata'), false);
  assert.deepEqual(result.noteRows.filter(row => row.target === '$.params.message.contextId').map(row => row.source), ['$.sessionId', '$.message.sessionId']);
  assert.throws(() => convertInput('sessionId=one, message=Message(role=USER,sessionId=two,parts=[TextPart(content=你好)])', 'message'), /冲突/);
  const input = { sessionId: null, question: '普通业务数据' };
  const business = convertInput(JSON.stringify(input), 'message');
  assert.equal(business.mapping, 'data');
  assert.deepEqual(business.output.params.message.parts[0].data, input);
  assert.equal(Object.hasOwn(business.output.params.message, 'contextId'), false);
});
