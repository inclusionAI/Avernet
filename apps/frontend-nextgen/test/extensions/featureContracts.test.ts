/**
 * 扩展槽位契约夹具（collab-form Task 17）：实现可指派 +（collab）源切片 typecheck 双重防线。
 *
 * 1) 实现满足契约（type-level 断言）：真实任务栈/选路/头像编辑实现与保留消费方均对
 *    src/extensions/contracts/* 的纯结构契约注解——实现或消费的签名漂移在 `npm run typecheck`
 *    阶段直接编译失败（jest 运行态仅走 babel 剥离类型，不掩盖错误）。
 * 2) 正夹具（运行态）：对「collab 裁剪面文件物理缺席」的模块解析 host 跑真实
 *    tsc program——root 集为守卫 BFS 的全量 collab 运行时保留文件集（checkClosure 的
 *    visitedFiles）+ 纯契约文件 + src 环境声明，而非个别样例：任何保留文件未来重新引入
 *    兼容家式 type import（如 '@/hooks/useTaskExecution'）都会即时在此红灯（TS2307 类）。
 *    切片树还必须在类型解析层重放 collab 的精确匹配门面 alias（'@/extensions' → collab.ts，
 *    见 sliceCompilerOptions）——否则 tsc paths 通配会把 app.tsx 的 '@/extensions' 解到
 *    index.ts → empty.ts（Open 装配的运行时 import 面，切片中缺席）——T19 的切片导出同需此口径。
 *    skipLibCheck：切片证明只关被裁剪的自源文件图；node_modules 声明检查由整树
 *    `tsc --noEmit` 承担，不在此重复付费。
 * 3) 负夹具（运行态）：给契约植回一条 Task 3 旧式的实现派生 `import type`，
 *    断言被裁剪实现导致 tsc 必须 FAIL（module-not-found）——防止契约向实现类型回退而不被发现。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from '@jest/globals';
import type { registerExtensionSlots } from '@/extensions/slots';
import type { LazyExoticComponent } from 'react';
import type { AppExtension, AppExtensionSlots } from '@/extensions/contract';
import type { AvatarEditorComponent } from '@/extensions/contracts/avatar';
import type {
  BuildTaskInstructions,
  ComposerCapabilitiesMenuComponent,
  ComposerCapabilitiesMenuProps,
  CreateGroupViaExecute,
  GroupTaskExecutionHook,
  ManagerWorkerTasksComponent,
  TaskLinkedSessionComponent,
  UseTaskExecutionResult,
} from '@/extensions/contracts/groupTasks';
import type {
  ServiceBotRoutingControlComponent,
  ServiceBotRoutingHook,
} from '@/extensions/contracts/groupRouting';
import type { TaskPanelBootstrapHook } from '@/extensions/contracts/taskPanelBootstrap';

// —— 1) 实现满足契约（type-level；由 npm run typecheck 强制）——

import type { ComposerCapabilitiesMenu as ComposerMenuImpl } from '@/components/Workspace/TaskComposerMenu';
import type AvatarEditorDefaultImpl from '@/components/BotWorkshop/BotAvatar/AvatarEditor';
import type { ManagerWorkerTasks as ManagerWorkerTasksImpl } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import type { ServiceBotRoutingControl as ServiceBotRoutingControlImpl } from '@/pages/Workspace/components/Modals/ServiceBotRoutingControl';
import type { TaskLinkedSession as TaskLinkedSessionImpl } from '@/components/Workspace/TaskLinkedSession';
import type { buildTaskInstructions as buildTaskInstructionsImpl } from '@/services/tasks/taskMapper';
import type { createGroupViaExecute as createGroupViaExecuteImpl } from '@/services/workspace/groupExecuteService';
import type { useGroupTaskExecution as useGroupTaskExecutionImpl } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import type { useServiceBotRouting as useServiceBotRoutingImpl } from '@/pages/Workspace/hooks/useServiceBotRouting';
import type { useSessionTaskPanelBootstrap as useSessionTaskPanelBootstrapImpl } from '@/hooks/useSessionTaskPanelBootstrap';

/** 实现必须满足契约（Impl extends Contract）；失配即 typecheck 报错并定位到具名断言。 */
type SatisfiesContract<Contract, Impl extends Contract> = Impl;

/**
 * 编译期断言元组（由 npm run typecheck 强制；jest 运行态不产生任务栈/选路实现的值加载）：
 * 1-10 槽位载荷：真实实现/消费方对纯契约的指派性——签名漂移使对应元素失配，报错定位到该行；
 * 11 编排结果 = composer 菜单 props.execution 字段类型（GroupChatComposer 传的就是槽位 hook 返回）；
 * 12 slots.ts 注册参数即 AppExtensionSlots 装配口径（三形态 null 语义）；
 * 13 三形态装配以 AppExtension 公共名持有 avatarEditor（React.lazy 载荷签名）。
 */
type FeatureContractAssertions = [
  SatisfiesContract<AvatarEditorComponent, typeof AvatarEditorDefaultImpl>,
  SatisfiesContract<ComposerCapabilitiesMenuComponent, typeof ComposerMenuImpl>,
  SatisfiesContract<TaskLinkedSessionComponent, typeof TaskLinkedSessionImpl>,
  SatisfiesContract<ManagerWorkerTasksComponent, typeof ManagerWorkerTasksImpl>,
  SatisfiesContract<BuildTaskInstructions, typeof buildTaskInstructionsImpl>,
  SatisfiesContract<CreateGroupViaExecute, typeof createGroupViaExecuteImpl>,
  SatisfiesContract<GroupTaskExecutionHook, typeof useGroupTaskExecutionImpl>,
  SatisfiesContract<ServiceBotRoutingHook, typeof useServiceBotRoutingImpl>,
  SatisfiesContract<ServiceBotRoutingControlComponent, typeof ServiceBotRoutingControlImpl>,
  SatisfiesContract<TaskPanelBootstrapHook, typeof useSessionTaskPanelBootstrapImpl>,
  SatisfiesContract<ComposerCapabilitiesMenuProps['execution'], UseTaskExecutionResult | undefined>,
  SatisfiesContract<AppExtensionSlots, Parameters<typeof registerExtensionSlots>[0]>,
  SatisfiesContract<AppExtension['avatarEditor'], LazyExoticComponent<AvatarEditorComponent> | null>,
];

// 值引用驱动上面的元组实例化（约束检查发生在 typecheck）；运行时恒为 null，由下方用例钉住。
const featureContractAssertions: FeatureContractAssertions | null = null;

// —— 2)/3) 源切片夹具：对 collab 裁剪面“物理缺席”的解析 host 跑真实 tsc ——
// 与运行时守卫共用同一 FORBIDDEN/ALLOW 清单（scripts/check-collab-closure.cjs 单一来源）。

const appRoot = path.resolve(__dirname, '../..');
const requireCjs = createRequire(__filename);
const { FORBIDDEN, ALLOW, checkClosure } = requireCjs('../../scripts/check-collab-closure.cjs') as {
  FORBIDDEN: string[];
  ALLOW: string[];
  checkClosure: (root: string) => {
    entryCount: number;
    visitedCount: number;
    visitedFiles: string[];
    failures: string[];
  };
};

function isOmitted(relSlash: string): boolean {
  return (
    FORBIDDEN.some((prefix) => relSlash.startsWith(prefix)) &&
    !ALLOW.some((prefix) => relSlash.startsWith(prefix))
  );
}

/**
 * collab 运行时保留文件集（守卫 BFS 的 visitedFiles 映回绝对路径）。守卫必须先绿——
 * 运行时闭包有违例时谈切片类型完备没有意义，直接 fail-fast 暴露守卫违例链。
 */
function collabClosureFiles(): string[] {
  const guard = checkClosure(appRoot);
  if (guard.failures.length > 0) {
    throw new Error(`collab closure guard must be green to derive the slice file set:\n${guard.failures.join('\n')}`);
  }
  return guard.visitedFiles.map((rel) => path.join(appRoot, rel));
}

function sliceCompilerOptions(): ts.CompilerOptions {
  const configFile = ts.readConfigFile(path.join(appRoot, 'tsconfig.json'), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(configFile.config, ts.sys, appRoot);
  if (parsed.errors.length) {
    throw new Error(parsed.errors.map((e) => ts.flattenDiagnosticMessageText(e.messageText, '\n')).join('\n'));
  }
  return {
    ...parsed.options,
    noEmit: true,
    skipLibCheck: true,
    incremental: false,
    composite: false,
    tsBuildInfoFile: undefined,
    // 重放 collab 形态的精确匹配门面 alias（config/config.ts 的 '@/extensions$' → collab）：
    // 与运行时守卫 resolve() 同一口径。tsc paths 的通配 '@/ *' 会走 index.ts → empty.ts（Open
    // 装配，运行时 import 全部任务栈实现）——整树 typecheck 无碍，但切片树里 empty.ts 正属
    // 裁剪面（其 import 目标被本 host 屏蔽），故门面 alias 必须在类型解析层同样重放。
    // 精确键优先于通配键，'@/extensions/slots' 等子路径不受影响。
    paths: {
      ...parsed.options.paths,
      '@/extensions': ['./src/extensions/collab.ts'],
    },
  };
}

/**
 * 裁剪版模块解析 host：collab 省略面（守卫 FORBIDDEN − ALLOW）文件不存在；
 * virtual 用于负夹具覆写真实文件内容（不落盘）。
 */
function makeSlicingHost(options: ts.CompilerOptions, virtual: Map<string, string>): ts.CompilerHost {
  const base = ts.createCompilerHost(options);
  const blocked = (fileName: string): boolean => {
    const rel = path.relative(appRoot, fileName).split(path.sep).join('/');
    return isOmitted(rel);
  };
  const vanillaReadFile = base.readFile.bind(base);
  const vanillaFileExists = base.fileExists.bind(base);
  base.readFile = (fileName) => {
    if (virtual.has(fileName)) return virtual.get(fileName);
    if (blocked(fileName)) return undefined;
    return vanillaReadFile(fileName);
  };
  base.fileExists = (fileName) => {
    if (virtual.has(fileName)) return true;
    if (blocked(fileName)) return false;
    return vanillaFileExists(fileName);
  };
  return base;
}

const contractFile = path.join(appRoot, 'src/extensions/contract.ts');
const contractsDir = path.join(appRoot, 'src/extensions/contracts');

/**
 * 环境声明根导：src 下的 .d.ts（含子目录）不经模块 import 边可达（构建期 define 注入常量、
 * 位图与 chardet 的模块声明），但真实 `tsc --noEmit`（tsconfig include 全量 src）与源切片导出
 * 都必须整套携带的环境面。固定装置按同一口径把 src 下全部 .d.ts 纳入 roots（.umi 生成物
 * 除外，tsconfig 已 exclude），保持夹具与整树的 typecheck 口径一致。
 */
function ambientDeclarationRoots(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === '.umi' || entry.name === '.umi-production') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && entry.name.endsWith('.d.ts')) out.push(full);
    }
  };
  walk(path.join(appRoot, 'src'));
  return out;
}

function programErrorDiagnostics(virtual: Map<string, string>, roots: string[]): ts.Diagnostic[] {
  const options = sliceCompilerOptions();
  const program = ts.createProgram(roots, options, makeSlicingHost(options, virtual));
  return ts.getPreEmitDiagnostics(program).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  );
}

// 全量保留文件集 + 纯契约 + 环境声明：切片导出的正向 typecheck root 集。
const sliceRoots = [
  ...collabClosureFiles(),
  ...fs.readdirSync(contractsDir).map((name) => path.join(contractsDir, name)),
  contractFile,
  ...ambientDeclarationRoots(),
];

// 负夹具 root 集：只正对契约文件本身（回退注入位于 contract.ts，无需重复全量 509 文件程序）。
const contractOnlyRoots = [
  ...fs.readdirSync(contractsDir).map((name) => path.join(contractsDir, name)),
  contractFile,
  ...ambientDeclarationRoots(),
];

describe('扩展槽位实现满足纯契约（type-level 断言元组）', () => {
  it('断言槽位保持 null（断言约束本体经 npm run typecheck 在编译期强制）', () => {
    expect(featureContractAssertions).toBeNull();
  });
});

describe('collab 源切片夹具（对裁剪面文件物理缺席 typecheck）', () => {
  it('全量 collab 保留文件集 + 契约在任务栈/选路/头像实现缺席时零错误', () => {
    const diagnostics = programErrorDiagnostics(new Map(), sliceRoots);
    expect(
      diagnostics.map((d) => `${d.code} ${ts.flattenDiagnosticMessageText(d.messageText, '\n')}`),
    ).toEqual([]);
  }, 120_000);

  it('负夹具：契约植回实现派生 import type 后必须因缺失实现而 FAIL', () => {
    const revertedContract = [
      fs.readFileSync(contractFile, 'utf8'),
      '',
      '// 负样本：Task 3 旧式的实现派生 import type（契约层禁止回退到该形态）。',
      "import type { TaskLinkedSession as RevertedLinkedSession } from '@/components/Workspace/TaskLinkedSession';",
      'export interface __RevertedSlotShape {',
      '  component: typeof RevertedLinkedSession;',
      '}',
      '',
    ].join('\n');
    const diagnostics = programErrorDiagnostics(new Map([[contractFile, revertedContract]]), contractOnlyRoots);
    const moduleNotFound = diagnostics.filter(
      (d) => d.code === 2307 && ts.flattenDiagnosticMessageText(d.messageText, '\n').includes('components/Workspace/TaskLinkedSession'),
    );
    expect(moduleNotFound.length).toBeGreaterThan(0);
  }, 120_000);
});
