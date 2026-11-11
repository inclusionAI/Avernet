import { readFileSync } from 'node:fs';
import { convertInput } from './web/convert-input.js';

try {
  const [mode, filename = '-', ...extra] = process.argv.slice(2);
  if (!['card', 'message'].includes(mode) || extra.length) {
    throw new Error('用法：node cli.mjs card|message [JSON 文件|-]；省略文件时读取标准输入。');
  }
  const { output, notes } = convertInput(readFileSync(filename === '-' ? 0 : filename, 'utf8'), mode);
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  for (const note of notes) process.stderr.write(`提示：${note}\n`);
} catch (error) {
  const messages = Array.isArray(error.issues) ? error.issues : [error.message];
  for (const message of messages) process.stderr.write(`错误：${message}\n`);
  process.exitCode = 1;
}
