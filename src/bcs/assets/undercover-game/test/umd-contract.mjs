import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import vm from 'node:vm';

const reactModule = await import('react');
const React = reactModule.default ?? reactModule;
const distPath = resolve('dist/index.umd.js');
const code = readFileSync(distPath, 'utf8');
const module = { exports: {} };
const context = {
  module,
  exports: module.exports,
  React,
  ReactDOM: {},
  require(name) {
    if (name === 'react') return React;
    throw new Error(`Unexpected UMD dependency: ${name}`);
  },
  console,
};

try {
  vm.runInNewContext(code, context, { filename: 'dist/index.umd.js', timeout: 2000 });
} catch (error) {
  console.error('UMD contract failed: bundle cannot eval with host React globals.');
  console.error(error);
  process.exit(1);
}

if (typeof module.exports.UndercoverGamePanel !== 'function') {
  console.error('UMD contract failed: UndercoverGamePanel export is missing.');
  process.exit(1);
}

const require = createRequire(import.meta.url);
const packageExports = require('..');
if (typeof packageExports.UndercoverGamePanel !== 'function') {
  console.error('UMD contract failed: package main does not expose UndercoverGamePanel.');
  process.exit(1);
}
console.log('UMD contract passed: undercoverGame.UndercoverGamePanel is available.');
