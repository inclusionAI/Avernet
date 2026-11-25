import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
const run = (args, input = '') => spawnSync(process.execPath, [cli, ...args], { input, encoding: 'utf8' });

for (const args of [['--help'], ['-h'], ['message', '--help'], ['card', '-h']]) {
  test(`帮助 ${args.join(' ')} 在标准输入保持打开时正常退出`, async () => {
    const child = spawn(process.execPath, [cli, ...args], { timeout: 5000 });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    const [code, signal] = await once(child, 'close');
    assert.equal(signal, null, '帮助不应等待读取标准输入');
    assert.equal(code, 0, stderr);
    assert.match(stdout, /^用法：a2a card\|message /);
    assert.doesNotMatch(stdout, /a2a-json/);
    assert.match(stdout, /card/);
    assert.match(stdout, /message/);
    assert.equal(stderr, '');
  });
}

test('命令行从标准输入转换消息，标准输出可直接作为 JSON 使用', () => {
  const input = { question: '你好', values: [1, false, null] };
  const result = run(['message'], JSON.stringify(input));
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.method, 'SendMessage');
  assert.equal(output.params.message.role, 'ROLE_USER');
  assert.deepEqual(output.params.message.parts[0].data, input);
});

test('命令行支持含空格的文件路径，补全说明不污染标准输出', () => {
  const directory = mkdtempSync(join(tmpdir(), 'a2a-cli-'));
  try {
    const file = join(directory, 'agent card.json');
    writeFileSync(file, JSON.stringify({
      name: '测试 Agent', description: '测试转换', version: '1.0.0',
      url: 'http://localhost:8080/a2a',
      skills: [{ id: 'echo', name: '回显', description: '回显 JSON', tags: ['json'] }]
    }));
    const result = run(['card', file]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).supportedInterfaces[0].protocolVersion, '1.0');
    assert.ok(result.stderr.length > 0);
  } finally {
    rmSync(directory, { recursive: true });
  }
});

test('命令行同样接受对象打印片段，映射文本且说明不包含原始值', () => {
  const result = run(['message'], 'agentId=demo_agent, message=Message(role=USER, parts=[TextPart(kind=text, content=片段转换测试)], messageId=msg_demo), metadata={"demoValue":"private-fixture-only"}');
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.params.message.parts[0].text, '片段转换测试');
  assert.equal(output.params.message.messageId, 'msg_demo');
  assert.equal(output.params.metadata.sourceEnvelope.agentId, 'demo_agent');
  assert.ok(result.stderr.length > 0);
  assert.equal(result.stderr.includes('private-fixture-only'), false);
});

test('CLI 不将含非法字符的 URI 声明为协议扩展或文件地址', () => {
  const dumped = run(['message'], 'message=Message(role=USER, parts=[TextPart(content=你好)], extensions=[urn:example:<ext>])');
  assert.equal(dumped.status, 0, dumped.stderr);
  const message = JSON.parse(dumped.stdout).params.message;
  assert.equal(Object.hasOwn(message, 'extensions'), false);
  assert.deepEqual(message.metadata.sourceFields.extensions, ['urn:example:<ext>']);
  const file = run(['message'], JSON.stringify({ role: 'USER', parts: [{ url: 'urn:example:<ext>' }] }));
  assert.notEqual(file.status, 0);
  assert.equal(file.stdout, '');
  assert.match(file.stderr, /parts\[0\]\.url/);
  const encoded = run(['message'], 'message=Message(role=USER, parts=[TextPart(content=你好)], extensions=[urn:example:%3Cext%3E])');
  assert.equal(encoded.status, 0, encoded.stderr);
  assert.deepEqual(JSON.parse(encoded.stdout).params.message.extensions, ['urn:example:%3Cext%3E']);
});

test('CLI 将消息 content 转为 data，parts 优先且非法 parts 不回退', () => {
  for (const wrap of [message => message, message => ({ message })]) {
    const contentOnly = run(['message'], JSON.stringify(wrap({ role: 'user', content: 'hello' })));
    assert.equal(contentOnly.status, 0, contentOnly.stderr);
    const message = JSON.parse(contentOnly.stdout).params.message;
    assert.equal(message.role, 'ROLE_USER');
    assert.deepEqual(message.parts, [{ data: 'hello', mediaType: 'application/json' }]);

    const parts = [{ text: '优先使用 parts' }];
    const content = { preserved: [null, false, 'hello'] };
    const both = run(['message'], JSON.stringify(wrap({ role: 'USER', parts, content })));
    assert.equal(both.status, 0, both.stderr);
    const preferred = JSON.parse(both.stdout).params.message;
    assert.deepEqual(preferred.parts, parts);
    assert.deepEqual(preferred.metadata.sourceFields.content, content);

    for (const invalidParts of [[], null, [{ text: '不能丢弃', data: {} }]]) {
      const invalid = run(['message'], JSON.stringify(wrap({ role: 'USER', parts: invalidParts, content: '不可用于回退' })));
      assert.notEqual(invalid.status, 0);
      assert.equal(invalid.stdout, '');
      assert.match(invalid.stderr, /parts/);
    }
  }
});

test('错误输入、缺失文件与错误用法均非零退出且不输出伪成功结果', () => {
  for (const [args, input] of [
    [['message'], '{bad'],
    [['card', '-'], '{}'],
    [['message', '/does/not/exist/a2a.json'], ''],
    [['wrong'], '{}'],
    [['message', '--unknown'], '{}'],
    [['message', '-', 'extra'], '{}']
  ]) {
    const result = run(args, input);
    assert.notEqual(result.status, 0);
    assert.equal(result.stdout, '');
    assert.ok(result.stderr.length > 0);
  }
});
