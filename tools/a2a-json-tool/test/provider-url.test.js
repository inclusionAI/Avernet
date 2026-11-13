import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { ConversionError, convertAgentCard } from '../web/converter.js';

const card = url => ({
  name: 'Provider 校验示例', description: '合成数据', version: '1.0.0',
  supportedInterfaces: [{ url: 'https://agent.example/a2a', protocolBinding: 'JSONRPC', protocolVersion: '1.0' }],
  skills: [{ id: 'echo', name: '回显', description: '返回输入', tags: ['test'] }],
  provider: { organization: 'Example', url }
});

test('AgentCard 拒绝 provider 的非 URL、缺主机及含非法原文字符的地址', () => {
  for (const url of [
    'not a URL', '/docs', 'https://', 'https:provider.example/docs',
    'https://provider.example/a b', 'https://provider.example/<docs>',
    'https://provider.example/%ZZ', 'https://provider.example/a\nb',
    'https://provider.example:99999/docs'
  ]) {
    assert.throws(() => convertAgentCard(card(url)), error =>
      error instanceof ConversionError && error.issues.some(issue => issue.includes('provider.url')), url);
  }
});

test('provider 网站或文档不套用接口限制，合法地址与片段原文保真', () => {
  for (const url of [
    'http://provider.example/docs', 'https://provider.example',
    'https://provider.example/docs?q=a%20b#quick-start',
    'HTTPS://Provider.Example:443/docs', 'http://[::1]:8080/docs#intro',
    'ftp://provider.example/docs/manual.txt'
  ]) {
    const input = card(url);
    const before = structuredClone(input);
    assert.equal(convertAgentCard(input).output.provider.url, url);
    assert.deepEqual(input, before);
  }
});

test('CLI 的非法 provider URL 非零退出且不输出卡片，合法公网 HTTP 文档成功', () => {
  const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
  const run = url => spawnSync(process.execPath, [cli, 'card'], {
    input: JSON.stringify(card(url)), encoding: 'utf8',
    env: { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR ?? '/tmp' }
  });
  const invalid = run('not a URL');
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.match(invalid.stderr, /provider\.url/);
  const valid = run('http://provider.example/docs#quick-start');
  assert.equal(valid.status, 0, valid.stderr);
  assert.equal(JSON.parse(valid.stdout).provider.url, 'http://provider.example/docs#quick-start');
});
