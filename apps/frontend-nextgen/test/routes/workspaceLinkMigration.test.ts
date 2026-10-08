// workspace-conversation-navigation-refactor Task 9 源码级扫描门禁:
// 旧混合页路由 `/workspace?tab=chat|tab=group` 已被两条 pathname 路由取代——
// - 对话: /workspace/chat?section=managed|friend&bot=&origin=&[scope=&friend=&]session=(src/domain/conversation/route.ts)
// - 协作群: /workspace/collaboration?current=&group=&session=&membership=(src/domain/workspaceRoute.ts)
// 因此 src/config/mock/test 全量禁止再出现旧 query 形态(tab=chat / tab=group,
// 含 '/workspace?tab=...' 旧深链);邀请(/workspace/invite/...)/BCN
// (/workspace/bcn/chat/detail) 为独立路由,不在禁用范围。
import fs from 'fs';
import path from 'path';

const forbidden = ['/workspace?tab=chat', '/workspace?tab=group', 'tab=chat', 'tab=group'];
// Open Core 导出产物会剥离 mock/(scripts/oss/open-core-export.cjs):
// 缺失的扫描根目录跳过,仓库内完整目录仍全量扫描。
const candidateRoots = ['src', 'config', 'mock', 'test'];
const scanRoots = candidateRoots.filter((root) => fs.existsSync(root));
const extensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs']);
// 扫描自身包含禁用 token 的定义行,排除后避免自咬。
const selfFile = path.join('test', 'routes', 'workspaceLinkMigration.test.ts');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir)) {
    const full = path.join(dir, entry);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) walk(full, out);
    else if (extensions.has(path.extname(entry))) out.push(full);
  }
  return out;
}

describe('workspace link migration scan(Task 9)', () => {
  it('生产代码与测试不再生成/断言旧 tab=chat / tab=group 路由形态', () => {
    const offenders = scanRoots
      .flatMap((root) => walk(root))
      .filter((file) => file !== selfFile)
      .flatMap((file) =>
        fs
          .readFileSync(file, 'utf8')
          .split('\n')
          .map((line, index) => ({ file, lineNo: index + 1, line }))
          .filter(({ line }) => forbidden.some((token) => line.includes(token))),
      );
    const report = offenders.map(({ file, lineNo, line }) => `${file}:${lineNo}: ${line.trim()}`);
    expect(report).toEqual([]);
  });

  it('仓库内扫描根目录完整(mock/ 在仓内存在,导出产物中被剥离)', () => {
    // src/config/test 在仓库与导出产物中都存在,必须始终参与扫描;
    // mock/ 仅在磁盘存在时参与(mock 在 Open Core 导出产物中被剥离)。
    expect(scanRoots).toEqual(candidateRoots.filter((root) => fs.existsSync(root)));
    // 仓库内四根齐全;导出产物至少含 src/config/test 三根。
    expect(scanRoots.length).toBe(fs.existsSync('mock') ? 4 : 3);
    expect(fs.existsSync('src') && fs.existsSync('config') && fs.existsSync('test')).toBe(true);
  });
});
