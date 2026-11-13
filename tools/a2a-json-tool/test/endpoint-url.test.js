import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { ConversionError, convertAgentCard } from '../web/converter.js';

const card = url => ({
  name: '接口校验示例', description: '合成输入', version: '1.0.0', url,
  skills: [{ id: 'echo', name: '回显', description: '返回输入', tags: ['test'] }]
});
const fullCard = url => {
  const input = card(url);
  delete input.url;
  return { ...input, supportedInterfaces: [{ url, protocolBinding: 'JSONRPC', protocolVersion: '1.0' }] };
};

test('简化和完整 AgentCard 均拒绝需要修复非法原文的接口 URL', () => {
  for (const url of [
    'https://agent.example/a b', 'https://agent.example/a\tb', 'https://agent.example/a\nb',
    'https://agent.example/a\\b', 'https://agent.example/<a>', 'https://agent.example/a|b',
    'https://agent.example/[a]', 'https://agent.example/%', 'https://agent.example/%2',
    'https://agent.example/%ZZ', 'https:///agent.example/a2a'
  ]) {
    for (const makeCard of [card, fullCard]) {
      assert.throws(() => convertAgentCard(makeCard(url)), error =>
        error instanceof ConversionError && error.issues.some(issue => issue.includes('supportedInterfaces[0].url')), url);
    }
  }
});

test('合法编码、IPv6、大小写与默认端口等合法地址保持原文', () => {
  for (const url of [
    'https://agent.example/a%20b?list=%5B1%5D', 'HTTPS://Agent.Example:443',
    'https://[2001:db8::1]:443/a2a', 'https://agent.example/a/../a2a',
    'http://localhost:8080/a2a', 'http://127.0.0.1:8080/a2a', 'http://[::1]:8080/a2a'
  ]) {
    for (const makeCard of [card, fullCard]) {
      const input = makeCard(url);
      const before = structuredClone(input);
      assert.equal(convertAgentCard(input).output.supportedInterfaces[0].url, url);
      assert.deepEqual(input, before);
    }
  }
  for (const url of ['grpc.example:443', '[::1]:50051']) {
    assert.equal(convertAgentCard({ ...card(url), protocolBinding: 'GRPC' }).output.supportedInterfaces[0].url, url);
  }
});

test('CLI 拒绝接口空格地址且 stdout 为空，合法编码地址转换成功', () => {
  const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
  const run = url => spawnSync(process.execPath, [cli, 'card'], {
    input: JSON.stringify(card(url)), encoding: 'utf8',
    env: { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR ?? '/tmp' }
  });
  const invalid = run('https://agent.example/a b');
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.match(invalid.stderr, /supportedInterfaces\[0\]\.url/);
  const valid = run('https://agent.example/a%20b');
  assert.equal(valid.status, 0, valid.stderr);
  assert.equal(JSON.parse(valid.stdout).supportedInterfaces[0].url, 'https://agent.example/a%20b');
});
