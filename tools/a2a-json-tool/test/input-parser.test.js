import test from 'node:test';
import assert from 'node:assert/strict';
import { parseInput } from '../web/input-parser.js';
import { ConversionError } from '../web/converter.js';

const dump = 'userId=123456, sessionId=session_demo, queryId=query_001, memory=null, message=Message(kind=message, role=USER, parts=[TextPart(kind=text, itemId=item_001, subType=normal, content=想去杭州玩两天, hasNext=true, full=false, extParams={"scene":"chat"}, metadata={"textMetaKey":"textMetaValue"})], metadata={"msgMetaKey":"msgMetaValue"}, messageId=msg_001, taskId=task_001, extensions=[ext_flag_1, ext_flag_2], referenceTaskIds=[ref_task_001, ref_task_002], sessionId=session_demo), artifact=null, bizServiceId=biz_demo, agentId=demoagent, metadata={"demoKey":"sample","query":"想去杭州玩两天"}';

function rejects(input, pattern) {
  assert.throws(() => parseInput(input), error => error instanceof ConversionError && (!pattern || pattern.test(error.message)));
}

test('严格 JSON 保留数据、来源和原有数值校验', () => {
  for (const value of [null, true, false, 0, 1.5, '你好', ['a', 1], { a: { b: 2 } }]) {
    assert.deepEqual(parseInput(JSON.stringify(value)), { value, notes: [], source: 'json' });
  }
  rejects('9007199254740993', /安全/);
  rejects('{"n":1e400}', /有限/);
  assert.equal(parseInput('"9007199254740993"').value, '9007199254740993');
});

test('支持完整 Java 对象输出，嵌套对象、数组和业务字段不丢失', () => {
  const result = parseInput(dump);
  assert.equal(result.source, 'object-dump');
  assert.equal(result.value.userId, 123456);
  assert.equal(result.value.memory, null);
  assert.equal(result.value.artifact, null);
  assert.equal(result.value.message.role, 'USER');
  assert.deepEqual(result.value.message.extensions, ['ext_flag_1', 'ext_flag_2']);
  assert.deepEqual(result.value.message.referenceTaskIds, ['ref_task_001', 'ref_task_002']);
  assert.deepEqual(result.value.message.parts[0], {
    kind: 'text', itemId: 'item_001', subType: 'normal', content: '想去杭州玩两天', hasNext: true, full: false,
    extParams: { scene: 'chat' }, metadata: { textMetaKey: 'textMetaValue' },
  });
  assert.deepEqual(Object.keys(result.value), ['userId', 'sessionId', 'queryId', 'memory', 'message', 'artifact', 'bizServiceId', 'agentId', 'metadata']);
  assert.ok(result.notes.some(note => note.includes('包装')));
});

test('外层字段片段、混合 key:value/key=value 和裸值', () => {
  assert.deepEqual(parseInput('"name": "助手", description: "测试", version: "1.0"').value, { name: '助手', description: '测试', version: '1.0' });
  assert.equal(parseInput('name: demo').source, 'fragment');
  assert.deepEqual(parseInput('a=USER, b:中文句子, c=[foo,bar], d={nested:null}').value, { a: 'USER', b: '中文句子', c: ['foo', 'bar'], d: { nested: null } });
  assert.deepEqual(parseInput('Message()').value, {});
});

test('单引号和双引号字符串中的逗号、括号、等号不会被当作结构', () => {
  assert.deepEqual(parseInput(String.raw`text='a,b=(c)', quoted="x\"y", slash='a\\b', unicode='\u4f60\u597d'`).value,
    { text: 'a,b=(c)', quoted: 'x"y', slash: 'a\\b', unicode: '你好' });
});

test('明显的无空格 scheme:// URL 可作为裸字符串，缺逗号仍报错', () => {
  const result = parseInput('url=https://example.com/a2a?mode=test&v=1, socket=wss://example.com/stream');
  assert.deepEqual(result.value, { url: 'https://example.com/a2a?mode=test&v=1', socket: 'wss://example.com/stream' });
  rejects('url=https://example.com/a2a next:value');
  rejects('a=x b:y');
});

test('对象打印中的非层级 URI 保留为字符串，记录字段值位置', () => {
  const input = 'message=Message(role=USER, parts=[TextPart(content=你好)], extensions=[urn:example:ext]), contact=mailto:agent@example.com, phone=tel:+123456789, identifier=custom+test:item:v1';
  const result = parseInput(input, { includeFields: true });
  assert.equal(result.source, 'object-dump');
  assert.deepEqual(result.value.message.extensions, ['urn:example:ext']);
  assert.equal(result.value.contact, 'mailto:agent@example.com');
  assert.equal(result.value.phone, 'tel:+123456789');
  assert.equal(result.value.identifier, 'custom+test:item:v1');
  const extensions = result.fields.find(field => field.path.join('.') === 'message.extensions');
  assert.equal(input.slice(extensions.valueStart, extensions.valueEnd), '[urn:example:ext]');
  assert.ok(result.notes.some(note => note.includes('URI')));
  assert.ok(result.notes.every(note => !note.includes('urn:example:ext')));
});

test('非层级 URI 不吞掉缺失分隔符或未引用的空白内容', () => {
  for (const input of ['identifier=urn:example:ext next=value', 'identifier=urn:example:ext next:value', 'a=name:foo bar:bad', 'extensions=[urn:example:one urn:example:two]']) {
    rejects(input, /边界不明确/);
  }
});

test('完整代码围栏、尾逗号和完整值之后缺少的结束括号可明确修复', () => {
  const fenced = parseInput('```json\n{"a":[1,2,],}\n```');
  assert.deepEqual(fenced.value, { a: [1, 2] });
  assert.ok(fenced.notes.some(note => note.includes('围栏')));
  assert.ok(fenced.notes.some(note => note.includes('尾逗号')));
  const missing = parseInput('message=Message(parts=[TextPart(content="你好")');
  assert.deepEqual(missing.value, { message: { parts: [{ content: '你好' }] } });
  assert.ok(missing.notes.some(note => note.includes('闭合')));
});

test('截断字符串、缺值、错配括号、缺分隔符和注释必须失败', () => {
  for (const input of ['', 'a=', 'a=, b=2', '{"a":}', 'a="unfinished', "a='unfinished", '[1}', 'a=Message(x=1]', 'a=1 b=2', 'a=x b:y', '{a:1 b:2}', '[1 2]', 'a=1,,b=2', '{a:1} garbage', '// comment\na=1', '/* comment */a=1', '```json\n{"a":1}', '{"a":"\\q"}']) {
    rejects(input);
  }
});

test('冲突重复键不能覆盖；相同重复值说明合并', () => {
  for (const input of ['a=1,a=2', '{"a":1,"a":2}', 'Message(a={x:1},a={x:2})']) rejects(input, /重复/);
  const same = parseInput('a={x:1},a={x:1}');
  assert.deepEqual(same.value, { a: { x: 1 } });
  assert.ok(same.notes.some(note => note.includes('重复')));
});

test('宽松输入中的危险数字不会降级为字符串', () => {
  for (const input of ['n=9007199254740993', 'Message(n=1e400)', 'n=NaN', 'n=Infinity', 'n=-Infinity', 'n=1e', 'n=1.2.3']) rejects(input);
  assert.equal(parseInput('n="9007199254740993"').value.n, '9007199254740993');
});

test('原型相关键安全保留且修复提示不包含任何输入值', () => {
  const result = parseInput('__proto__={polluted:true},constructor={prototype:{polluted:true}},metadata={token:"not-a-real-token"}');
  assert.equal(Object.getPrototypeOf(result.value), Object.prototype);
  assert.deepEqual(Object.getOwnPropertyDescriptor(result.value, '__proto__').value, { polluted: true });
  assert.equal({}.polluted, undefined);
  assert.ok(result.notes.every(note => !note.includes('not-a-real-token')));
  assert.ok(result.notes.every(note => !note.includes('metadata')));
});

test('字段位置是可选元数据，不改变默认返回值、语义或说明', () => {
  const input = '  a={b:1}, c=[{d:2}]  ';
  const regular = parseInput(input);
  assert.deepEqual(parseInput(input, { includeFields: false }), regular);
  assert.equal(Object.hasOwn(regular, 'fields'), false);
  const { fields, ...withFields } = parseInput(input, { includeFields: true });
  assert.deepEqual(withFields, regular);
  assert.deepEqual(fields, [
    { path: ['a'], start: 2, end: 3, valueStart: 4, valueEnd: 9 },
    { path: ['a', 'b'], start: 5, end: 6, valueStart: 7, valueEnd: 8 },
    { path: ['c'], start: 11, end: 12, valueStart: 13, valueEnd: 20 },
    { path: ['c', 0, 'd'], start: 15, end: 16, valueStart: 17, valueEnd: 18 },
  ]);
  assert.deepEqual(parseInput('null', { includeFields: true }).fields, []);
});

test('递归路径区分各层同名 metadata，字符串值不产生字段位置', () => {
  const input = '{"metadata":{"id":"metadata"},"message":{"metadata":{"id":1}},"parts":[{"metadata":{"id":2}}]}';
  const { fields } = parseInput(input, { includeFields: true });
  assert.deepEqual(fields.map(field => field.path), [
    ['metadata'], ['metadata', 'id'], ['message'], ['message', 'metadata'], ['message', 'metadata', 'id'],
    ['parts'], ['parts', 0, 'metadata'], ['parts', 0, 'metadata', 'id'],
  ]);
  assert.deepEqual(fields.map(field => input.slice(field.start, field.end)), ['"metadata"', '"id"', '"message"', '"metadata"', '"id"', '"parts"', '"metadata"', '"id"']);
  assert.equal(fields.filter(field => input.slice(field.start, field.end) === '"metadata"').length, 3);
  assert.ok(fields.every((field, i) => i === 0 || field.start > fields[i - 1].end));
});

test('Java 构造器包装不进入路径，数组路径使用数字', () => {
  const input = '  message=Message(parts=[TextPart(metadata={key:value})]), metadata={key:other}';
  const { fields } = parseInput(input, { includeFields: true });
  assert.deepEqual(fields.map(field => field.path), [
    ['message'], ['message', 'parts'], ['message', 'parts', 0, 'metadata'], ['message', 'parts', 0, 'metadata', 'key'], ['metadata'], ['metadata', 'key'],
  ]);
  assert.deepEqual(fields.map(field => input.slice(field.start, field.end)), ['message', 'parts', 'metadata', 'key', 'metadata', 'key']);
});

test('转义键、单引号键和同值重复键各自记录完整源 token', () => {
  const input = String.raw`'\u0061':{'quoted\'key':1}, "a":{"quoted'key":1}`;
  const { fields, value } = parseInput(input, { includeFields: true });
  assert.deepEqual(value, { a: { "quoted'key": 1 } });
  assert.deepEqual(fields.map(field => field.path), [['a'], ['a', "quoted'key"], ['a'], ['a', "quoted'key"]]);
  assert.deepEqual(fields.map(field => input.slice(field.start, field.end)), [String.raw`'\u0061'`, String.raw`'quoted\'key'`, '"a"', '"quoted\'key"']);
});

test('原始 UTF-16 下标包含 leading 空白、代码围栏和中文 emoji 的偏移', () => {
  const prefix = ' \r\n```json\r\n  ';
  const body = '[{"中文😀":"😀", "next":{"😀":1}}]';
  const input = prefix + body + ' \r\n```\r\n ';
  const { fields } = parseInput(input, { includeFields: true });
  const tokens = ['"中文😀"', '"next"', '"😀"'];
  const starts = [prefix.length + 2, input.indexOf('"next"'), input.lastIndexOf('"😀"')];
  const valueStarts = starts.map(start => input.indexOf(':', start) + 1);
  assert.deepEqual(fields, [
    { path: [0, '中文😀'], start: starts[0], end: starts[0] + tokens[0].length, valueStart: valueStarts[0], valueEnd: valueStarts[0] + '"😀"'.length },
    { path: [0, 'next'], start: starts[1], end: starts[1] + tokens[1].length, valueStart: valueStarts[1], valueEnd: valueStarts[1] + '{"😀":1}'.length },
    { path: [0, 'next', '😀'], start: starts[2], end: starts[2] + tokens[2].length, valueStart: valueStarts[2], valueEnd: valueStarts[2] + 1 },
  ]);
  assert.deepEqual(fields.map(field => input.slice(field.start, field.end)), tokens);
  assert.deepEqual(fields.map(field => input.slice(field.valueStart, field.valueEnd)), ['"😀"', '{"😀":1}', '1']);
});

test('修复末尾括号时字段仍对应原始输入的位置', () => {
  const input = '  a=Message(items=[{key:1';
  const { fields } = parseInput(input, { includeFields: true });
  assert.deepEqual(fields.map(field => field.path), [['a'], ['a', 'items'], ['a', 'items', 0, 'key']]);
  assert.deepEqual(fields.map(field => input.slice(field.start, field.end)), ['a', 'items', 'key']);
});

test('值区间包括对象、数组和构造器，仅排除值外的首尾空白', () => {
  const input = '  a= one  , b = Message(c = 12  ), d = [null, true], text="保留尾部空格  "  ';
  const { fields } = parseInput(input, { includeFields: true });
  assert.deepEqual(fields.map(field => field.path), [['a'], ['b'], ['b', 'c'], ['d'], ['text']]);
  assert.deepEqual(fields.map(field => input.slice(field.valueStart, field.valueEnd)), [
    'one', 'Message(c = 12  )', '12', '[null, true]', '"保留尾部空格  "'
  ]);
  assert.ok(fields.every(field => field.valueStart >= field.end && field.valueEnd > field.valueStart));
});
