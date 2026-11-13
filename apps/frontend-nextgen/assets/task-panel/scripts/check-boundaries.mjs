import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
const root = fileURLToPath(new URL('..', import.meta.url));
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const declared = new Set(Object.keys({ ...pkg.dependencies, ...pkg.devDependencies, ...pkg.peerDependencies }));
const errors = [];
function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (['node_modules', 'dist', '.git'].includes(entry.name)) return [];
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(file) : /\.(?:[cm]?[jt]sx?)$/.test(file) ? [file] : [];
  });
}
for (const file of walk(root)) {
  const text = fs.readFileSync(file, 'utf8');
  if (text.split('\n').length > 1000) errors.push(`${file}: exceeds 1000 lines`);
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const check = specifier => {
    if (specifier.startsWith('.')) {
      const target = path.resolve(path.dirname(file), specifier);
      const relative = path.relative(root, target);
      if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative))
        errors.push(`${file}: import escapes independent task package: ${specifier}`);
    } else if (/^(?:@\/|@tc-chat(?:\/|$)|@aix-chat(?:\/|$)|@alipay(?:\/|$))/.test(specifier)) {
      errors.push(`${file}: host/SDK dependency: ${specifier}`);
    } else if (!specifier.startsWith('node:')) {
      const name = specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0];
      if (!declared.has(name)) errors.push(`${file}: undeclared package: ${specifier}`);
    }
  };
  const visit = node => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier))
      check(node.moduleSpecifier.text);
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
      (ts.isIdentifier(node.expression) && node.expression.text === 'require'))) {
      const arg = node.arguments[0];
      if (arg && ts.isStringLiteral(arg)) check(arg.text);
      else errors.push(`${file}: nonliteral module import cannot be boundary-checked`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}
for (const name of declared) {
  if (/^@(?:alipay|tc-chat|aix-chat)\//.test(name)) errors.push(`private/host dependency: ${name}`);
}
if (errors.length) throw new Error(errors.join('\n'));
console.log('PASS: independent task package imports, declared dependencies and file sizes');
