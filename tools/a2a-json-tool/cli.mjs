#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { convertInput } from './web/convert-input.js';

const usage = '用法：a2a card|message [文件|-]';
const help = `${usage}

模式：
  message  将业务 JSON 或消息片段转换为 A2A SendMessage 请求
  card     将 Agent 配置转换为 A2A 1.0 AgentCard

输入：完整 JSON、JSON 字段片段或 Message(...) 对象打印文本。
省略文件或传入 - 时读取标准输入；相对路径以当前工作目录为准。
转换结果写入 stdout，说明和错误写入 stderr；失败时非零退出。

示例：
  a2a message input.json > request.json
  a2a card agent.json > agent-card.json
  echo '{"question":"你好"}' | a2a message

选项：
  -h, --help  显示帮助

也可使用 node cli.mjs 执行。无需启动网页服务，不调用大模型或远端 Agent。
`;

try {
  const args = process.argv.slice(2);
  const [mode, filename = '-', ...extra] = args;
  const isHelp = value => ['--help', '-h'].includes(value);
  if ((args.length === 1 && isHelp(mode)) || (args.length === 2 && ['card', 'message'].includes(mode) && isHelp(filename))) {
    process.stdout.write(help);
  } else {
    if (!['card', 'message'].includes(mode) || extra.length) {
      throw new Error(`${usage}；运行 a2a --help 查看说明。`);
    }
    const { output, notes } = convertInput(readFileSync(filename === '-' ? 0 : filename, 'utf8'), mode);
    process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
    for (const note of notes) process.stderr.write(`提示：${note}\n`);
  }
} catch (error) {
  const messages = Array.isArray(error.issues) ? error.issues : [error.message];
  for (const message of messages) process.stderr.write(`错误：${message}\n`);
  process.exitCode = 1;
}
