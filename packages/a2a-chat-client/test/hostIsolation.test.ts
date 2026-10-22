/**
 * hostIsolation —— 包边界自检钉板（packages/a2a-chat-client 版）。
 * ============================================================================
 * 公共核心的宿主无关性三面钉死（源自 nextgen 原地版钉板，随提取升级）：
 *   · 禁词 grep —— src/** 不出现 window/document/localStorage/__A2A_/
 *     具体聊天 SDK 包名；宿主三件（开关/装配/SDK 适配）不进包。
 *   · 目录完整性对账 —— src 下增删文件不入 COMMON_FILES 即红（打包清单
 *     机制延续）。
 *   · import 图闭合 + **防逃逸** —— 相对 import 不得 '../'（包内自含），
 *     经任意公共文件的静态依赖闭包只见 COMMON_FILES 成员（包外不可达——
 *     ocb 消费时"包自含"的静态保证）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..', 'src');

/** 公共核心全清单（目录完整性对账基准 + 打包清单） */
const COMMON_FILES = [
  'rpc.ts', 'rpcOnce.ts', 'stream.ts', 'card.ts', 'translate.ts',
  'cancelOutcomes.ts', 'sendBan.ts', 'choiceCache.ts', 'urls.ts', 'recovery.ts',
  'A2AProviderInput.ts', 'A2AProvider.ts', 'providerFacts.ts', 'A2aRunEngine.ts',
  'sdkParser.ts', 'policy.ts',
  'index.ts', 'testing.ts', 'v3FrameShapes.ts',
] as const;

/** 宿主面痕迹（注释与 import 一视同仁——公共文件连注释都不该提宿主包名） */
const FORBIDDEN = [
  'window', 'document', 'localStorage', '__A2A_CLIENT_DEFAULT__',
  '@tc-chat', '@alipay', 'isA2aClientEnabled',
] as const;

// ─── 目录完整性：包 src 不引入清单外文件 ──────────────────────────────────

it('包 src 清单与目录实况一致：无漏检新文件（新公共文件必须入 COMMON_FILES）', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- dts/路径探测用同款
  const onDisk = require('node:fs').readdirSync(SRC).filter((f: string) => f.endsWith('.ts'));
  const unlisted = onDisk.filter((f: string) => !(COMMON_FILES as readonly string[]).includes(f));
  expect(unlisted).toEqual([]);
});

// ─── 逐文件：零宿主字样 ────────────────────────────────────────────────────

describe('hostIsolation — 包 src 零宿主字样', () => {
  for (const file of COMMON_FILES) {
    it(`${file}：无 window/define/SDK 字样`, () => {
      const src = readFileSync(join(SRC, file), 'utf8');
      const hits = FORBIDDEN.filter((needle) => src.includes(needle));
      expect(hits).toEqual([]);
    });
  }
});

// ─── import 图闭合 + 防逃逸 ────────────────────────────────────────────────

/** 收集一个 TS 文件对同目录 './x' 相对模块的静态引用（import/re-export） */
function relativeImports(fileName: string): string[] {
  const src = readFileSync(join(SRC, fileName), 'utf8');
  const hits = [...src.matchAll(/from\s+['"]\.\/([\w-]+)['"]/g)];
  return [...new Set(hits.map((m) => `${m[1]}.ts`))];
}

it('防逃逸：公共文件的相对 import 不得越出包（无 "../" 上引、闭包只见清单成员）', () => {
  for (const file of COMMON_FILES) {
    const src = readFileSync(join(SRC, file), 'utf8');
    expect(src.includes("'../")).toBe(false);
    expect(src.includes('"../')).toBe(false);
    // BFS：沿 './x' 相对 import 展开的闭包只见 COMMON_FILES 成员
    const seen = new Set<string>([file]);
    const queue: string[] = [file];
    while (queue.length > 0) {
      const cur = queue.shift()!;
      for (const dep of relativeImports(cur)) {
        expect((COMMON_FILES as readonly string[]).includes(dep)).toBe(true);   // 未知文件即红
        if (!seen.has(dep)) {
          seen.add(dep);
          queue.push(dep);
        }
      }
    }
  }
});
