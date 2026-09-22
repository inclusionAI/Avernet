// html-react-parser ESM 包在 Jest CommonJS 环境里的桩：返回原始 HTML 文本节点，避免转译整个 ESM 依赖树。
const React = require('react');

module.exports = function parse(html) {
  return React.createElement('div', {
    className: 'html-react-parser-mock',
    dangerouslySetInnerHTML: { __html: html || '' },
  });
};
