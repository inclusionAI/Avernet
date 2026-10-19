#!/usr/bin/env node
// Public counterpart of OCB's deployment package-boundary check; no checkout or dependencies needed.
import { existsSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';

function walk(root, visit) {
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (['node_modules', 'dist', 'dist-server'].includes(entry.name)) continue;
    const path = join(root, entry.name);
    if (entry.isDirectory()) walk(path, visit);
    else visit(path);
  }
}

function realpathAllowMissing(input) {
  let existing = resolve(input);
  const suffix = [];
  while (!existsSync(existing)) {
    const parent = dirname(existing);
    if (parent === existing) break;
    suffix.unshift(basename(existing));
    existing = parent;
  }
  return resolve(realpathSync(existing), ...suffix);
}

function inside(root, target) {
  const rel = relative(root, target);
  return rel === '' || (!rel.startsWith(`..${sep}`) && rel !== '..' && !isAbsolute(rel));
}

export function verifyPackageBoundaries(packages) {
  if (!packages.length) throw new Error('No public ClawWeb packages found');
  const names = new Set();
  for (const pkg of packages) {
    const root = realpathSync(pkg.root);
    const manifest = join(root, 'package.json');
    const metadata = JSON.parse(readFileSync(manifest, 'utf8'));
    if (!metadata.name || names.has(metadata.name)) throw new Error(`Missing/duplicate package name: ${manifest}`);
    names.add(metadata.name);
    walk(root, (sourcePath) => {
      if (!/\.(?:ts|tsx|js|jsx|mjs|cjs)$/.test(sourcePath)) return;
      const source = readFileSync(sourcePath, 'utf8');
      // Keep import/from/import() checks aligned with the deployment gate.
      // Also cover literal require and test mock/actual imports: tests have no boundary exemption.
      const imports = /(?:\bfrom\s*|\bimport\s*\(|\bimport\s+|\b(?:require|(?:vi|jest)\.(?:mock|doMock|importActual|importMock))\s*\()\s*["']([^"']+)["']/g;
      for (const [, specifier] of source.matchAll(imports)) {
        if (specifier.startsWith('@ocb/')) throw new Error(`Public package imports OCB: ${sourcePath}`);
        if (!specifier.startsWith('./') && !specifier.startsWith('../')) continue;
        const target = realpathAllowMissing(resolve(dirname(sourcePath), specifier));
        if (!inside(root, target)) {
          throw new Error(`relative import escapes package: ${sourcePath} -> ${specifier}`);
        }
      }
    });
  }
  return names.size;
}
