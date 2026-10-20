/**
 * B16 — 公共/宿主文件分类钉板（公共化边界·原地版的机器可读打包清单）。
 * ============================================================================
 * 枚举两类文件并逐文件 grep：
 *   · 公共核心 —— 断言源码**不出现**宿主字样（window/document/localStorage/
 *     __A2A_、@tc-chat/@alipay、import 任何 switch/tcChatParser）。将来公共化
 *     提取 PR 的"打包装清单"即本表的 COMMON_FILES——新增公共文件不入表即红。
 *   · 宿主装配 —— 知情登记（允许触碰宿主面）：switch（开关读取）、bootstrap
 *     （app 单例 + 装配，其纯函数提取时迁出）、tcChatParser（@tc-chat 适配器）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..', '..', '..', 'src', 'adapters', 'a2a');

/** 公共核心：提取时整体带走（hostIsolation 钉板 = 打包清单） */
const COMMON_FILES = [
  'rpc.ts', 'rpcOnce.ts', 'stream.ts', 'card.ts', 'translate.ts',
  'cancelOutcomes.ts', 'sendBan.ts', 'choiceCache.ts', 'urls.ts', 'recovery.ts',
  'A2AProviderInput.ts', 'A2AProvider.ts', 'sdkParser.ts',
  'index.ts', 'testing.ts', 'v3FrameShapes.ts',
] as const;

/** 宿主装配面：有意留在 nextgen（OCB 消费用 README 契约自带等价物） */
const HOST_FILES = ['switch.ts', 'bootstrap.ts', 'tcChatParser.ts'] as const;

/** 公共核心禁词（宿主面痕迹；comment 与 import 一视同仁——公共文件连注释都不该提宿主包名） */
const FORBIDDEN = [
  'window', 'document', 'localStorage', '__A2A_CLIENT_DEFAULT__',
  '@tc-chat', '@alipay', "'./switch'", '"./switch"', "'./tcChatParser'",
  '"./tcChatParser"', 'isA2aClientEnabled',
] as const;

// ─── 目录完整性：源码面不引入清单外文件（防"漏检新文件"）─────────────────

const COMMON = [...COMMON_FILES] as unknown as string[];

it('公共核心清单与目录实况一致：无漏检新文件（新公共文件必须入 COMMON_FILES）', () => {
  const fs = require('node:fs') as typeof import('node:fs');
  const onDisk = fs.readdirSync(SRC).filter((f) => f.endsWith('.ts'));
  const known = [...COMMON, ...HOST_FILES];
  const unlisted = onDisk.filter((f) => !known.includes(f));
  expect(unlisted).toEqual([]);
});

// ─── 公共核心逐文件：零宿主字样 ────────────────────────────────────────────

describe('hostIsolation — 公共核心零宿主字样（提取 PR 的打包清单）', () => {
  for (const file of COMMON) {
    it(`${file}：无 window/define/SDK 字样、不 import switch/tcChatParser`, () => {
      const src = readFileSync(join(SRC, file), 'utf8');
      const hits = FORBIDDEN.filter((needle) => src.includes(needle));
      expect(hits).toEqual([]);
    });
  }
});

// ─── 宿主装配面：知情登记（出现即有据，不在此做行为断言）───────────────────

it('宿主装配清单钉死（增删 HOST_FILES 须过评审：它们是 README 消费契约的组成）', () => {
  const fs = require('node:fs') as typeof import('node:fs');
  const onDisk = fs.readdirSync(SRC).filter((f) => f.endsWith('.ts'));
  for (const file of HOST_FILES) {
    expect(onDisk).toContain(file);   // 宿主文件不可无故消失/改名
  }
});