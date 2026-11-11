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
    assert.match(stdout, /a2a-json/);
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
