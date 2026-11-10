/**
 * 部署镜像双入口对齐（PR review D1）。
 *
 * 仓库有两份前端镜像定义：apps/frontend-nextgen/Dockerfile（本 app 侧）与
 * docker/services/frontend-nextgen.dockerfile（编排侧）。两者必须安装同一个入口
 * 脚本名，并把 bcs-only profile 模板 COPY 到 entrypoint 的 cp 源路径；否则
 * bcs-only 模式在 /docker-entrypoint.d 阶段 cp 失败，set -e 直接退出容器。
 */
import { readFileSync } from 'node:fs';
import { basename, join } from 'node:path';

const appDockerfile = join(process.cwd(), 'Dockerfile');
const servicesDockerfile = join(process.cwd(), '../../docker/services/frontend-nextgen.dockerfile');
const entrypointPath = join(process.cwd(), 'deploy/docker-entrypoint.sh');

const ENTRYPOINT_INSTALL_NAME = '10-teamclaw-api-profile.sh';
const ENTRYPOINT_INSTALL_DIR = '/docker-entrypoint.d/';
const TEMPLATE_PROFILES_PREFIX = '/etc/nginx/template-profiles/';

interface CopyInstruction {
  source: string;
  destination: string;
}

function copyInstructions(dockerfilePath: string): CopyInstruction[] {
  return readFileSync(dockerfilePath, 'utf8')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('COPY '))
    .map((line) => line.split(/\s+/).filter((token) => !token.startsWith('--')))
    .map((tokens) => ({
      source: tokens[tokens.length - 2] ?? '',
      destination: tokens[tokens.length - 1] ?? '',
    }));
}

/** entrypoint 里 `cp <源> <envsubst 模板位>` 的源路径（bcs-only 分支）。 */
function bcsOnlyTemplateCpSource(): string {
  const entrypoint = readFileSync(entrypointPath, 'utf8');
  const match = entrypoint.match(/^\s*cp\s+(\S+)\s+/m);
  if (!match) throw new Error('docker-entrypoint.sh 缺少 bcs-only profile 模板的 cp 指令');

  // 允许 `cp <字面量>` 或 `cp "$var"`（变量在同一脚本内赋值）两种写法。
  const source = match[1].replace(/^["']|["']$/g, '');
  const reference = source.match(/^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$/);
  if (!reference) return source;

  const assignment = entrypoint.match(new RegExp(`^\\s*${reference[1]}=(\\S+)$`, 'm'));
  if (!assignment) throw new Error(`docker-entrypoint.sh 未定义 cp 源变量: ${reference[1]}`);
  return assignment[1];
}

describe.each([
  ['apps/frontend-nextgen/Dockerfile', appDockerfile],
  ['docker/services/frontend-nextgen.dockerfile', servicesDockerfile],
])('%s', (_label, dockerfilePath) => {
  const copies = copyInstructions(dockerfilePath);

  test('installs the profile entrypoint under the 10-* name that runs before envsubst', () => {
    const entrypointCopies = copies.filter((copy) => copy.destination.startsWith(ENTRYPOINT_INSTALL_DIR));

    expect(entrypointCopies).toHaveLength(1);
    expect(basename(entrypointCopies[0].destination)).toBe(ENTRYPOINT_INSTALL_NAME);
    // 源必须就是本 app 的 entrypoint（app 侧与编排侧同源，不各写一份）。
    expect(entrypointCopies[0].source).toBe('apps/frontend-nextgen/deploy/docker-entrypoint.sh');
  });

  test('copies the bcs-only profile template to the cp source used by the entrypoint', () => {
    const cpSource = bcsOnlyTemplateCpSource();
    expect(cpSource.startsWith(TEMPLATE_PROFILES_PREFIX)).toBe(true);

    const profileCopies = copies.filter((copy) => copy.destination === cpSource);

    expect(profileCopies).toHaveLength(1);
    expect(profileCopies[0].source).toBe(
      `apps/frontend-nextgen/deploy/nginx/${basename(cpSource)}`,
    );
  });

  test('keeps the full template at the envsubst templates destination', () => {
    const fullTemplateCopies = copies.filter(
      (copy) => copy.destination === '/etc/nginx/templates/default.conf.template',
    );

    expect(fullTemplateCopies).toHaveLength(1);
    expect(fullTemplateCopies[0].source).toBe('apps/frontend-nextgen/deploy/nginx/default.conf.template');
  });
});