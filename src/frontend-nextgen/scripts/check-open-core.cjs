#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.resolve(process.argv[2] || '.');
const forbiddenPaths = ['.internal-paths', 'src/internal', 'src/extensions/internal.ts', 'config/internal', 'config/routes.internal.ts'];
const errors = forbiddenPaths.filter((file) => fs.existsSync(path.join(root, file))).map((file) => `${file}: forbidden path`);
const packageFiles = ['package.json', 'package-lock.json'];
for (const file of packageFiles) {
  const absolute = path.join(root, file);
  if (!fs.existsSync(absolute)) continue;
  const text = fs.readFileSync(absolute, 'utf8');
  for (const [name, pattern] of [
    ['internal registry', /registry\.antgroup-inc\.cn/],
    ['internal package alias', /npm:@alipay\//],
    ['Bigfish dependency', /@alipay\/bigfish/],
  ]) if (pattern.test(text)) errors.push(`${file}: ${name}`);
}
if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log('Open Core artifact checks passed');
