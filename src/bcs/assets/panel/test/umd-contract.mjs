import { readFileSync } from 'node:fs';
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

console.log('UMD contract passed.');
