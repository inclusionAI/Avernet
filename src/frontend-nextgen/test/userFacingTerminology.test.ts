import { readFileSync } from 'node:fs';
import path from 'node:path';

it('用户可见文案不直接透出 Human input 技术概念', () => {
  const source = readFileSync(path.join(process.cwd(), 'src/assets/BcsWorkflowPanel/StateMachineRunView.tsx'), 'utf8');

  expect(source).not.toContain('检测到多个待处理的 Human input');
  expect(source).not.toContain('待处理的 Human input 与当前运行节点不一致');
  expect(source).not.toContain("normalizedError.message || '加载 Human input 信息失败'");
  expect(source).not.toContain("normalizedError.message || '提交 Human input 失败'");
  expect(source).toContain('检测到多个待处理的用户输入，当前版本不支持并发用户输入。');
  expect(source).toContain('待处理的用户输入与当前运行节点不一致，请刷新后重试。');
  expect(source).toContain("normalizedError.message || '加载用户输入信息失败'");
  expect(source).toContain("normalizedError.message || '提交用户输入失败'");
});
