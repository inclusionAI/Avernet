import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = process.cwd();
const targets = [
  'src',
  'README.md',
  'package.json',
  'package-lock.json',
  'vite.config.ts',
  'tsconfig.json',
  'tsconfig.build.json',
];
const deniedPatterns = [
  /alipay/i,
  /antgroup/i,
  /tnpm/i,
  /yuyan/i,
  /hitu/i,
  /code\.alipay/i,
  /registry\.antgroup/i,
  /Bearer\b/,
  /Authorization\b/,
  /document\.cookie/,
];
const ignoredDirs = new Set(['node_modules', 'dist']);

function collectFiles(path) {
  const stat = statSync(path);

  if (stat.isFile()) {
    return [path];
  }

  if (!stat.isDirectory()) {
    return [];
  }

  return readdirSync(path).flatMap((entry) => {
    if (ignoredDirs.has(entry)) {
      return [];
    }

    return collectFiles(join(path, entry));
  });
}

const findings = [];

for (const target of targets) {
  const targetPath = join(root, target);

  if (!existsSync(targetPath)) {
    continue;
  }

  const files = collectFiles(targetPath);

  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    const normalizedText =
      relative(root, file) === 'package.json'
        ? JSON.stringify(
            {
              ...JSON.parse(text),
              scripts: undefined,
            },
            null,
            2,
          )
        : text;

    deniedPatterns.forEach((pattern) => {
      if (pattern.test(normalizedText)) {
        findings.push(`${relative(root, file)}: ${pattern}`);
      }
    });
  }
}

if (findings.length > 0) {
  console.error('Public scan failed:');
  findings.forEach((finding) => console.error(`- ${finding}`));
  process.exit(1);
}

console.log('Public scan passed.');
