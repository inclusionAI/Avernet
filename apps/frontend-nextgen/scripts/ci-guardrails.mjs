#!/usr/bin/env node
/**
 * Public frontend structural checks.
 *
 * Keep this limited to source that is available in Avernet. Internal assembly,
 * private configuration, and internal-only tests remain checked by OCB.
 */
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const sourceRoot = path.join(root, 'src');
const excludedPaths = new Set(['src/.umi', 'src/.umi-production', 'src/pages/Demo', '.worktrees']);
const fileSizeLimits = {
  component: 300,
  hook: 250,
  store: 150,
  service: 1000,
  config: 300,
};
const importBoundaryRules = [
  ['src/pages/', ['src/services/backendApi/']],
  ['src/components/', ['src/services/backendApi/'], ['src/components/ui/']],
  ['src/hooks/', ['src/services/backendApi/']],
  ['src/stores/', ['src/services/', 'src/pages/', 'src/components/', 'sonner']],
  ['src/services/backendApi/', ['src/hooks/', 'src/pages/', 'src/components/']],
];
const uiRules = [
  [/<button(?:\s|>)/g, '裸 <button>：请使用项目 Button 组件。'],
  [/<(?:button|a|img|input|select|textarea|svg|div|span|Button|IconButton)\b[^<>]*\btitle\s*=\s*(?:"|\{|')/g, 'title 属性不能替代 Tooltip：请使用 Tooltip 组件。'],
];

function toPosix(filePath) {
  return filePath.split(path.sep).join('/');
}

function relative(filePath) {
  return toPosix(path.relative(root, filePath));
}

function isExcluded(filePath) {
  const rel = relative(filePath);
  return [...excludedPaths].some((excluded) => rel === excluded || rel.startsWith(`${excluded}/`));
}

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const filePath = path.join(directory, entry.name);
    if (isExcluded(filePath)) return [];
    if (entry.isDirectory()) return walk(filePath);
    return ['.ts', '.tsx'].includes(path.extname(entry.name)) ? [filePath] : [];
  });
}

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length;
}

function classify(rel) {
  const base = path.basename(rel);
  if (rel.startsWith('src/stores/') || /Store\.(ts|tsx)$/.test(base)) return 'store';
  if (rel.startsWith('src/hooks/') || /^use[A-Z].*\.(ts|tsx)$/.test(base)) return 'hook';
  if ((rel.startsWith('src/pages/') || rel.startsWith('src/components/')) && rel.endsWith('.tsx')) return 'component';
  if (rel.startsWith('src/services/')) return 'service';
  if (rel.startsWith('config/') || rel.includes('/config/')) return 'config';
  return undefined;
}

function imports(text) {
  const patterns = [
    /import\s+(?:type\s+)?[\s\S]*?\s+from\s+['"]([^'"]+)['"]/g,
    /export\s+(?:type\s+)?[\s\S]*?\s+from\s+['"]([^'"]+)['"]/g,
    /import\s*['"]([^'"]+)['"]/g,
    /import\(\s*['"]([^'"]+)['"]\s*\)/g,
    /require\(\s*['"]([^'"]+)['"]\s*\)/g,
  ];
  return patterns.flatMap((pattern) => [...text.matchAll(pattern)].map((match) => ({ specifier: match[1], index: match.index })));
}

function normalizeSpecifier(filePath, specifier) {
  if (specifier.startsWith('@/')) return `src/${specifier.slice(2)}`;
  if (specifier.startsWith('./') || specifier.startsWith('../')) {
    return toPosix(path.relative(root, path.resolve(path.dirname(filePath), specifier)));
  }
  return specifier;
}

function matchesPrefix(value, prefix) {
  return value === prefix.replace(/\/$/, '') || value.startsWith(prefix);
}

function isBusinessComponent(rel) {
  return rel.endsWith('.tsx') && (rel.startsWith('src/pages/') || rel.startsWith('src/components/')) && !rel.startsWith('src/components/ui/');
}

function report(issues, code, filePath, message, index) {
  const location = index === undefined ? relative(filePath) : `${relative(filePath)}:${lineOf(fs.readFileSync(filePath, 'utf8'), index)}`;
  issues.push(`${code} ${location} — ${message}`);
}

const issues = [];
for (const filePath of walk(sourceRoot)) {
  const rel = relative(filePath);
  const text = fs.readFileSync(filePath, 'utf8');
  const type = classify(rel);
  if (type) {
    const lines = text ? text.replace(/\n$/, '').split('\n').length : 0;
    const limit = fileSizeLimits[type];
    if (lines > limit) report(issues, 'TC-G005', filePath, `${type} 文件为 ${lines} 行，超过 ${limit} 行阈值。`);
  }

  const rules = importBoundaryRules.filter(([source, , excluded = []]) => matchesPrefix(rel, source) && !excluded.some((prefix) => matchesPrefix(rel, prefix)));
  for (const item of imports(text)) {
    const normalized = normalizeSpecifier(filePath, item.specifier);
    for (const [, forbidden] of rules) {
      if (forbidden.some((prefix) => matchesPrefix(normalized, prefix))) {
        report(issues, 'TC-G002', filePath, `分层越界 import：${item.specifier}。调用必须遵循 Component → Hook → Service → API Controller。`, item.index);
      }
    }
  }

  if (isBusinessComponent(rel)) {
    for (const [pattern, message] of uiRules) {
      for (const match of text.matchAll(pattern)) report(issues, 'TC-G006', filePath, message, match.index);
    }
  }
}

if (issues.length > 0) {
  console.error(`❌ 前端公开工程门禁未通过，共 ${issues.length} 项失败\n`);
  issues.forEach((issue) => console.error(issue));
  process.exit(1);
}

console.log('✅ 前端公开工程门禁通过');
