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
  require(name) {
    if (name === 'react') {
      return React;
    }

    throw new Error(`Unexpected UMD dependency: ${name}`);
  },
  console,
};

try {
  vm.runInNewContext(code, context, {
    filename: 'dist/index.umd.js',
    timeout: 1000,
  });
} catch (error) {
  console.error('UMD contract failed: bundle cannot eval without Node globals.');
  console.error(error);
  process.exit(1);
}

if (typeof module.exports.StateMachineRunView !== 'function') {
  console.error('UMD contract failed: StateMachineRunView export is missing.');
  process.exit(1);
}

const require = createRequire(import.meta.url);
const packageExports = require('..');

if (typeof packageExports.StateMachineRunView !== 'function') {
  console.error('UMD contract failed: package main does not expose StateMachineRunView.');
  process.exit(1);
}

// The browser loader resolves both component names from the same global.
const browser = { React, console };
vm.runInNewContext(code, browser, { timeout: 1000 });
for (const name of ['StateMachineRunView', 'UndercoverGamePanel']) {
  for (const exports of [module.exports, packageExports, browser.bcsPanel]) {
    if (typeof exports?.[name] !== 'function') {
      throw new Error(`UMD contract failed: bcsPanel.${name} is missing.`);
    }
  }
}
console.log('UMD contract passed: both bcsPanel components are available.');
