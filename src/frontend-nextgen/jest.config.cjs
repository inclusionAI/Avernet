// Jest 配置。测试默认 node 环境；需要 DOM 的用例在文件顶部加 `/** @jest-environment jsdom */`。
const transform = {
  '^.+\\.(ts|tsx|js|jsx)$': [
    'babel-jest',
    { presets: ['@umijs/babel-preset-umi'], plugins: ['@babel/plugin-transform-modules-commonjs'] },
  ],
};

const moduleNameMapper = {
  '^@/(.*)$': '<rootDir>/src/$1',
  '\\.(css|less|scss|sass)$': '<rootDir>/test/mocks/styleMock.js',
  '^@alipay/tc-chat-extensions': '<rootDir>/test/mocks/tc-chat-extensions.js',
  // @tc-chat/ui 的 es/ 是未转译 ESM，直接 import 会 SyntaxError。该包同时发了 CJS 的 lib/
  // （package.json main），故测试环境重定向到 lib/ 用真实实现：既省掉全量转译整个 UI 库的开销，
  // 也绕开 es/ 里的 .css/.less 副作用导入（lib/ 无此问题）。
  '^@tc-chat/ui/es/(.*)$': '<rootDir>/node_modules/@tc-chat/ui/lib/$1',
  // @tc-chat/* 的 dist 引用了 @babel/runtime 的 esm helpers（同样是未转译 ESM）。
  // @babel/runtime 在 helpers/ 下发了等价 CJS 版本，重定向过去即可，无需再转译一遍 runtime。
  '^@babel/runtime/helpers/esm/(.*)$': '<rootDir>/node_modules/@babel/runtime/helpers/$1',
  '^react-syntax-highlighter/dist/esm/styles/prism$':
    '<rootDir>/node_modules/react-syntax-highlighter/dist/cjs/styles/prism/index.js',
};

// @tc-chat/{adapters,core,utils} 只发了 ESM dist（main===module===dist/index.js，无 CJS 产物），
// 无法像 ui 那样重定向，只能放开默认的 node_modules 忽略让 babel 转译它们。
// 这三个包的 dist 不含 .css/.less 导入，转译后可直接在 node 环境跑。
// tc-chat/ui's CJS MarkdownRenderer still imports @ant-design/colors/es from a
// nested dependency. Transform that small ESM package as well; the rest of
// node_modules stays ignored.
const transformIgnorePatterns = ['/node_modules/(?!@tc-chat/|@ant-design/colors/)', '\\.pnp\\.[^\\\\/]+$'];

const testMatch = ['<rootDir>/test/**/*.(test|spec|e2e).(ts|tsx|js|jsx)'];

module.exports = {
  testEnvironment: 'node',
  testEnvironmentOptions: {},
  testMatch,
  transform,
  moduleNameMapper,
  transformIgnorePatterns,
  // 标记 `@jest-environment` docblock生效；test 目录中带 `/** @jest-environment jsdom */` 的测试自动使用 jsdom
  projects: [
    {
      displayName: 'node',
      testEnvironment: 'node',
      testMatch,
      transform,
      moduleNameMapper,
      transformIgnorePatterns,
    },
  ],
};
