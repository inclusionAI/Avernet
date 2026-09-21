// react-markdown ESM 包在 Jest CommonJS 环境里的桩：
// 将传入的 markdown 子内容原样渲染，保留 getByText 等测试查询能力。
const React = require('react');

function ReactMarkdown({ children }) {
  if (typeof children !== 'string') {
    return React.createElement('div', { className: 'react-markdown-mock' }, children);
  }
  // 简单按行切分：# 开头渲染为 h1，- 开头渲染为 li，其余为 p。
  // 足以满足现有测试对 getByRole('heading') 和 getByText 的断言，且不引入真实 ESM 依赖。
  const lines = children.split('\n');
  const elements = [];
  let key = 0;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith('# ')) {
      elements.push(React.createElement('h1', { key: key++ }, line.slice(2)));
    } else if (line.startsWith('- ')) {
      elements.push(React.createElement('li', { key: key++ }, line.slice(2)));
    } else {
      elements.push(React.createElement('p', { key: key++ }, line));
    }
  }
  return React.createElement('div', { className: 'react-markdown-mock' }, elements);
}

module.exports = ReactMarkdown;
