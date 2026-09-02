import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = process.cwd();
const targets = ['src', 'assets', 'README.md', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json', 'dist/index.umd.js'];
const deniedPatterns = [/alipay/i, /antgroup/i, /tnpm/i, /yuyan/i, /hitu/i, /code\.alipay/i, /registry\.antgroup/i, /Bearer\b/, /Authorization\b/, /document\.cookie/];
const ignoredDirs = new Set(['node_modules', 'dist']);
function collectFiles(path) {
  const stat = statSync(path);
  if (stat.isFile()) return [path];
  if (!stat.isDirectory()) return [];
  return readdirSync(path).flatMap((entry) => ignoredDirs.has(entry) ? [] : collectFiles(join(path, entry)));
}
const findings = [];
for (const target of targets) {
  const targetPath = join(root, target);
  if (!existsSync(targetPath)) continue;
  for (const file of collectFiles(targetPath)) {
    const text = readFileSync(file, 'utf8');
    const normalized = relative(root, file) === 'package.json' ? JSON.stringify({ ...JSON.parse(text), scripts: undefined }, null, 2) : text;
    for (const pattern of deniedPatterns) if (pattern.test(normalized)) findings.push(`${relative(root, file)}: ${pattern}`);
  }
}
if (findings.length) {
  console.error('Public scan failed:');
  findings.forEach((finding) => console.error(`- ${finding}`));
  process.exit(1);
}
console.log('Public scan passed.');
