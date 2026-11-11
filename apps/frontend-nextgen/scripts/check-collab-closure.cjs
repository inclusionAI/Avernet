#!/usr/bin/env node
// Runtime source closure: static imports/re-exports, import() and require().
// Type-only edges are erased. Missing local code and opaque bundled loads fail.
// Note (Task 17): extractLocalEdges also reports issues for nonliteral TYPE-only edges,
// which the Task 14 runtime-only parse skipped entirely — the source-slice view must
// prove every local file it needs, including erased ones.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const FORBIDDEN = [
  'src/pages/BotWorkshop/',
  'src/pages/Lab',
  'src/pages/EvolutionFactory/',
  'src/pages/CollaborationSquare/',
  'src/components/CollaborationSquare/SquarePageShell/',
  'src/components/CollaborationSquare/PublicTaskCatalogPanel/',
  'src/hooks/useCollaborationSquare.ts',
  'src/hooks/useCollaborationSquareTask',
  'src/services/collaborationSquare/collaborationSquareService.ts',
  'src/services/collaborationSquare/collaborationSquareApiAdapter.ts',
  'src/services/collaborationSquare/mockCollaborationSquareAdapter',
  'src/services/backendApi/collaboration/bbsTaskController',
  'src/pages/Admin/',
  'src/pages/SpaceAdmin/',
  'src/pages/TicketCenter/',
  'src/pages/Welcome/',
  'src/pages/MyTask/',
  'src/pages/ScheduledTasks/',
  'src/pages/Demo/',
  'src/pages/ModuleOverview/',
  'src/pages/ComponentExamples/',
  'src/components/BotWorkshop/',
  'src/components/TaskCards/',
  'src/components/Workspace/TaskComposerMenu/',
  'src/components/Workspace/TaskLinkedSession',
  'src/components/Workspace/TaskPanelSwitcherSlot',
  'src/services/botWorkshop/',
  'src/services/botHealthCheck/',
  'src/services/engine/',
  'src/services/workspace/groupExecuteService',
  'src/services/workspace/groupTaskAdapter',
  'src/services/backendApi/tasks/',
  'src/services/backendApi/workflows/',
  'src/services/backendApi/bots/botClassificationController',
  'src/services/workspace/botClassificationService',
  'src/assets/TaskPanel/',
  'src/hooks/useTaskExecution',
  'src/hooks/useTaskExecuteFromCard',
  'src/hooks/useWorkflowList',
  'src/hooks/useTaskSwitcherTasks',
  'src/hooks/useSessionTaskPanelBootstrap',
  'src/hooks/taskPanelNavBinding',
  'src/pages/Workspace/hooks/useGroupTaskExecution',
  'src/pages/Workspace/hooks/useManagerWorkerTasks',
  'src/pages/Workspace/hooks/useServiceBotRouting',
  'src/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks',
  'src/pages/Workspace/components/GroupChatPane/GroupTaskRow',
  'src/pages/Workspace/components/Modals/ServiceBotRoutingControl',
  // 单聊整树禁入；唯二正向复用文件经 ALLOW 豁免（spec acceptance 1 的机器可证口径）。
  'src/pages/Workspace/Chat/',
];

/** 正向复用白名单：禁入前缀下的豁免（其余 pages/Workspace/Chat 一律违例）。新增豁免 = 扩 spec，不许随手加。 */
const ALLOW = [
  // 2026-11-11 collab-discovery：仅这两个页面入口获准，专家/任务页面仍禁入。
  'src/pages/CollaborationSquare/Bots/index.tsx',
  'src/pages/CollaborationSquare/Groups/index.tsx',
  'src/pages/Workspace/Chat/components/SessionHistorySearchPanel',
  'src/pages/Workspace/Chat/hooks/sidePaneContent',
];


const ASSET = /\.(?:css|less|scss|sass|svg|png|jpe?g|gif|webp|avif|woff2?|ttf|ico|json|wasm)$/i;

function runtimeImport(node) {
  const clause = node.importClause;
  if (!clause) return true; // Side-effect import.
  if (clause.isTypeOnly) return false;
  const bindings = clause.namedBindings;
  return Boolean(clause.name) || !bindings || ts.isNamespaceImport(bindings)
    || bindings.elements.length === 0 || bindings.elements.some((item) => !item.isTypeOnly);
}

/**
 * 源切片边分类（collab-form Task 17）：
 * - runtime：打包生效的值/副作用边（import/export-from、import()、require）。
 * - type：仅类型边（import type / export type / 内联 type 修饰）——运行时擦除，
 *   但导出源切片必须携带目标文件，漏文件则被裁剪树无法 typecheck。
 * - asset：静态资源边（切片以原文件形式携带）。
 * 运行时守卫与源切片清单共用同一解析口径：本文件零依赖纯逻辑，两类消费方不得各自另写规则。
 */
function edgeKind(specifier, isRuntimeSyntax) {
  if (!isRuntimeSyntax) return 'type';
  return ASSET.test(specifier) ? 'asset' : 'runtime';
}

/** 解析单个文件的全部本地依赖边（含 runtime/type/asset 三类），供运行时守卫与源切片共用。 */
function extractLocalEdges(file, source) {
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const edges = [];
  const seen = new Set();
  const issues = [];
  const literal = (node, context, isRuntimeSyntax) => {
    if (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))) {
      const kind = edgeKind(node.text, isRuntimeSyntax);
      const key = `${kind}\u0000${node.text}`;
      if (!seen.has(key)) {
        seen.add(key);
        edges.push({ specifier: node.text, kind });
      }
    } else {
      issues.push(`${file}: nonliteral ${context} cannot prove bundled closure`);
    }
  };
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      literal(node.moduleSpecifier, 'import', runtimeImport(node));
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier) {
      const clause = node.exportClause;
      const isRuntimeSyntax = !node.isTypeOnly && (!clause || !ts.isNamedExports(clause)
        || clause.elements.length === 0 || clause.elements.some((item) => !item.isTypeOnly));
      literal(node.moduleSpecifier, 're-export', isRuntimeSyntax);
    } else if (ts.isCallExpression(node)) {
      const dynamic = node.expression.kind === ts.SyntaxKind.ImportKeyword;
      const requireCall = ts.isIdentifier(node.expression) && node.expression.text === 'require';
      if (dynamic || requireCall) {
        // Detect webpackIgnore only in the argument's comment trivia.
        // Runtime URL loads need a reviewed exception; do not silently skip them.
        const trivia = node.arguments[0]
          ? source.slice(node.arguments[0].pos, node.arguments[0].getStart(ast)) : '';
        if (dynamic && /webpackIgnore\s*:\s*true/.test(trivia)) {
          issues.push(`${file}: webpackIgnore runtime import requires an explicit reviewed exception`);
        } else {
          literal(node.arguments[0], dynamic ? 'import()' : 'require()', true);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return { edges, issues };
}

/** 运行时闭包视图：type 边擦除（同 Task 14 语义），asset 边保持入队后由 resolve() 丢弃。 */
function extractEdges(file, source) {
  const parsed = extractLocalEdges(file, source);
  return {
    edges: parsed.edges.filter((edge) => edge.kind !== 'type').map((edge) => edge.specifier),
    issues: parsed.issues,
  };
}

function checkClosure(root) {
  const src = path.join(root, 'src');
  const configFile = ts.readConfigFile(path.join(root, 'tsconfig.json'), ts.sys.readFile);
  if (configFile.error) throw new Error(ts.flattenDiagnosticMessageText(configFile.error.messageText, '\n'));
  const config = ts.parseJsonConfigFileContent(configFile.config, ts.sys, root);
  if (config.errors.length) throw new Error(config.errors.map((e) => ts.flattenDiagnosticMessageText(e.messageText, '\n')).join('\n'));
  const failures = [];
  const visited = new Set();
  const relative = (file) => path.relative(root, file).split(path.sep).join('/');

  function resolve(specifier, from) {
    if (ASSET.test(specifier)) return null;
    // Umi-generated runtime is supplied by the build; not application source.
    if (specifier.startsWith('@@/')) return null;
    // Replay the exact facade alias; other aliases use tsconfig paths, including
    // @avernet/a2a-chat-client workspace source. Never redirect slots/contract.
    const spec = specifier === '@/extensions'
      ? path.join(src, 'extensions/collab.ts') : specifier;
    const resolved = ts.resolveModuleName(spec, from, config.options, ts.sys).resolvedModule;
    if (!resolved) throw new Error(`unresolved code import ${specifier} from ${relative(from)}`);
    if (/\.d\.(?:ts|mts|cts)$/.test(resolved.resolvedFileName)) return null;
    if (resolved.isExternalLibraryImport && resolved.resolvedFileName.includes('/node_modules/')) return null;
    return path.resolve(resolved.resolvedFileName);
  }

  const routesFile = path.join(root, 'config/routes.collab.ts');
  const routesAst = ts.createSourceFile(routesFile, fs.readFileSync(routesFile, 'utf8'), ts.ScriptTarget.Latest, true);
  const entries = ['@/layouts/AppLayout', '@/app'];
  function visitRoute(node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(routesAst).replace(/['"]/g, '') === 'component') {
      if (!ts.isStringLiteral(node.initializer)) throw new Error('Route component must be a literal source entry');
      entries.push(node.initializer.text);
    }
    ts.forEachChild(node, visitRoute);
  }
  visitRoute(routesAst);
  if (entries.length === 2) throw new Error('No route component entries found');
  const queue = entries.map((spec) => ({ spec, from: routesFile, chain: [] }));
  while (queue.length) {
    const { spec, from, chain } = queue.shift();
    let file;
    try { file = resolve(spec, from); }
    catch (error) { failures.push(`${chain.join(' -> ')}: ${error.message}`); continue; }
    if (!file || visited.has(file)) continue;
    visited.add(file);
    const rel = relative(file);
    const next = [...chain, rel];
    if (FORBIDDEN.some((prefix) => rel.startsWith(prefix)) && !ALLOW.some((prefix) => rel.startsWith(prefix))) {
      failures.push(next.join(' -> '));
      continue;
    }
    const parsed = extractEdges(file, fs.readFileSync(file, 'utf8'));
    failures.push(...parsed.issues.map((issue) => `${next.join(' -> ')}: ${issue}`));
    for (const edge of parsed.edges) queue.push({ spec: edge, from: file, chain: next });
  }
  // visitedFiles：collab 运行时闭包的完整保留文件集（仓相对路径，排序稳定）——供源切片固定装置
  // （test/extensions/featureContracts.test.ts）作正向 typecheck 的 root 集，运行时守卫与源切片
  // 共用同一 BFS。CLI 输出不变（只打印计数）；本字段仅程序化消费。
  return {
    entryCount: entries.length,
    visitedCount: visited.size,
    visitedFiles: [...visited].map((file) => relative(file)).sort(),
    failures,
  };
}

// FORBIDDEN / ALLOW 一并导出：源切片固定装置（test/extensions/featureContracts.test.ts）复用同一
// 「collab 裁剪面」判定口径，避免守卫与切片各持一份清单漂移。新增豁免 = 扩 spec，不许随手加。
module.exports = { checkClosure, extractEdges, extractLocalEdges, FORBIDDEN, ALLOW };
if (require.main === module) {
  try {
    const result = checkClosure(path.resolve(__dirname, '..'));
    console.log(`[collab-closure] ${result.entryCount} entries, ${result.visitedCount} files, ${result.failures.length} violations`);
    for (const failure of result.failures) console.error(`  ${failure}`);
    if (result.failures.length) process.exitCode = 1;
  } catch (error) {
    console.error(`[collab-closure] FAIL: ${error.message}`);
    process.exitCode = 1;
  }
}
