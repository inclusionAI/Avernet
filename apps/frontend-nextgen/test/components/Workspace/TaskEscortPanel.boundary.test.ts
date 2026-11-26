import fs from 'fs';
import path from 'path';
import ts from 'typescript';

const sourceRoot = path.resolve(__dirname, '../../../src');
function resolveSource(from: string, specifier: string) {
  const base = specifier.startsWith('@/')
    ? path.join(sourceRoot, specifier.slice(2))
    : specifier.startsWith('.') ? path.resolve(path.dirname(from), specifier) : null;
  if (!base) return null;
  return [base, `${base}.ts`, `${base}.tsx`, path.join(base, 'index.ts'), path.join(base, 'index.tsx')]
    .find((candidate) => /\.(ts|tsx)$/.test(candidate) && fs.existsSync(candidate)) ?? null;
}

it('keeps every demo fixture/component out of the live drawer and chat-header runtime import graph', () => {
  const pending = [
    path.join(sourceRoot, 'components/Workspace/TaskEscortPanel/index.tsx'),
    path.join(sourceRoot, 'pages/Workspace/Chat/components/ConversationHeaderPanels.tsx'),
    path.join(sourceRoot, 'pages/Workspace/Chat/StandardConversationStageContainer.tsx'),
  ];
  const visited = new Set<string>();
  while (pending.length) {
    const file = pending.pop()!;
    if (visited.has(file)) continue;
    visited.add(file);
    const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
    const add = (specifier: string) => {
      const dependency = resolveSource(file, specifier);
      if (dependency) pending.push(dependency);
    };
    const walk = (node: ts.Node) => {
      if (ts.isImportDeclaration(node) && !node.importClause?.isTypeOnly) {
        add((node.moduleSpecifier as ts.StringLiteral).text);
      } else if (ts.isExportDeclaration(node) && !node.isTypeOnly && node.moduleSpecifier) {
        add((node.moduleSpecifier as ts.StringLiteral).text);
      } else if (ts.isCallExpression(node) && node.arguments.length === 1 && ts.isStringLiteral(node.arguments[0])) {
        if (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === 'require')) {
          add(node.arguments[0].text);
        }
      }
      ts.forEachChild(node, walk);
    };
    walk(source);
  }
  expect([...visited].filter((file) => file.includes('/TaskEscortDemo/'))).toEqual([]);
  expect(visited.has(path.join(sourceRoot, 'hooks/useWorkflowList.ts'))).toBe(true);
  expect(visited.has(path.join(sourceRoot, 'services/tasks/taskService.ts'))).toBe(true);
});
