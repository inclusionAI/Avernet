/**
 * hostDir —— nextgen 宿主装配面钉板（hostIsolation 拆双的本地半）。
 * ============================================================================
 * 公共核心已提取 packages/a2a-chat-client（包边界自检在包内
 * test/hostIsolation.test.ts：禁词/清单对账/防逃逸；消费契约文档已随包
 * tests/README 迁 packages/a2a-chat-client/README.md）。本文件钉 nextgen 侧：
 *   · src/adapters/a2a 目录完整性 = 宿主三件套（公共文件/README 回流即红）；
 *   · 宿主文件不得深引包内私有路径（只许包名顶/子两级出口——防 mapper 与
 *     alias 双解析外的路径穿透，兼防将来包内文件改名悄悄断宿主）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..', '..', '..', 'src', 'adapters', 'a2a');

/** nextgen 宿主装配面知情清单（消费契约的组成部分；增删须过评审） */
const HOST_FILES = ['switch.ts', 'bootstrap.ts', 'tcChatParser.ts'] as const;

it('src/adapters/a2a 目录 = 宿主三件套（公共文件/README 回流即红）', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 目录探测
  const onDisk = require('node:fs').readdirSync(SRC);
  expect([...onDisk].sort()).toEqual([...HOST_FILES].sort());
});

it('宿主文件不深引包内私有路径（只许 @avernet/a2a-chat-client 顶/子两级出口）', () => {
  for (const file of HOST_FILES) {
    const src = readFileSync(join(SRC, file), 'utf8');
    expect(src.includes('@avernet/a2a-chat-client/src/')).toBe(false);
  }
});